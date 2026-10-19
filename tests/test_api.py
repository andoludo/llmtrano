"""api unit tests: FastAPI's test client, SQLite in memory, a fake queue and a fake LLM client."""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from llmtrano.api.app import Services, create_app
from llmtrano.db.models import Base, Run, RunStatus


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


class FakeLLM:
    """Mimics the parts of AsyncOpenAI the api uses."""

    def __init__(self, tokens: list[str], up: bool = True) -> None:
        self.tokens = tokens
        self.up = up
        self.requests: list[dict[str, Any]] = []
        self.models = SimpleNamespace(list=self._list)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _list(self) -> SimpleNamespace:
        if not self.up:
            raise ConnectionError("llm down")
        return SimpleNamespace(data=[SimpleNamespace(id="qwen3.5-9b"), SimpleNamespace(id="qwen3.6-35b-a3b")])

    async def _create(self, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        self.requests.append(kwargs)

        async def stream() -> AsyncIterator[SimpleNamespace]:
            for token in self.tokens:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=token))])
            yield SimpleNamespace(choices=[])  # final usage-only chunk

        return stream()

    async def close(self) -> None:
        return None


@dataclass
class Harness:
    client: TestClient
    queue: FakeQueue
    llm: FakeLLM
    sessions: sessionmaker[Any]


@pytest.fixture
def harness() -> Iterator[Harness]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    queue, llm = FakeQueue(), FakeLLM(["Hel", "lo"])
    services = Services(sessions=sessions, queue=queue, llm=cast(AsyncOpenAI, llm))
    with TestClient(create_app(services)) as client:
        yield Harness(client, queue, llm, sessions)


def _sse_events(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


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


def test_chat_streams_tokens_with_thinking_off(harness: Harness) -> None:
    response = harness.client.post(
        "/chat", json={"model": "qwen3.5-9b", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _sse_events(response.text) == [
        ("token", {"text": "Hel"}),
        ("token", {"text": "lo"}),
        ("done", {}),
    ]
    request = harness.llm.requests[0]
    assert request["stream"] is True
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_chat_rejects_empty_messages(harness: Harness) -> None:
    assert harness.client.post("/chat", json={"model": "m", "messages": []}).status_code == 422


def test_create_and_get_run(harness: Harness) -> None:
    response = harness.client.post("/runs", json={"yaml_text": "default: {}\n", "end_time_s": 86400})
    assert response.status_code == 201
    run_id = response.json()["id"]
    assert harness.queue.jobs == [("run_simulation", (run_id,), run_id)]

    run = harness.client.get(f"/runs/{run_id}").json()
    assert run["status"] == RunStatus.QUEUED.value
    assert run["end_s"] == 86400
    assert run["library"] == "Buildings"
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
