"""Gradio Blocks ui (phase 0 skeleton), served on port 7860. Run with ``python -m llmtrano.ui.app``.

- a model dropdown filled from ``GET /models``;
- a chat streaming from ``POST /chat`` (SSE);
- a "Simulate tutorial house" button posting to ``POST /runs`` and polling ``GET /runs/{id}`` every 2 s.
"""

import json
import logging
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from llmtrano.data import tutorial_house_yaml
from llmtrano.logs import configure_logging
from llmtrano.settings import Settings

# Gradio sends usage analytics by default; this app is local-first. Set before importing gradio.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
import gradio as gr

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0
DAY_S = 86_400
PERIODS = {"1 day": DAY_S, "1 year": 365 * DAY_S}
Message = dict[str, str]


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: Any


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Server-Sent Events from text lines: ``event:`` and ``data:`` fields, a blank line ends an event."""
    event, data = "message", list[str]()
    for line in lines:
        if line == "":
            if data:
                yield SseEvent(event, json.loads("\n".join(data)))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").strip())
    if data:
        yield SseEvent(event, json.loads("\n".join(data)))


def to_api_messages(history: Iterable[dict[str, Any]]) -> list[Message]:
    """Gradio chat history as the api's ``{"role", "content": str}`` messages.

    Gradio 6 stores each message's content as a list of parts (``[{"type": "text", "text": ...}]``) with extra
    ``metadata`` and ``options`` keys; the api accepts plain text only.
    """
    messages = []
    for message in history:
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if isinstance(content, str) and content:
            messages.append({"role": str(message["role"]), "content": content})
    return messages


class ApiClient:
    """The ui's only way out: HTTP calls to the api."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(30.0, read=300.0), transport=transport
        )

    def models(self) -> list[str]:
        response = self.http.get("/models")
        response.raise_for_status()
        return [str(m) for m in response.json()]

    def chat(self, model: str, messages: list[Message]) -> Iterator[str]:
        """The reply's text pieces as they stream in."""
        with self.http.stream("POST", "/chat", json={"model": model, "messages": messages}) as response:
            response.raise_for_status()
            for event in parse_sse(response.iter_lines()):
                if event.event == "token":
                    yield str(event.data["text"])
                elif event.event == "error":
                    raise RuntimeError(event.data.get("message", "LLM error"))

    def create_run(self, yaml_text: str, end_time_s: float) -> str:
        response = self.http.post("/runs", json={"yaml_text": yaml_text, "end_time_s": end_time_s})
        response.raise_for_status()
        return str(response.json()["id"])

    def get_run(self, run_id: str) -> dict[str, Any]:
        response = self.http.get(f"/runs/{run_id}")
        response.raise_for_status()
        run: dict[str, Any] = response.json()
        return run


def run_status_markdown(run: dict[str, Any], waited_s: float) -> str:
    lines = [f"**Run** `{run['id']}`", f"**Status:** {run['status']} (waited {waited_s:.0f} s)"]
    if run.get("duration_s") is not None:
        lines.append(f"**Simulation duration:** {run['duration_s']:.1f} s")
    if run.get("result_path"):
        lines.append(f"**Result file:** `{run['result_path']}`")
    if run.get("error_kind"):
        lines.append(f"**Error ({run['error_kind']}):** {run.get('error_message') or ''}")
    return "  \n".join(lines)


def build_ui(api: ApiClient, settings: Settings) -> gr.Blocks:
    def load_models() -> Any:
        try:
            ids = api.models()
        except httpx.HTTPError as error:
            log.warning("models unavailable error=%r", error)
            return gr.update(choices=[], value=None, label="Model (LLM unavailable)")
        default = (
            settings.llm_default_model if settings.llm_default_model in ids else (ids[0] if ids else None)
        )
        return gr.update(choices=ids, value=default, label="Model")

    def respond(
        message: str, history: list[Message], model: str | None
    ) -> Iterator[tuple[list[Message], str]]:
        if not message.strip():
            yield history, ""
            return
        if not model:
            yield (
                [*history, {"role": "assistant", "content": "No model available: is the llm running?"}],
                message,
            )
            return
        messages = [*to_api_messages(history), {"role": "user", "content": message}]
        reply = ""
        yield [*messages, {"role": "assistant", "content": reply}], ""
        try:
            for piece in api.chat(model, messages):
                reply += piece
                yield [*messages, {"role": "assistant", "content": reply}], ""
        except (httpx.HTTPError, RuntimeError) as error:
            log.warning("chat failed model=%s error=%r", model, error)
            yield [*messages, {"role": "assistant", "content": f"{reply}\n\n[error: {error}]"}], ""

    def simulate(period: str) -> Iterator[str]:
        try:
            run_id = api.create_run(tutorial_house_yaml(), PERIODS[period])
        except httpx.HTTPError as error:
            yield f"Could not queue the run: {error}"
            return
        started = time.monotonic()
        deadline = started + settings.simulation_timeout_s + 120
        while time.monotonic() < deadline:
            try:
                run = api.get_run(run_id)
            except httpx.HTTPError as error:
                yield f"Could not read run {run_id}: {error}"
                return
            yield run_status_markdown(run, time.monotonic() - started)
            if run["status"] in {"succeeded", "failed"}:
                return
            time.sleep(POLL_INTERVAL_S)
        yield f"Run {run_id} still not finished after {time.monotonic() - started:.0f} s."

    ui = gr.Blocks(title="llmtrano")
    with ui:
        gr.Markdown("# llmtrano\nPhase 0 skeleton: chat with a local model, simulate trano's tutorial house.")
        with gr.Row():
            with gr.Column(scale=3):
                model = gr.Dropdown(label="Model", choices=[], interactive=True)
                chatbot = gr.Chatbot(label="Chat", height=420)
                with gr.Row():
                    box = gr.Textbox(placeholder="Ask something", show_label=False, scale=5)
                    send = gr.Button("Send", variant="primary", scale=1)
                gr.ClearButton([chatbot, box], value="Clear")
            with gr.Column(scale=2):
                gr.Markdown("### Simulation")
                period = gr.Radio(list(PERIODS), value="1 day", label="Period")
                run_button = gr.Button("Simulate tutorial house")
                run_status = gr.Markdown("No run yet.")

        ui.load(load_models, outputs=model)
        box.submit(respond, [box, chatbot, model], [chatbot, box])
        send.click(respond, [box, chatbot, model], [chatbot, box])
        run_button.click(simulate, period, run_status)
    return ui


def main() -> None:
    configure_logging()
    settings = Settings()
    log.info("ui start api_url=%s", settings.api_url)
    build_ui(ApiClient(settings.api_url), settings).launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
    main()
