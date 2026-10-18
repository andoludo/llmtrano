"""Enqueueing jobs. Kept apart from the worker so the api does not import trano's simulation code."""

import uuid

from arq.connections import ArqRedis

SIMULATION_JOB = "run_simulation"


async def enqueue_simulation(redis: ArqRedis, run_id: uuid.UUID) -> None:
    """Queue a simulation; the run id is the job id, so a run is never queued twice."""
    await redis.enqueue_job(SIMULATION_JOB, str(run_id), _job_id=str(run_id))
