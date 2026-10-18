"""arq worker: runs `run_simulation` jobs with trano's local runtime (no Docker socket).

Start with ``arq llmtrano.jobs.worker.WorkerSettings`` (the worker image's CMD).
"""

import asyncio
import importlib.metadata
import json
import logging
import uuid
from pathlib import Path
from typing import Any, ClassVar

import yaml
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy.orm import Session, sessionmaker
from trano.api import simulate_yaml
from trano.simulate.simulate import SimulationOptions

from llmtrano.db.models import Run, RunStatus
from llmtrano.db.session import make_sessionmaker
from llmtrano.settings import Settings

log = logging.getLogger(__name__)

JOB_NAME = "run_simulation"
LOG_TAIL_CHARS = 5_000
OUTPUT_STEP_S = 3600  # one stored output point per simulated hour
# Margin over trano's own timeout, so trano stops omc and reports `timeout` before arq cancels the job.
JOB_TIMEOUT_MARGIN_S = 60


def number_of_intervals(start_s: float, end_s: float) -> int:
    return max(1, round((end_s - start_s) / OUTPUT_STEP_S))


def modelica_package_name(run_id: uuid.UUID) -> str:
    """A valid, per-run Modelica package name; the result file is ``<name>.building_res.mat``."""
    return f"run_{run_id.hex[:8]}"


def trano_version() -> str:
    """trano's version plus the commit it was installed from, e.g. ``0.52.0+4220d3c``."""
    distribution = importlib.metadata.distribution("trano")
    direct_url = distribution.read_text("direct_url.json")
    commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id", "") if direct_url else ""
    return f"{distribution.version}+{commit[:7]}" if commit else distribution.version


def execute_run(run_id: uuid.UUID, sessions: sessionmaker[Session], settings: Settings) -> RunStatus:
    """Simulate the `runs` row `run_id` and write the outcome back. Blocking; called in a thread."""
    with sessions() as session:
        run = session.get_one(Run, run_id)
        run.status = RunStatus.RUNNING
        run.trano_version = trano_version()
        session.commit()
        model = yaml.safe_load(run.yaml)
        library, start_s, end_s = run.library, run.start_s, run.end_s

    project_path = Path(settings.runs_dir) / str(run_id)
    log.info("run start run_id=%s library=%s start_s=%s end_s=%s", run_id, library, start_s, end_s)
    try:
        result = simulate_yaml(
            model,
            library=library,
            options=SimulationOptions(
                start_time=int(start_s),
                end_time=int(end_s),
                number_of_intervals=number_of_intervals(start_s, end_s),
            ),
            project_path=project_path,
            # trano's default package name for a dict is "model", a Modelica keyword (fails to parse)
            name=modelica_package_name(run_id),
            runtime="local",
            timeout_s=settings.simulation_timeout_s,
        )
    except Exception as error:
        _finish(sessions, run_id, RunStatus.FAILED, error_kind="internal", error_message=repr(error))
        raise

    status = RunStatus.SUCCEEDED if result.success else RunStatus.FAILED
    _finish(
        sessions,
        run_id,
        status,
        result_path=str(result.result_path) if result.result_path else None,
        duration_s=result.duration_s,
        error_kind=result.error_kind.value if result.error_kind else None,
        error_message=result.error_message,
        log_tail=result.log[-LOG_TAIL_CHARS:],
    )
    log.info(
        "run end run_id=%s status=%s duration_s=%.1f error_kind=%s",
        run_id,
        status.value,
        result.duration_s,
        result.error_kind,
    )
    return status


def _finish(sessions: sessionmaker[Session], run_id: uuid.UUID, status: RunStatus, **fields: Any) -> None:
    with sessions() as session:
        run = session.get_one(Run, run_id)
        run.status = status
        for name, value in fields.items():
            setattr(run, name, value)
        session.commit()


async def run_simulation(ctx: dict[str, Any], run_id: str) -> str:
    status = await asyncio.to_thread(execute_run, uuid.UUID(run_id), ctx["sessions"], ctx["settings"])
    return status.value


async def enqueue_simulation(redis: ArqRedis, run_id: uuid.UUID) -> None:
    """Queue a simulation; the run id is the job id, so a run is never queued twice."""
    await redis.enqueue_job(JOB_NAME, str(run_id), _job_id=str(run_id))


async def startup(ctx: dict[str, Any]) -> None:
    # arq's CLI configures only its own logger; give ours a handler too.
    if not logging.getLogger("llmtrano").handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger("llmtrano").addHandler(handler)
        logging.getLogger("llmtrano").setLevel(logging.INFO)
    settings = Settings()
    ctx["settings"] = settings
    ctx["sessions"] = make_sessionmaker(settings.database_url)
    log.info("worker ready runs_dir=%s trano=%s", settings.runs_dir, trano_version())


_settings = Settings()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_simulation]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(_settings.valkey_url)
    max_jobs = 1  # one OpenModelica build and simulation at a time: RAM is the limit (docs/measurements.md)
    job_timeout = _settings.simulation_timeout_s + JOB_TIMEOUT_MARGIN_S
    max_tries = 1  # a simulation that killed the worker would kill it again
