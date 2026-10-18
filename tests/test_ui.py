"""ui logic without a browser or a server: SSE parsing and the api client on httpx's mock transport."""

import json

import httpx
import pytest

from llmtrano.settings import Settings
from llmtrano.ui.app import (
    ApiClient,
    SseEvent,
    build_ui,
    parse_sse,
    run_status_markdown,
    to_api_messages,
)

SSE_BODY = (
    'event: token\ndata: {"text":"Hel"}\n\nevent: token\ndata: {"text":"lo"}\n\nevent: done\ndata: {}\n\n'
)


def _client(handler: httpx.MockTransport) -> ApiClient:
    return ApiClient("http://api", transport=handler)


def test_parse_sse() -> None:
    assert list(parse_sse(SSE_BODY.split("\n"))) == [
        SseEvent("token", {"text": "Hel"}),
        SseEvent("token", {"text": "lo"}),
        SseEvent("done", {}),
    ]


def test_parse_sse_last_event_without_blank_line() -> None:
    assert list(parse_sse(["data: 1"])) == [SseEvent("message", 1)]


def test_chat_yields_tokens_and_sends_history() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, text=SSE_BODY, headers={"content-type": "text/event-stream"})

    pieces = list(_client(httpx.MockTransport(handler)).chat("m", [{"role": "user", "content": "hi"}]))
    assert pieces == ["Hel", "lo"]
    assert seen == {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def test_chat_error_event_raises() -> None:
    body = 'event: error\ndata: {"message":"boom"}\n\n'
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    with pytest.raises(RuntimeError, match="boom"):
        list(client.chat("m", [{"role": "user", "content": "hi"}]))


def test_runs_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert json.loads(request.content)["end_time_s"] == 86400
            return httpx.Response(201, json={"id": "abc"})
        return httpx.Response(200, json={"id": "abc", "status": "running"})

    client = _client(httpx.MockTransport(handler))
    assert client.create_run("a: 1", 86400) == "abc"
    assert client.get_run("abc")["status"] == "running"


def test_run_status_markdown() -> None:
    text = run_status_markdown(
        {"id": "abc", "status": "succeeded", "duration_s": 26.1, "result_path": "/runs/x.mat"}, 30
    )
    assert "succeeded" in text and "26.1 s" in text and "/runs/x.mat" in text


def test_ui_builds() -> None:
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    assert build_ui(client, Settings()) is not None


def test_gradio_history_becomes_plain_messages() -> None:
    import gradio as gr

    chatbot = gr.Chatbot()
    history = chatbot.preprocess(
        chatbot.postprocess([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    )
    assert to_api_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
