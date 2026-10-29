"""Session endpoints: the chat, the confirmation, the state for reloads and feedback.

- ``POST /sessions`` -> ``{"id"}``.
- ``POST /sessions/{id}/messages`` -> SSE: ``token`` while the reply streams, then ``brief`` (the card),
  ``questions`` (with their options) and ``phase``. During the confirmation, a message reopens the intake.
- ``POST /sessions/{id}/confirm`` with ``{approved, edits}`` -> SSE: ``run_phase`` and ``kpis`` (each with
  its ``scenario``: the baseline, then the renovation when planned), ``comparison``, ``token`` (the
  interpretation), ``guardrail`` (when it triggered), ``explanation``, then ``brief``, ``questions``,
  ``phase``.
- ``GET /sessions/{id}``: the state for a reload. ``POST /feedback``: thumbs up or down on a message.
- ``GET /sessions/{id}/export``: everything stored about a session (phase 3). ``DELETE /sessions/{id}``:
  erases it, its runs, its checkpoints, and asks the worker to remove its result files.

One lock per session: two requests of the same session never run the graph at the same time, and a token
bucket per session limits the turns per minute (``RATE_LIMIT_PER_MINUTE``).
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol, Self

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from llmtrano.brief.card import build_card
from llmtrano.db.models import FactRow, Feedback, HouseBriefRow, KpiRow, Message, Run
from llmtrano.db.models import Session as SessionRow
from llmtrano.jobs.retention import erase_session
from llmtrano.workflow.state import Answer, Phase, WorkflowState

log = logging.getLogger(__name__)
router = APIRouter()


class Graph(Protocol):
    """The parts of a compiled LangGraph graph the api uses."""

    def astream(self, input: Any, config: Any, *, stream_mode: Any) -> AsyncIterator[Any]: ...

    async def aget_state(self, config: Any) -> Any: ...


class SessionCreated(BaseModel):
    id: uuid.UUID


class MessageIn(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=4000)
    answer: Answer | None = None  # an answer button instead of text
    model: str | None = None  # the interactive model chosen in the ui

    @model_validator(mode="after")
    def _message_or_answer(self) -> Self:
        if (self.message is None) == (self.answer is None):
            raise ValueError("give either a message or an answer")
        return self


class ConfirmIn(BaseModel):
    approved: bool
    edits: dict[str, str] = Field(default_factory=dict)  # field -> Brief value, from the card


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    thumbs: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=2000)


class SessionOut(BaseModel):
    id: uuid.UUID
    phase: Phase
    card: dict[str, Any]
    questions: list[dict[str, Any]]
    messages: list[dict[str, str]]
    kpis: dict[str, Any] | None
    explanation: str | None
    run_id: uuid.UUID | None
    # phase 2: the renovation scenario and its comparison with the baseline, when simulated
    renovation_kpis: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    renovation_run_id: uuid.UUID | None = None


def _locks(request: Request) -> defaultdict[str, asyncio.Lock]:
    locks: defaultdict[str, asyncio.Lock] = request.app.state.session_locks
    return locks


def _graph(request: Request) -> Graph:
    graph: Graph = request.app.state.services.graph
    return graph


def _sessions(request: Request) -> sessionmaker[Session]:
    sessions: sessionmaker[Session] = request.app.state.services.sessions
    return sessions


def _config(session_id: uuid.UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": str(session_id)}}


async def existing_session(session_id: uuid.UUID, request: Request) -> uuid.UUID:
    """Dependency: 404 before any stream starts (an SSE response cannot change its status later)."""

    def exists() -> bool:
        with _sessions(request)() as session:
            return session.get(SessionRow, session_id) is not None

    if not await asyncio.to_thread(exists):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"session {session_id} not found")
    return session_id


async def waiting_session(
    session_id: Annotated[uuid.UUID, Depends(existing_session)], request: Request
) -> uuid.UUID:
    """Dependency: 409 unless the session waits at the confirmation."""
    _, waiting = await _state(_graph(request), session_id)
    if not waiting:
        raise HTTPException(status.HTTP_409_CONFLICT, "the session is not waiting for a confirmation")
    return session_id


async def within_limit(
    session_id: Annotated[uuid.UUID, Depends(existing_session)], request: Request
) -> uuid.UUID:
    """Dependency: 429 when the session's token bucket is empty."""
    if not request.app.state.limiter.allow(str(session_id)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many turns for this session; wait a minute"
        )
    return session_id


