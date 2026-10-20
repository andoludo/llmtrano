"""arq worker: ``check_model`` and ``run_simulation`` jobs with trano's local runtime (no Docker socket).

Start with ``arq llmtrano.jobs.worker.WorkerSettings`` (the worker image's CMD).

- ``check_model(run_id)``: ``trano.api.check_yaml``; the readable validation errors (T8) go to
  ``runs.errors``.
- ``run_simulation(run_id)``: ``trano.api.simulate_yaml``; each trano phase is published on ``run:<id>`` and
  written on the row; on success the KPI JSON (``llmtrano.kpi``) goes to the ``kpis`` table.

Failures keep the error kind, a one-line message and the tail of the log.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

import yaml
from arq.connections import RedisSettings
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker
from trano.api import check_yaml, simulate_yaml
from trano.data_models.conversion import convert_network
from trano.elements.library.library import Library
from trano_simulate.result import SimulationErrorKind, SimulationResult
from trano_simulate.simulate import SimulationOptions

from llmtrano.db.models import KpiRow, Run, RunStatus
from llmtrano.db.session import make_sessionmaker
from llmtrano.jobs.progress import Publisher, channel, progress_message
from llmtrano.kpi.compute import house_kpis
from llmtrano.logs import configure_logging
from llmtrano.settings import Settings
from llmtrano.trano_info import trano_version

log = logging.getLogger(__name__)

LOG_TAIL_CHARS = 5_000
OUTPUT_STEP_S = 3600  # one stored output point per simulated hour
# Margin over trano's own timeout, so trano stops omc and reports `timeout` before arq cancels the job.
JOB_TIMEOUT_MARGIN_S = 60


class NullPublisher:
    def publish(self, channel: str, message: str) -> None:
        return None


def number_of_intervals(start_s: float, end_s: float) -> int:
    return max(1, round((end_s - start_s) / OUTPUT_STEP_S))


def modelica_package_name(run_id: uuid.UUID) -> str:
    """A valid, per-run Modelica package name; the result file is ``<name>.building_res.mat``."""
    return f"run_{run_id.hex[:8]}"


def readable_errors(result: SimulationResult) -> list[str]:
    """One line per error: trano's ``Invalid model:`` list for a validation error, else the message."""
    if result.error_kind is SimulationErrorKind.validation:
        lines = [line[2:] for line in result.log.splitlines() if line.startswith("- ")]
        if lines:
            return lines
    return [result.error_message] if result.error_message else []


def compute_kpis(project_path: Path, name: str, library: str, result_path: Path) -> dict[str, Any]:
    """The KPI JSON of a run: trano rebuilds the network from the YAML that ``simulate_yaml`` wrote."""
    network = convert_network(
        name, project_path / f"{name}.yaml", library=Library.from_configuration(library)
    )
    return house_kpis(result_path, network)


def _start(sessions: sessionmaker[Session], run_id: uuid.UUID) -> tuple[dict[str, Any], str, float, float]:
    with sessions() as session:
        run = session.get_one(Run, run_id)
        run.status = RunStatus.RUNNING
        run.trano_version = trano_version()
        session.commit()
        return yaml.safe_load(run.yaml), run.library, run.start_s, run.end_s


def _finish(sessions: sessionmaker[Session], run_id: uuid.UUID, status: RunStatus, **fields: Any) -> None:
    with sessions() as session:
        run = session.get_one(Run, run_id)
        run.status = status
        for name, value in fields.items():
            setattr(run, name, value)
        session.commit()


def _set_phase(sessions: sessionmaker[Session], run_id: uuid.UUID, phase: str) -> None:
    with sessions() as session:
        session.get_one(Run, run_id).phase = phase
        session.commit()


def _failure_fields(result: SimulationResult) -> dict[str, Any]:
    return {
        "duration_s": result.duration_s,
        "error_kind": result.error_kind.value if result.error_kind else None,
        "error_message": result.error_message,
        "errors": readable_errors(result) or None,
        "log_tail": result.log[-LOG_TAIL_CHARS:],
    }


