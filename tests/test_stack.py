"""Workflow on the real stack: Postgres checkpointer and tables, Valkey, the worker (OpenModelica), a fake
LLM.

Needs db, queue and worker (``docker compose up -d``); run with ``uv run pytest -m compose
tests/test_stack.py``.
"""

import json
import uuid
from typing import Any, cast

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from fakes import FakeLLM
from langgraph.types import Command
from openai import AsyncOpenAI
from sqlalchemy import select

from llmtrano.db.migrate import upgrade_head
from llmtrano.db.models import FactRow, HouseBriefRow, KpiRow, Message, Run, RunKind
from llmtrano.db.models import Session as SessionRow
from llmtrano.db.recorder import DbRecorder
from llmtrano.db.session import make_sessionmaker
from llmtrano.jobs.queue import WorkerJobs
from llmtrano.settings import Settings
from llmtrano.workflow.checkpoint import postgres_checkpointer
from llmtrano.workflow.deps import Deps
from llmtrano.workflow.graph import build_graph
from llmtrano.workflow.state import Phase

pytestmark = pytest.mark.compose


def facts(*items: tuple[str, str], complete: bool = False) -> str:
    return json.dumps(
        {
            "facts": [
                {"field": f, "value": v, "scenario": "baseline", "evidence": v, "confidence": "high"}
                for f, v in items
            ],
            "user_says_complete": complete,
        }
    )


async def test_session_to_simulation_and_restart() -> None:
    settings = Settings()
    upgrade_head(settings.database_url)
    sessions = make_sessionmaker(settings.database_url)
    with sessions() as session:
        row = SessionRow()
        session.add(row)
        session.commit()
        session_id = str(row.id)
    llm = FakeLLM()
    llm.queue(
        "extract",
        facts(
            ("building_type", "terraced"),
            ("construction_period", "1962"),
            ("heated_floor_area_m2", "110 m2"),
            ("storeys", "2"),
            complete=True,
        ),
    )
    llm.queue("respond", "Please check the card and confirm.")
    llm.queue("interpret", "The table shows the heating need of the house.")
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
    redis = await create_pool(RedisSettings.from_dsn(settings.valkey_url))
    deps = Deps(
        llm=cast(AsyncOpenAI, llm),
        jobs=WorkerJobs(redis, sessions, settings),
        recorder=DbRecorder(sessions),
        settings=settings,
    )
    events: list[dict[str, Any]] = []
    try:
        async with postgres_checkpointer(settings.database_url) as saver:
            first = await build_graph(deps, saver).ainvoke(
                {"session_id": session_id, "message": "Terraced, 1962, 110 m2, two floors. That's all."},
                config,
            )
            assert first["phase"] == Phase.confirm
        # a new checkpointer connection and graph, as after an api restart
        async with postgres_checkpointer(settings.database_url) as saver:
            graph = build_graph(deps, saver)
            async for mode, chunk in graph.astream(
                Command(resume={"approved": True}), config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    events.append(chunk)
                else:
                    result = chunk
    finally:
        await redis.aclose()

    assert result["phase"] == Phase.done, result.get("errors")
    kpis = result["kpis"]
    assert kpis["heating_energy_kwh"] > 0 and kpis["peak_heating_w"] > 0
    phases = [e["phase"] for e in events if e["event"] == "run_phase" and e["kind"] == "simulate"]
    assert phases[:4] == ["generating", "compiling", "simulating", "done"]
    assert phases[-1] == "succeeded"
    with sessions() as session:
        sid = uuid.UUID(session_id)
        runs = session.scalars(select(Run).where(Run.session_id == sid).order_by(Run.created_at)).all()
        assert [r.kind for r in runs] == [RunKind.CHECK, RunKind.SIMULATE]
        assert session.get_one(KpiRow, runs[1].id).values["heating_energy_kwh"] == kpis["heating_energy_kwh"]
        assert (
            session.scalars(select(HouseBriefRow).where(HouseBriefRow.session_id == sid)).one().confirmed_at
        )
        roles = session.scalars(select(Message.role).where(Message.session_id == sid)).all()
        assert set(roles) == {"user", "extract", "assistant"}
        assert len(session.scalars(select(FactRow).where(FactRow.session_id == sid)).all()) == 4
