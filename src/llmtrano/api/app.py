"""FastAPI app.

Run with ``uvicorn llmtrano.api.app:app``. On start it applies Alembic migrations (idempotent), then opens the
database, the Valkey pool, the LLM client and the workflow's Postgres checkpointer once, and keeps them in
``app.state.services``. Session endpoints are in ``llmtrano.api.sessions``.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

import yaml
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from llmtrano.api.sessions import Graph
from llmtrano.api.sessions import router as sessions_router
from llmtrano.db.migrate import upgrade_head
from llmtrano.db.models import Run, RunStatus
from llmtrano.db.recorder import DbRecorder
from llmtrano.db.session import make_sessionmaker
from llmtrano.jobs.progress import TERMINAL_PHASES, channel
from llmtrano.jobs.queue import SIMULATION_JOB, WorkerJobs
from llmtrano.llm.client import make_llm_client
from llmtrano.logs import configure_logging
from llmtrano.settings import Settings
from llmtrano.workflow.checkpoint import postgres_checkpointer
from llmtrano.workflow.deps import Deps
from llmtrano.workflow.graph import build_graph

log = logging.getLogger(__name__)


class Queue(Protocol):
    """The part of arq's ``ArqRedis`` the api uses."""

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None) -> Any: ...

    def ping(self) -> Awaitable[Any]: ...

    async def aclose(self) -> None: ...


@dataclass
class Services:
    sessions: sessionmaker[Session]
    queue: Queue
    llm: AsyncOpenAI
    graph: Graph
    valkey_url: str


# ---------- request and response bodies


class RunRequest(BaseModel):
    yaml_text: str = Field(min_length=1)
    library: str = "Buildings"
    end_time_s: float = Field(gt=0, le=366 * 86_400)


class RunCreated(BaseModel):
    id: uuid.UUID


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    status: RunStatus
    kind: str
    session_id: uuid.UUID | None
    phase: str | None
    errors: list[str] | None
    library: str
    start_s: float
    end_s: float
    result_path: str | None
    duration_s: float | None
    error_kind: str | None
    error_message: str | None
    log_tail: str | None
    trano_version: str | None


class Health(BaseModel):
    status: Literal["ok", "degraded", "down"]
    db: str
    queue: str
    llm: str


# ---------- app


async def _open_services(settings: Settings, stack: AsyncExitStack) -> Services:
    await asyncio.to_thread(upgrade_head, settings.database_url)
    queue: ArqRedis = await create_pool(RedisSettings.from_dsn(settings.valkey_url))
    stack.push_async_callback(queue.aclose)
    llm = make_llm_client(settings)
    stack.push_async_callback(llm.close)
    sessions = make_sessionmaker(settings.database_url)
    checkpointer = await stack.enter_async_context(postgres_checkpointer(settings.database_url))
    deps = Deps(
        llm=llm, jobs=WorkerJobs(queue, sessions, settings), recorder=DbRecorder(sessions), settings=settings
    )
    return Services(
        sessions=sessions,
        queue=queue,
        llm=llm,
        graph=build_graph(deps, checkpointer),
        valkey_url=settings.valkey_url,
    )