def execute_check(run_id: uuid.UUID, sessions: sessionmaker[Session], settings: Settings) -> RunStatus:
    """Validate, translate and check the model of row ``run_id`` with OpenModelica. Blocking."""
    model, library, _, _ = _start(sessions, run_id)
    log.info("check start run_id=%s library=%s", run_id, library)
    try:
        result = check_yaml(
            model,
            library=library,
            runtime="local",
            project_path=Path(settings.runs_dir) / str(run_id),
            name=modelica_package_name(run_id),
            timeout_s=settings.simulation_timeout_s,
        )
    except Exception as error:
        _finish(sessions, run_id, RunStatus.FAILED, error_kind="internal", error_message=repr(error))
        raise
    status = RunStatus.SUCCEEDED if result.success else RunStatus.FAILED
    _finish(sessions, run_id, status, phase="done", **_failure_fields(result))
    log.info("check end run_id=%s status=%s error_kind=%s", run_id, status.value, result.error_kind)
    return status


def execute_run(
    run_id: uuid.UUID,
    sessions: sessionmaker[Session],
    settings: Settings,
    publisher: Publisher | None = None,
) -> RunStatus:
    """Simulate the ``runs`` row ``run_id``, publish its phases, store its KPIs. Blocking (a thread)."""
    publisher = publisher or NullPublisher()
    model, library, start_s, end_s = _start(sessions, run_id)
    project_path = Path(settings.runs_dir) / str(run_id)
    name = modelica_package_name(run_id)
    started = time.perf_counter()

    def on_phase(phase: str) -> None:
        _set_phase(sessions, run_id, str(phase))
        publisher.publish(
            channel(run_id), progress_message(run_id, str(phase), time.perf_counter() - started)
        )

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
            name=name,
            runtime="local",
            timeout_s=settings.simulation_timeout_s,
            on_phase=on_phase,
        )
        kpis = (
            compute_kpis(project_path, name, library, result.result_path)
            if result.success and result.result_path
            else None
        )
    except Exception as error:
        _finish(sessions, run_id, RunStatus.FAILED, error_kind="internal", error_message=repr(error))
        publisher.publish(channel(run_id), progress_message(run_id, "failed", time.perf_counter() - started))
        raise

    status = RunStatus.SUCCEEDED if result.success else RunStatus.FAILED
    with sessions() as session:
        if kpis is not None:
            session.add(KpiRow(run_id=run_id, values=kpis))
        session.commit()
    _finish(
        sessions,
        run_id,
        status,
        result_path=str(result.result_path) if result.result_path else None,
        **_failure_fields(result),
    )
    publisher.publish(channel(run_id), progress_message(run_id, status.value, time.perf_counter() - started))
    log.info(
        "run end run_id=%s status=%s duration_s=%.1f error_kind=%s heating_kwh=%s",
        run_id,
        status.value,
        result.duration_s,
        result.error_kind,
        f"{kpis['heating_energy_kwh']:.0f}" if kpis else None,
    )
    return status


async def run_simulation(ctx: dict[str, Any], run_id: str) -> str:
    args = (uuid.UUID(run_id), ctx["sessions"], ctx["settings"], ctx["publisher"])
    status = await asyncio.to_thread(execute_run, *args)
    return status.value


async def check_model(ctx: dict[str, Any], run_id: str) -> str:
    status = await asyncio.to_thread(execute_check, uuid.UUID(run_id), ctx["sessions"], ctx["settings"])
    return status.value


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()  # arq's CLI configures only its own logger
    settings = Settings()
    ctx["settings"] = settings
    ctx["sessions"] = make_sessionmaker(settings.database_url)
    ctx["publisher"] = Redis.from_url(settings.valkey_url)  # blocking client: used from the job's thread
    log.info("worker ready runs_dir=%s trano=%s", settings.runs_dir, trano_version())


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx["publisher"].close()


_settings = Settings()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_simulation, check_model]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.valkey_url)
    max_jobs = 1  # one OpenModelica build and simulation at a time: RAM is the limit (docs/measurements.md)
    job_timeout = _settings.simulation_timeout_s + JOB_TIMEOUT_MARGIN_S
    max_tries = 1  # a simulation that killed the worker would kill it again
    keep_result = 3600  # the api awaits job results
