"""Model shape checks on in-memory SQLite (unit tests only; the app uses Postgres)."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from llmtrano.db.models import Base, Run, RunStatus


def test_run_defaults_round_trip() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Run(library="Buildings", end_s=86400.0, yaml="default: {}"))
        session.commit()
        run = session.scalars(select(Run)).one()
        assert run.status is RunStatus.QUEUED
        assert run.start_s == 0.0
        assert run.created_at is not None
        assert run.result_path is None


def test_status_values_are_lowercase_strings() -> None:
    assert [s.value for s in RunStatus] == ["queued", "running", "succeeded", "failed"]
