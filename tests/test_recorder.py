"""Database recorder and phase 1 tables on in-memory SQLite (unit tests only; the app uses Postgres)."""

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from llmtrano.brief.model import Fact, FieldProvenance, Glazing, HouseBrief, Source
from llmtrano.db.models import Base, FactRow, Feedback, HouseBriefRow, KpiRow, Message, Run, RunKind
from llmtrano.db.models import Session as SessionRow
from llmtrano.db.recorder import DbRecorder
from llmtrano.llm.calls import CallStats


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def session_id(sessions: sessionmaker[Session]) -> str:
    with sessions() as session:
        row = SessionRow()
        session.add(row)
        session.commit()
        return str(row.id)


async def test_message_with_call_stats(sessions: sessionmaker[Session], session_id: str) -> None:
    stats = CallStats(
        call="respond", model="qwen3.5-9b", prompt_version="a66246ea", tokens_in=900, tokens_out=40
    )
    stats.ttft_ms, stats.latency_ms = 120.0, 1800.0
    message_id = await DbRecorder(sessions).message(session_id, "assistant", "Hello", stats)
    with sessions() as session:
        row = session.get_one(Message, message_id)
        assert (row.role, row.content, row.model, row.prompt_version) == (
            "assistant",
            "Hello",
            "qwen3.5-9b",
            "a66246ea",
        )
        assert (row.tokens_in, row.tokens_out, row.ttft_ms, row.latency_ms) == (900, 40, 120.0, 1800.0)


async def test_facts(sessions: sessionmaker[Session], session_id: str) -> None:
    fact = Fact(
        field="glazing",
        value=Glazing.triple,
        raw_value="driedubbel",
        scenario="renovation",
        evidence="we want triple glazing",
        confidence="high",
        turn=2,
    )
    await DbRecorder(sessions).facts(session_id, [fact], accepted=False)
    with sessions() as session:
        row = session.scalars(select(FactRow)).one()
        assert (row.field, row.value, row.raw_value, row.scenario, row.accepted) == (
            "glazing",
            "triple",
            "driedubbel",
            "renovation",
            False,
        )


async def test_brief_versions(sessions: sessionmaker[Session], session_id: str) -> None:
    recorder = DbRecorder(sessions)
    brief = HouseBrief(heated_floor_area_m2=120, municipality="Gent")
    provenance = {"heated_floor_area_m2": FieldProvenance(source=Source.user, evidence="120 m2", turn=1)}
    await recorder.brief(session_id, brief, provenance, "abc", confirmed=True)
    await recorder.brief(session_id, brief, provenance, "abc", confirmed=False)
    with sessions() as session:
        rows = session.scalars(select(HouseBriefRow).order_by(HouseBriefRow.version)).all()
        assert [r.version for r in rows] == [1, 2]
        assert rows[0].values["municipality"] == "Gent"
        assert rows[0].provenance["heated_floor_area_m2"]["evidence"] == "120 m2"
        assert rows[0].confirmed_at is not None and rows[1].confirmed_at is None
        assert rows[0].trano_version


def test_run_kpis_and_feedback(sessions: sessionmaker[Session], session_id: str) -> None:
    with sessions() as session:
        run = Run(
            session_id=uuid.UUID(session_id), kind=RunKind.CHECK, library="Buildings", end_s=1.0, yaml="a: 1"
        )
        message = Message(session_id=uuid.UUID(session_id), role="assistant", content="x")
        session.add_all([run, message])
        session.flush()
        session.add_all(
            [
                KpiRow(run_id=run.id, values={"heating_energy_kwh": 1.0}),
                Feedback(message_id=message.id, thumbs=1),
            ]
        )
        session.commit()
        assert session.get_one(Run, run.id).kind is RunKind.CHECK
        assert session.get_one(KpiRow, run.id).values == {"heating_energy_kwh": 1.0}
        assert session.scalars(select(Feedback)).one().thumbs == 1
