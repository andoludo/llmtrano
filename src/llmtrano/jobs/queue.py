"""Enqueueing and awaiting jobs. Kept apart from the worker so the api does not import trano's simulation
code.

``WorkerJobs`` is the workflow's ``Jobs``: it inserts the ``runs`` row, queues the arq job (the run id is the
job id, so a run is never queued twice), relays the run's progress from Valkey and waits for the end of the
job.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from arq.connections import ArqRedis
from arq.jobs import Job
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from llmtrano.compiler.compile import CompiledModel
from llmtrano.db.models import KpiRow, Run, RunKind, RunStatus
from llmtrano.jobs.progress import TERMINAL_PHASES, channel
from llmtrano.settings import Settings
from llmtrano.workflow.deps import CheckOutcome, PhaseCallback, SimulationOutcome

log = logging.getLogger(__name__)

SIMULATION_JOB = "run_simulation"
CHECK_JOB = "check_model"
YEAR_S = 365 * 86_400
# Waiting longer than the worker's own job timeout: the worker always ends a job first.
WAIT_MARGIN_S = 180


async def enqueue_simulation(redis: ArqRedis, run_id: uuid.UUID) -> Job | None:
    """Queue a simulation; the run id is the job id, so a run is never queued twice."""
    return await redis.enqueue_job(SIMULATION_JOB, str(run_id), _job_id=str(run_id))


async def enqueue_check(redis: ArqRedis, run_id: uuid.UUID) -> Job | None:
    return await redis.enqueue_job(CHECK_JOB, str(run_id), _job_id=str(run_id))


class WorkerJobs:
    """The workflow's jobs on the real queue and database."""

    def __init__(self, redis: ArqRedis, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.redis, self.sessions, self.settings = redis, sessions, settings

    def _insert(self, session_id: str, compiled: CompiledModel, kind: RunKind) -> uuid.UUID:
        with self.sessions() as session:
            run = Run(
                session_id=uuid.UUID(session_id),
                kind=kind,
                library=self.settings.simulation_library,
                start_s=0.0,
                end_s=YEAR_S,
                yaml=compiled.yaml_text,
            )
            session.add(run)
            session.commit()
            return run.id

    def _load(self, run_id: uuid.UUID) -> tuple[Run, dict[str, Any] | None]:
        with self.sessions() as session:
            run = session.get_one(Run, run_id)
            kpis = session.scalar(select(KpiRow.values).where(KpiRow.run_id == run_id))
            return run, kpis

    async def _wait(self, job: Job | None, run_id: uuid.UUID) -> None:
        timeout = self.settings.simulation_timeout_s + WAIT_MARGIN_S
        if job is None:  # already queued with this id: wait on the existing job
            job = Job(str(run_id), self.redis)
        try:
            await job.result(timeout=timeout, poll_delay=1.0)
        except Exception as error:  # the job failed or timed out; the row says why
            log.warning("job ended with an error run_id=%s error=%r", run_id, error)

    async def check_model(self, session_id: str, compiled: CompiledModel) -> CheckOutcome:
        run_id = await asyncio.to_thread(self._insert, session_id, compiled, RunKind.CHECK)
        await self._wait(await enqueue_check(self.redis, run_id), run_id)
        run, _ = await asyncio.to_thread(self._load, run_id)
        return CheckOutcome(
            run_id=run_id,
            ok=run.status is RunStatus.SUCCEEDED,
            errors=run.errors or ([run.error_message] if run.error_message else []),
            error_kind=run.error_kind,
        )

    async def simulate(
        self, session_id: str, compiled: CompiledModel, on_phase: PhaseCallback
    ) -> SimulationOutcome:
        run_id = await asyncio.to_thread(self._insert, session_id, compiled, RunKind.SIMULATE)
        relay = asyncio.create_task(relay_progress(self.settings.valkey_url, run_id, on_phase))
        await asyncio.sleep(0)  # subscribe before the job can start
        try:
            await self._wait(await enqueue_simulation(self.redis, run_id), run_id)
        finally:
            relay.cancel()
        run, kpis = await asyncio.to_thread(self._load, run_id)
        return SimulationOutcome(
            run_id=run_id,
            ok=run.status is RunStatus.SUCCEEDED and kpis is not None,
            kpis=kpis,
            error_kind=run.error_kind or (None if kpis is not None else "kpi"),
            error_message=run.error_message,
        )


async def relay_progress(valkey_url: str, run_id: uuid.UUID, on_phase: PhaseCallback) -> None:
    """Call ``on_phase`` for every phase published on ``run:<id>`` until a terminal phase."""
    client = Redis.from_url(valkey_url)
    try:
        async with client.pubsub() as pubsub:
            await pubsub.subscribe(channel(run_id))
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                phase = json.loads(message["data"])["phase"]
                await on_phase(phase)
                if phase in TERMINAL_PHASES:
                    return
    finally:
        await client.aclose()