def create_app(services: Services | None = None) -> FastAPI:
    """The app; pass ``services`` (tests) to skip migrations and real connections."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.session_locks = defaultdict(asyncio.Lock)
        if services is not None:
            app.state.services = services
            yield
            return
        configure_logging()  # uvicorn configures only its own loggers
        async with AsyncExitStack() as stack:
            app.state.services = await _open_services(Settings(), stack)
            log.info("api ready")
            yield

    app = FastAPI(title="llmtrano", version="0.1.0", lifespan=lifespan)
    app.add_api_route("/health", health, methods=["GET"], response_model=Health)
    app.add_api_route("/models", models, methods=["GET"])
    app.add_api_route("/runs", create_run, methods=["POST"], status_code=status.HTTP_201_CREATED)
    app.add_api_route("/runs/{run_id}", get_run, methods=["GET"])
    app.add_api_route(
        "/runs/{run_id}/events", run_events, methods=["GET"], response_class=EventSourceResponse
    )
    app.include_router(sessions_router)
    return app


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def _probe(check: Callable[[], Awaitable[object]]) -> str:
    try:
        await check()
    except Exception as error:
        return f"error: {type(error).__name__}: {error}"[:300]
    return "ok"


async def health(request: Request, response: Response) -> Health:
    """Each dependency's status. The LLM is optional (it only runs with the `gpu` profile): without it the
    api is `degraded` but still healthy, so `docker compose up -d` works for ui and simulation work."""
    services = _services(request)

    def select_one() -> None:
        with services.sessions() as session:
            session.execute(text("SELECT 1"))

    async def db_check() -> None:
        await asyncio.to_thread(select_one)

    async def queue_check() -> None:
        await services.queue.ping()

    async def llm_check() -> None:
        await services.llm.models.list()

    db, queue, llm = await asyncio.gather(_probe(db_check), _probe(queue_check), _probe(llm_check))
    if db != "ok" or queue != "ok":
        overall: Literal["ok", "degraded", "down"] = "down"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall = "ok" if llm == "ok" else "degraded"
    return Health(status=overall, db=db, queue=queue, llm=llm)


async def models(request: Request) -> list[str]:
    try:
        page = await _services(request).llm.models.list()
    except Exception as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"LLM unavailable: {error}") from error
    return [model.id for model in page.data]


async def create_run(body: RunRequest, request: Request) -> RunCreated:
    """Store the run and queue it for the worker; returns at once with the run id."""
    try:
        parsed = yaml.safe_load(body.yaml_text)
    except yaml.YAMLError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"yaml_text is not valid YAML: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "yaml_text must be a YAML mapping")

    services = _services(request)

    def insert() -> uuid.UUID:
        with services.sessions() as session:
            run = Run(library=body.library, start_s=0.0, end_s=body.end_time_s, yaml=body.yaml_text)
            session.add(run)
            session.commit()
            return run.id

    run_id = await asyncio.to_thread(insert)
    await services.queue.enqueue_job(SIMULATION_JOB, str(run_id), _job_id=str(run_id))
    log.info("run queued run_id=%s library=%s end_s=%s", run_id, body.library, body.end_time_s)
    return RunCreated(id=run_id)


async def get_run(run_id: uuid.UUID, request: Request) -> RunOut:
    services = _services(request)

    def load() -> Run | None:
        with services.sessions() as session:
            return session.get(Run, run_id)

    run = await asyncio.to_thread(load)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return RunOut.model_validate(run)


def _load_run(services: Services, run_id: uuid.UUID) -> Run | None:
    with services.sessions() as session:
        return session.get(Run, run_id)


async def existing_run(run_id: uuid.UUID, request: Request) -> Run:
    """Dependency: 404 before the stream starts."""
    run = await asyncio.to_thread(_load_run, _services(request), run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return run


async def run_events(
    run: Annotated[Run, Depends(existing_run)], request: Request
) -> AsyncIterator[ServerSentEvent]:
    """The run's progress as SSE ``run_phase`` events, relayed from Valkey ``run:<id>`` until it ends.

    It subscribes first, then sends the row's current state, so no phase is lost between the two.
    """
    if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):  # finished: the row says it all
        yield ServerSentEvent(
            event="run_phase", data={"run_id": str(run.id), "phase": run.status.value, "t": None}
        )
        return
    services = _services(request)
    client = Redis.from_url(services.valkey_url)
    try:
        async with client.pubsub() as pubsub:
            await pubsub.subscribe(channel(run.id))
            current = await asyncio.to_thread(_load_run, services, run.id)
            assert current is not None
            terminal = current.status in (RunStatus.SUCCEEDED, RunStatus.FAILED)
            phase = current.status.value if terminal else current.phase or current.status.value
            yield ServerSentEvent(event="run_phase", data={"run_id": str(run.id), "phase": phase, "t": None})
            if terminal:
                return
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = json.loads(message["data"])
                yield ServerSentEvent(event="run_phase", data=data)
                if data["phase"] in TERMINAL_PHASES:
                    return
    finally:
        await client.aclose()


app = create_app()
