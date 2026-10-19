"""Worker and smoke logic without OpenModelica: trano's simulate_yaml is replaced by a fake."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from trano_simulate.result import SimulationErrorKind, SimulationResult

from llmtrano.db.models import Base, Run, RunStatus
from llmtrano.jobs import worker
from llmtrano.jobs.smoke import Outcome, phase_timings, report
from llmtrano.settings import Settings


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _insert(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions() as session:
        run = Run(library="Buildings", start_s=0.0, end_s=86400.0, yaml="default: {}\n")
        session.add(run)
        session.commit()
        return run.id


def test_number_of_intervals_is_hourly() -> None:
    assert worker.number_of_intervals(0, 86400) == 24
    assert worker.number_of_intervals(0, 365 * 86400) == 8760
    assert worker.number_of_intervals(0, 10) == 1


def test_execute_run_success(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}

    def fake_simulate_yaml(model: Any, **kwargs: Any) -> SimulationResult:
        calls.update(kwargs, model=model)
        return SimulationResult(success=True, result_path=tmp_path / "r.mat", log="ok", duration_s=12.5)

    monkeypatch.setattr(worker, "simulate_yaml", fake_simulate_yaml)
    run_id = _insert(sessions)
    status = worker.execute_run(run_id, sessions, Settings(runs_dir=tmp_path, simulation_timeout_s=99))

    assert status is RunStatus.SUCCEEDED
    assert calls["model"] == {"default": {}}
    assert calls["runtime"] == "local"
    assert calls["timeout_s"] == 99
    assert calls["project_path"] == tmp_path / str(run_id)
    assert calls["options"].end_time == 86400
    assert calls["options"].number_of_intervals == 24
    assert calls["name"] == f"run_{run_id.hex[:8]}"
    with sessions() as session:
        run = session.get_one(Run, run_id)
        assert run.status is RunStatus.SUCCEEDED
        assert run.duration_s == 12.5
        assert run.result_path == str(tmp_path / "r.mat")
        assert run.trano_version


def test_execute_run_failure_is_recorded(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_simulate_yaml(model: Any, **kwargs: Any) -> SimulationResult:
        return SimulationResult.failure(SimulationErrorKind.translation, "bad model", log="x" * 9000)

    monkeypatch.setattr(worker, "simulate_yaml", fake_simulate_yaml)
    run_id = _insert(sessions)
    assert worker.execute_run(run_id, sessions, Settings(runs_dir=tmp_path)) is RunStatus.FAILED
    with sessions() as session:
        run = session.get_one(Run, run_id)
        assert run.error_kind == "translation"
        assert run.error_message == "bad model"
        assert run.log_tail is not None and len(run.log_tail) == worker.LOG_TAIL_CHARS


def test_execute_run_unexpected_error_marks_failed(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(model: Any, **kwargs: Any) -> SimulationResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "simulate_yaml", broken)
    run_id = _insert(sessions)
    with pytest.raises(RuntimeError):
        worker.execute_run(run_id, sessions, Settings(runs_dir=tmp_path))
    with sessions() as session:
        run = session.get_one(Run, run_id)
        assert run.status is RunStatus.FAILED
        assert run.error_kind == "internal"


def test_phase_timings_from_simulate_record() -> None:
    log = "record SimulationResult\n    timeFrontend = 1.5,\n    timeCompile = 20.25,\n    timeTotal = 30.0\n"
    assert phase_timings(log) == {"Frontend": 1.5, "Compile": 20.25, "Total": 30.0}


def test_report_table() -> None:
    table = report([Outcome("day", "succeeded", 31.0, 2_500_000, None, {"Total": 30.0})])
    assert "| day | succeeded | 31.0 | 2.50 MB | Total 30.0 | - |" in table