SessionId = Annotated[uuid.UUID, Depends(existing_session)]
LimitedSessionId = Annotated[uuid.UUID, Depends(within_limit)]
WaitingSessionId = Annotated[uuid.UUID, Depends(waiting_session)]


async def _state(graph: Graph, session_id: uuid.UUID) -> tuple[WorkflowState | None, bool]:
    """The session's state and whether it waits at the confirmation interrupt."""
    snapshot = await graph.aget_state(_config(session_id))
    if not snapshot.values:
        return None, False
    values = snapshot.values
    state = values if isinstance(values, WorkflowState) else WorkflowState.model_validate(values)
    return state, bool(snapshot.interrupts)


def _card(state: WorkflowState) -> dict[str, Any]:
    card = build_card(
        state.brief,
        state.provenance,
        state.conflicts,
        ready=state.phase is Phase.confirm,
        renovation_facts=state.renovation_facts,
    )
    return card.model_dump(mode="json")


def _closing_events(state: WorkflowState) -> list[ServerSentEvent]:
    return [
        ServerSentEvent(event="brief", data=_card(state)),
        ServerSentEvent(event="questions", data=[q.model_dump(mode="json") for q in state.pending_questions]),
        ServerSentEvent(event="phase", data={"phase": state.phase.value}),
    ]


async def _run(request: Request, session_id: uuid.UUID, graph_input: Any) -> AsyncIterator[ServerSentEvent]:
    """Run the graph for one request and relay its custom events as SSE, then the closing events."""
    graph = _graph(request)
    async with _locks(request)[str(session_id)]:
        try:
            async for mode, chunk in graph.astream(
                graph_input, _config(session_id), stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    data = dict(chunk)
                    yield ServerSentEvent(event=str(data.pop("event")), data=data)
        except Exception as error:
            log.exception("graph error session=%s", session_id)
            yield ServerSentEvent(event="error", data={"text": f"{type(error).__name__}: {error}"})
            return
        state, _ = await _state(graph, session_id)
    if state is not None:
        for event in _closing_events(state):
            yield event


async def create_session(request: Request) -> SessionCreated:
    def insert() -> uuid.UUID:
        with _sessions(request)() as session:
            row = SessionRow()
            session.add(row)
            session.commit()
            return row.id

    session_id = await asyncio.to_thread(insert)
    log.info("session created session=%s", session_id)
    return SessionCreated(id=session_id)


async def post_message(
    session_id: LimitedSessionId, body: MessageIn, request: Request
) -> AsyncIterator[ServerSentEvent]:
    _, waiting = await _state(_graph(request), session_id)
    if waiting:  # at the confirmation: a message or an answer reopens the intake
        if body.answer is not None:
            graph_input: Any = Command(
                resume={"approved": False, "edits": {body.answer.field: body.answer.value}}
            )
        else:
            graph_input = Command(resume={"approved": False, "message": body.message})
    else:
        graph_input = {"session_id": str(session_id), "message": body.message, "answer": body.answer}
        if body.model:
            graph_input["model"] = body.model
    async for event in _run(request, session_id, graph_input):
        yield event


async def confirm(
    session_id: WaitingSessionId, body: ConfirmIn, request: Request
) -> AsyncIterator[ServerSentEvent]:
    if not request.app.state.limiter.allow(str(session_id)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many turns for this session; wait a minute"
        )
    decision = {"approved": True} if body.approved else {"approved": False, "edits": body.edits}
    async for event in _run(request, session_id, Command(resume=decision)):
        yield event


async def get_session(session_id: SessionId, request: Request) -> SessionOut:
    state, _ = await _state(_graph(request), session_id)
    state = state or WorkflowState(session_id=str(session_id))
    return SessionOut(
        id=session_id,
        phase=state.phase,
        card=_card(state),
        questions=[q.model_dump(mode="json") for q in state.pending_questions],
        messages=[m.model_dump() for m in state.recent_messages],
        kpis=state.kpis,
        explanation=state.explanation,
        run_id=state.run_id,
        renovation_kpis=state.renovation_kpis,
        comparison=state.comparison,
        renovation_run_id=state.renovation_run_id,
    )


async def post_feedback(body: FeedbackIn, request: Request) -> dict[str, str]:
    def insert() -> bool:
        with _sessions(request)() as session:
            if session.get(Message, body.message_id) is None:
                return False
            session.add(Feedback(message_id=body.message_id, thumbs=body.thumbs, comment=body.comment))
            session.commit()
            return True

    if not await asyncio.to_thread(insert):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"message {body.message_id} not found")
    return {"status": "ok"}


