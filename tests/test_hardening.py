"""Token buckets and the retention sweep (phase 3)."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from llmtrano.api.limits import RateLimiter
from llmtrano.db.models import Base, KpiRow, Message, Run
from llmtrano.db.models import Session as SessionRow
from llmtrano.jobs.retention import erase_session, remove_run_directories, sweep
from llmtrano.settings import Settings


def test_token_bucket_refills_at_the_rate() -> None:
    now = [0.0]
    limiter = RateLimiter(per_minute=2, clock=lambda: now[0])
    assert limiter.allow("s") and limiter.allow("s") and not limiter.allow("s")
    now[0] = 31.0  # half a minute: one token back
    assert limiter.allow("s") and not limiter.allow("s")
    assert limiter.allow("other")
    limiter.forget("s")
    assert limiter.allow("s")
    assert RateLimiter(per_minute=0).allow("anything")  # disabled


def _db() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _session_with_run(sessions: sessionmaker[Session], created: datetime) -> tuple[uuid.UUID, uuid.UUID]:
    with sessions() as session:
        row = SessionRow(created_at=created)
        session.add(row)
        session.flush()
        run = Run(session_id=row.id, library="Buildings", end_s=1.0, yaml="a: 1", created_at=created)
        session.add(run)
        session.flush()
        session.add_all(
            [Message(session_id=row.id, role="user", content="x"), KpiRow(run_id=run.id, values={})]
        )
        session.commit()
        return row.id, run.id


def test_erase_session_removes_rows_and_names_the_runs(tmp_path: Path) -> None:
    sessions = _db()
    session_id, run_id = _session_with_run(sessions, datetime.now(UTC))
    (tmp_path / str(run_id)).mkdir()
    run_ids = erase_session(sessions, session_id)
    assert run_ids == [run_id]
    with sessions() as session:
        assert session.get(SessionRow, session_id) is None and session.get(Run, run_id) is None
        assert session.scalars(select(Message)).all() == [] and session.scalars(select(KpiRow)).all() == []
    assert remove_run_directories(tmp_path, run_ids) == 1 and not (tmp_path / str(run_id)).exists()


def test_sweep_applies_the_retention_and_removes_orphan_directories(tmp_path: Path) -> None:
    sessions = _db()
    now = datetime.now(UTC)
    old_session, old_run = _session_with_run(sessions, now - timedelta(days=100))
    new_session, new_run = _session_with_run(sessions, now - timedelta(days=1))
    for name in (str(old_run), str(new_run), str(uuid.uuid4()), "not-a-run"):
        (tmp_path / name).mkdir()
    result = sweep(sessions, Settings(retention_days=90, runs_dir=tmp_path), now=now)
    assert (result.sessions_deleted, result.runs_deleted, result.directories_removed) == (1, 1, 2)
    with sessions() as session:
        assert (
            session.get(SessionRow, old_session) is None and session.get(SessionRow, new_session) is not None
        )
        assert session.get(Run, new_run) is not None
    assert not (tmp_path / str(old_run)).exists() and (tmp_path / str(new_run)).exists()
    assert (tmp_path / "not-a-run").exists()  # only run directories are touched
