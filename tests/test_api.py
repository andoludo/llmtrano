"""api unit tests: FastAPI's test client, SQLite in memory, a fake queue, a fake LLM and the real graph."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fakes import FakeLLM
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from openai import AsyncOpenAI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_workflow import FakeJobs, facts

from llmtrano.api.app import Services, create_app
from llmtrano.db.models import Base, Feedback, HouseBriefRow, Message, Run, RunStatus
from llmtrano.db.recorder import DbRecorder
from llmtrano.settings import Settings
from llmtrano.workflow.deps import Deps
from llmtrano.workflow.graph import build_graph, make_serializer


@dataclass
class FakeQueue:
    jobs: list[tuple[str, tuple[Any, ...], str | None]] = field(default_factory=list)
    healthy: bool = True

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None) -> None:
        self.jobs.append((function, args, _job_id))

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("valkey down")
        return True

    async def aclose(self) -> None:
        return None


@dataclass
class Harness:
    client: TestClient
    queue: FakeQueue
    llm: FakeLLM
    jobs: FakeJobs
    sessions: sessionmaker[Any]

    def session(self) -> str:
        response = self.client.post("/sessions")
        assert response.status_code == 201
        return str(response.json()["id"])

    def stream(self, path: str, body: dict[str, Any]) -> list[tuple[str, Any]]:
        response = self.client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        return _sse_events(response.text)


@pytest.fixture
def harness() -> Iterator[Harness]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    queue, llm, jobs = FakeQueue(), FakeLLM(), FakeJobs()
    deps = Deps(llm=cast(AsyncOpenAI, llm), jobs=jobs, recorder=DbRecorder(sessions), settings=Settings())
    graph = build_graph(deps, InMemorySaver(serde=make_serializer()))
    services = Services(
        sessions=sessions, queue=queue, llm=cast(AsyncOpenAI, llm), graph=graph, valkey_url="redis://unused"
    )
    with TestClient(create_app(services)) as client:
        yield Harness(client, queue, llm, jobs, sessions)


def _sse_events(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


def _names(events: list[tuple[str, Any]]) -> list[str]:
    names = [name for name, _ in events]
    return [name for i, name in enumerate(names) if i == 0 or names[i - 1] != name]  # collapse token runs


def _data(events: list[tuple[str, Any]], name: str) -> Any:
    return next(data for event, data in events if event == name)


def _to_confirmation(h: Harness, session_id: str) -> list[tuple[str, Any]]:
    h.llm.queue(
        "extract",
        facts(
            ("building_type", "detached", "baseline"),
            ("construction_period", "1985", "baseline"),
            ("heated_floor_area_m2", "140 m2", "baseline"),
            ("storeys", "2", "baseline"),
            complete=True,
        ),
    )
    h.llm.queue("respond", "Thanks, all noted. Please check the card and confirm.")
    return h.stream(
        f"/sessions/{session_id}/messages", {"message": "Detached, 1985, 140 m2, 2 floors. That's all."}
    )


# ---------- health, models, runs (phase 0)


def test_health_ok(harness: Harness) -> None:
    response = harness.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "queue": "ok", "llm": "ok"}


def test_health_degraded_without_llm(harness: Harness) -> None:
    harness.llm.up = False
    response = harness.client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["llm"].startswith("error: ConnectionError")


def test_health_down_without_queue(harness: Harness) -> None:
    harness.queue.healthy = False
    response = harness.client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_models(harness: Harness) -> None:
    assert harness.client.get("/models").json() == ["qwen3.5-9b", "qwen3.6-35b-a3b"]


def test_models_unavailable(harness: Harness) -> None:
    harness.llm.up = False
    assert harness.client.get("/models").status_code == 503


def test_phase_0_chat_is_gone(harness: Harness) -> None:
    assert harness.client.post("/chat", json={}).status_code == 404


def test_create_and_get_run(harness: Harness) -> None:
    response = harness.client.post("/runs", json={"yaml_text": "default: {}\n", "end_time_s": 86400})
    assert response.status_code == 201
    run_id = response.json()["id"]
    assert harness.queue.jobs == [("run_simulation", (run_id,), run_id)]

    run = harness.client.get(f"/runs/{run_id}").json()
    assert run["status"] == RunStatus.QUEUED.value
    assert run["end_s"] == 86400
    assert run["library"] == "Buildings"
    assert run["kind"] == "simulate"
    assert "yaml" not in run  # the model text is not echoed back


def test_create_run_rejects_invalid_yaml(harness: Harness) -> None:
    response = harness.client.post("/runs", json={"yaml_text": "a: [unclosed", "end_time_s": 10})
    assert response.status_code == 422
    with harness.sessions() as session:
        assert session.query(Run).count() == 0
    assert harness.queue.jobs == []


def test_create_run_rejects_non_mapping(harness: Harness) -> None:
    assert harness.client.post("/runs", json={"yaml_text": "- a\n- b\n", "end_time_s": 10}).status_code == 422


def test_get_unknown_run(harness: Harness) -> None:
    assert harness.client.get("/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_events_of_a_finished_run(harness: Harness) -> None:
    with harness.sessions() as session:
        run = Run(library="Buildings", end_s=1.0, yaml="a: 1", status=RunStatus.SUCCEEDED, phase="done")
        session.add(run)
        session.commit()
        run_id = run.id
    response = harness.client.get(f"/runs/{run_id}/events")
    assert _sse_events(response.text) == [
        ("run_phase", {"run_id": str(run_id), "phase": "succeeded", "t": None})
    ]
    assert harness.client.get(f"/runs/{uuid.uuid4()}/events").status_code == 404


# ---------- sessions


def test_message_streams_tokens_then_card_questions_and_phase(harness: Harness) -> None:
    session_id = harness.session()
    harness.llm.queue("extract", facts(("building_type", "terraced", "baseline")))
    harness.llm.queue("respond", "Thanks, a terraced house. How big is it, and how many floors?")
    events = harness.stream(
        f"/sessions/{session_id}/messages", {"message": "A terraced house.", "model": "m-x"}
    )
    assert _names(events) == ["token", "message", "brief", "questions", "phase"]
    reply = "".join(data["text"] for name, data in events if name == "token")
    assert reply == "Thanks, a terraced house. How big is it, and how many floors?"
    card = _data(events, "brief")
    assert {r["field"]: r["value"] for r in card["rows"]}["building_type"] == "Terraced"
    questions = _data(events, "questions")
    assert [q["field"] for q in questions] == ["heated_floor_area_m2", "storeys"]
    assert [o["label"] for o in questions[1]["options"]] == ["1", "2", "3"]
    assert _data(events, "phase") == {"phase": "intake"}
    assert {r["model"] for r in harness.llm.requests} == {"m-x"}  # the model chosen in the ui


def test_answer_button(harness: Harness) -> None:
    session_id = harness.session()
    harness.llm.queue("respond", "Two floors, noted.")
    answer = {"field": "storeys", "value": "2", "label": "2"}
    events = harness.stream(f"/sessions/{session_id}/messages", {"answer": answer})
    assert {r["field"]: r["value"] for r in _data(events, "brief")["rows"]}["storeys"] == "2"
    assert harness.llm.calls("extract") == []


def test_message_validation(harness: Harness) -> None:
    session_id = harness.session()
    path = f"/sessions/{session_id}/messages"
    assert harness.client.post(path, json={}).status_code == 422
    both = {"message": "x", "answer": {"field": "storeys", "value": "2", "label": "2"}}
    assert harness.client.post(path, json=both).status_code == 422
    assert harness.client.post(f"/sessions/{uuid.uuid4()}/messages", json={"message": "x"}).status_code == 404


def test_confirm_needs_a_waiting_session(harness: Harness) -> None:
    session_id = harness.session()
    assert harness.client.post(f"/sessions/{session_id}/confirm", json={"approved": True}).status_code == 409


def test_confirm_runs_the_simulation_and_explains(harness: Harness) -> None:
    session_id = harness.session()
    events = _to_confirmation(harness, session_id)
    assert _data(events, "phase") == {"phase": "confirm"}
    assert _data(events, "brief")["ready"] is True
    harness.llm.queue("interpret", "The house needs 6469 kWh a year, 53.9 kWh per m2.")
    events = harness.stream(f"/sessions/{session_id}/confirm", {"approved": True})
    names = _names(events)
    assert names[: names.index("kpis")].count("run_phase") >= 1
    assert names[names.index("kpis") :] == ["kpis", "token", "explanation", "brief", "questions", "phase"]
    simulate_phases = [d["phase"] for n, d in events if n == "run_phase" and d["kind"] == "simulate"]
    assert simulate_phases == ["generating", "compiling", "simulating", "done"]
    assert _data(events, "explanation")["text"] == "The house needs 6469 kWh a year, 53.9 kWh per m2."
    assert _data(events, "phase") == {"phase": "done"}

    state = harness.client.get(f"/sessions/{session_id}").json()
    assert state["phase"] == "done"
    assert state["kpis"]["heating_energy_kwh"] == 6469.2
    assert state["explanation"].startswith("The house needs")
    assert state["messages"][-1] == {"role": "assistant", "content": state["explanation"]}


def test_rejection_with_edits(harness: Harness) -> None:
    session_id = harness.session()
    _to_confirmation(harness, session_id)
    harness.llm.queue("respond", "Updated. Please confirm.")
    events = harness.stream(
        f"/sessions/{session_id}/confirm", {"approved": False, "edits": {"glazing": "triple"}}
    )
    rows = {r["field"]: r for r in _data(events, "brief")["rows"]}
    assert rows["glazing"]["value"] == "Triple glazing" and rows["glazing"]["source"] == "user"
    assert _data(events, "phase") == {"phase": "confirm"}
    assert harness.jobs.simulated == []


def test_message_during_confirmation(harness: Harness) -> None:
    session_id = harness.session()
    _to_confirmation(harness, session_id)
    harness.llm.queue("extract", facts(("occupants", "4", "baseline")))
    harness.llm.queue("respond", "Four people, noted. Please confirm.")
    events = harness.stream(f"/sessions/{session_id}/messages", {"message": "We are four."})
    assert {r["field"]: r["value"] for r in _data(events, "brief")["rows"]}["occupants"] == "4"


def test_get_new_session(harness: Harness) -> None:
    session_id = harness.session()
    state = harness.client.get(f"/sessions/{session_id}").json()
    assert state["phase"] == "intake" and state["messages"] == [] and state["kpis"] is None
    assert harness.client.get(f"/sessions/{uuid.uuid4()}").status_code == 404


def test_feedback(harness: Harness) -> None:
    session_id = harness.session()
    harness.llm.queue("extract", facts())
    harness.llm.queue("respond", "Tell me about the house.")
    events = harness.stream(f"/sessions/{session_id}/messages", {"message": "hello"})
    message_id = _data(events, "message")["id"]
    response = harness.client.post(
        "/feedback", json={"message_id": message_id, "thumbs": -1, "comment": "vague"}
    )
    assert response.status_code == 201
    with harness.sessions() as session:
        row = session.scalars(select(Feedback)).one()
        assert (str(row.message_id), row.thumbs, row.comment) == (message_id, -1, "vague")
        assert session.get(Message, uuid.UUID(message_id)) is not None
    missing = {"message_id": str(uuid.uuid4()), "thumbs": 1}
    assert harness.client.post("/feedback", json=missing).status_code == 404
    assert harness.client.post("/feedback", json={"message_id": message_id, "thumbs": 0}).status_code == 422


# ---------- phase 2: the renovation scenario through the api


def test_confirm_with_renovation_plans_runs_both_scenarios(harness: Harness) -> None:
    session_id = harness.session()
    harness.llm.queue(
        "extract",
        facts(
            ("building_type", "detached", "baseline"),
            ("construction_period", "1985", "baseline"),
            ("heated_floor_area_m2", "140 m2", "baseline"),
            ("storeys", "2", "baseline"),
            ("roof_insulation", "22 cm", "renovation"),
            complete=True,
        ),
    )
    harness.llm.queue("respond", "Noted, the roof insulation is planned. Please confirm.")
    events = harness.stream(
        f"/sessions/{session_id}/messages",
        {"message": "Detached, 1985, 140 m2, 2 floors; we plan 22 cm on the roof."},
    )
    card = _data(events, "brief")
    assert card["renovation_fields"] == ["roof_insulation"]
    assert {r["field"]: r.get("renovation") for r in card["rows"]}[
        "roof_insulation"
    ] == "Good (renovated or recent)"
    harness.llm.queue("interpret", "Now 6469 kWh; after the roof 6469 kWh too in this fake.")
    events = harness.stream(f"/sessions/{session_id}/confirm", {"approved": True})
    scenarios = [data["scenario"] for name, data in events if name == "kpis"]
    assert scenarios == ["baseline", "renovation"]
    assert [d["scenario"] for n, d in events if n == "run_phase" and d["phase"] == "simulating"] == [
        "baseline",
        "renovation",
    ]
    assert _data(events, "comparison")["comparison"]["heating_energy_kwh"]["saving"] == 0.0
    assert _data(events, "phase") == {"phase": "done"}
    state = harness.client.get(f"/sessions/{session_id}").json()
    assert state["renovation_kpis"]["heating_energy_kwh"] == 6469.2
    assert state["comparison"]["heating_energy_kwh"]["saving_percent"] == 0
    assert state["renovation_run_id"] is not None and state["run_id"] != state["renovation_run_id"]
    with harness.sessions() as session:
        runs = session.scalars(select(Run).order_by(Run.created_at)).all()
        assert [r.scenario for r in runs] == []  # fake jobs: no rows; the recorder wrote the Briefs
        briefs = session.scalars(select(HouseBriefRow).order_by(HouseBriefRow.version)).all()
        assert [b.scenario for b in briefs] == ["baseline", "renovation"]