def _rows(session: Session, model: type[Any], session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.scalars(select(model).where(model.session_id == session_id).order_by(model.created_at))
    return [{c.name: _plain(getattr(row, c.name)) for c in model.__table__.columns} for row in rows]


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


async def export_session(session_id: SessionId, request: Request) -> dict[str, Any]:
    """Everything stored about the session, for the user who asks for their data."""

    def load() -> dict[str, Any]:
        with _sessions(request)() as session:
            row = session.get_one(SessionRow, session_id)
            runs = _rows(session, Run, session_id)
            run_ids = [uuid.UUID(run["id"]) for run in runs]
            kpis = (
                [
                    {"run_id": str(k.run_id), "values": k.values}
                    for k in session.scalars(select(KpiRow).where(KpiRow.run_id.in_(run_ids)))
                ]
                if run_ids
                else []
            )
            message_ids = [uuid.UUID(m["id"]) for m in _rows(session, Message, session_id)]
            feedback = (
                [
                    {"message_id": str(f.message_id), "thumbs": f.thumbs, "comment": f.comment}
                    for f in session.scalars(select(Feedback).where(Feedback.message_id.in_(message_ids)))
                ]
                if message_ids
                else []
            )
            return {
                "session": {"id": str(row.id), "created_at": row.created_at.isoformat()},
                "messages": _rows(session, Message, session_id),
                "facts": _rows(session, FactRow, session_id),
                "house_briefs": _rows(session, HouseBriefRow, session_id),
                "runs": runs,
                "kpis": kpis,
                "feedback": feedback,
            }

    export = await asyncio.to_thread(load)
    state, _ = await _state(_graph(request), session_id)
    export["workflow_state"] = state.model_dump(mode="json") if state else None
    return export


async def delete_session(session_id: SessionId, request: Request) -> Response:
    """Erase the session: rows, checkpoints, and the worker removes the result files (right to erasure)."""
    services = request.app.state.services
    run_ids = await asyncio.to_thread(erase_session, _sessions(request), session_id)
    checkpointer = services.checkpointer
    if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
        await checkpointer.adelete_thread(str(session_id))
    if run_ids:
        await services.queue.enqueue_job("retention_sweep", _job_id=f"erase-{session_id}")
    request.app.state.limiter.forget(str(session_id))
    log.info("session deleted session=%s runs=%d", session_id, len(run_ids))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.add_api_route("/sessions", create_session, methods=["POST"], status_code=status.HTTP_201_CREATED)
router.add_api_route("/sessions/{session_id}/export", export_session, methods=["GET"])
router.add_api_route("/sessions/{session_id}", delete_session, methods=["DELETE"], status_code=204)
router.add_api_route(
    "/sessions/{session_id}/messages", post_message, methods=["POST"], response_class=EventSourceResponse
)
router.add_api_route(
    "/sessions/{session_id}/confirm", confirm, methods=["POST"], response_class=EventSourceResponse
)
router.add_api_route("/sessions/{session_id}", get_session, methods=["GET"], response_model=SessionOut)
router.add_api_route("/feedback", post_feedback, methods=["POST"], status_code=status.HTTP_201_CREATED)
