"""Against the running stack (`docker compose --profile gpu up -d`). Run with `uv run pytest -m compose`."""

import json
import os
import time
import urllib.request
from typing import Any

import pytest

from llmtrano.data import tutorial_house_yaml

API = os.environ.get("LLMTRANO_API_URL", "http://localhost:8000")

pytestmark = pytest.mark.compose


def _request(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30
) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode()


def test_health_all_ok() -> None:
    status, body = _request("GET", "/health")
    assert status == 200
    assert json.loads(body) == {"status": "ok", "db": "ok", "queue": "ok", "llm": "ok"}


def test_models_lists_both() -> None:
    _, body = _request("GET", "/models")
    assert set(json.loads(body)) >= {"qwen3.5-9b", "qwen3.6-35b-a3b"}


@pytest.mark.llm
def test_chat_streams_tokens() -> None:
    _, body = _request(
        "POST",
        "/chat",
        {
            "model": "qwen3.5-9b",
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        },
        timeout=180,
    )
    events = [block for block in body.strip().split("\n\n") if block]
    assert any(block.startswith("event: token") for block in events)
    assert events[-1].startswith("event: done")


@pytest.mark.simulation
def test_run_one_day_succeeds() -> None:
    status, body = _request("POST", "/runs", {"yaml_text": tutorial_house_yaml(), "end_time_s": 86400})
    assert status == 201
    run_id = json.loads(body)["id"]
    deadline = time.monotonic() + 600
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = json.loads(_request("GET", f"/runs/{run_id}")[1])
        if run["status"] in {"succeeded", "failed"}:
            break
        time.sleep(2)
    assert run["status"] == "succeeded", run.get("error_message")
    assert run["result_path"].endswith(".building_res.mat")
