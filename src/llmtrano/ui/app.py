"""Gradio Blocks ui, served on port 7860. Run with ``python -m llmtrano.ui.app``.

Two columns. Left: the chat (streamed), the answer buttons of the planner's questions and the model used for
the interactive turns. Right: the House Brief card ("you said" or "assumed", the user's words on hover,
conflicts highlighted, a renovation column when plans were described), the Confirm button (active only when
the Brief is ready), the run progress and the results table drawn by code: one column per scenario, the
saving, the cost and CO2 at the prices assumed, and the heat-loss split. Thumbs up or down under each answer
go to ``POST /feedback``.

The ui talks only to the api. Gradio cannot add components while it streams, so the answer buttons are a fixed
pool whose labels and visibility follow the current questions.
"""

import html
import json
import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from llmtrano.logs import configure_logging
from llmtrano.settings import Settings

# Gradio sends usage analytics by default; this app is local-first. Set before importing gradio.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
import gradio as gr

log = logging.getLogger(__name__)

MAX_QUESTIONS = 2
MAX_OPTIONS = 7
FIELD_LABELS = {
    "building_type": "Building type",
    "construction_period": "Construction period",
    "heated_floor_area_m2": "Heated floor area (m2)",
    "storeys": "Storeys",
    "zoning": "Zoning",
    "room_height_m": "Room height (m)",
    "street_facade_orientation": "Street facade faces",
    "main_window_orientation": "Most glass faces",
    "wall_insulation": "Wall insulation",
    "roof_insulation": "Roof insulation",
    "floor_insulation": "Floor insulation",
    "glazing": "Glazing",
    "window_to_wall_ratio": "Window-to-wall ratio",
    "airtightness": "Airtightness",
    "ventilation": "Ventilation",
    "heating_system": "Heating system",
    "heating_setpoint_day_c": "Day temperature (degC)",
    "heating_setpoint_night_c": "Night temperature (degC)",
    "day_start_h": "Day starts (h)",
    "day_end_h": "Day ends (h)",
    "occupants": "Occupants",
    "municipality": "Municipality",
}
PHASE_LABELS = {
    "intake": "Describing the house",
    "confirm": "Ready: check the card and confirm",
    "compiling": "Building the model",
    "simulating": "Simulating one year",
    "done": "Done",
    "failed": "Failed",
}


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


class ApiClient:
    """The ui's only way out: HTTP calls to the api."""

    def __init__(
        self, base_url: str, transport: httpx.BaseTransport | None = None, token: str | None = None
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(30.0, read=1800.0), transport=transport, headers=headers
        )

    def models(self) -> list[str]:
        response = self.http.get("/models")
        response.raise_for_status()
        return [str(m) for m in response.json()]

    def create_session(self) -> str:
        response = self.http.post("/sessions")
        response.raise_for_status()
        return str(response.json()["id"])

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self.http.get(f"/sessions/{session_id}")
        response.raise_for_status()
        state: dict[str, Any] = response.json()
        return state

    def _stream(self, path: str, body: dict[str, Any]) -> Iterator[SseEvent]:
        with self.http.stream("POST", path, json=body) as response:
            response.raise_for_status()
            yield from parse_sse(response.iter_lines())

    def send(
        self,
        session_id: str,
        *,
        message: str | None = None,
        answer: dict[str, str] | None = None,
        model: str | None,
    ) -> Iterator[SseEvent]:
        body: dict[str, Any] = {"message": message, "answer": answer, "model": model}
        return self._stream(
            f"/sessions/{session_id}/messages", {k: v for k, v in body.items() if v is not None}
        )

    def confirm(self, session_id: str) -> Iterator[SseEvent]:
        return self._stream(f"/sessions/{session_id}/confirm", {"approved": True})

    def feedback(self, message_id: str, thumbs: int) -> None:
        self.http.post("/feedback", json={"message_id": message_id, "thumbs": thumbs}).raise_for_status()


# ---------- what the ui shows, as pure functions of the api's data


def card_html(card: dict[str, Any] | None) -> str:
    """The Brief card as an HTML table: value, a source badge, the user's words or the basis on hover, and
    a renovation column when the user described plans (phase 2)."""
    if not card:
        return "<p><em>No description yet.</em></p>"
    renovated = any(row.get("renovation") for row in card["rows"])
    rows = []
    for row in card["rows"]:
        if row["value"] is None and not row.get("renovation"):
            continue
        if row["source"] == "user":
            badge, hover = "you said", row.get("evidence") or ""
        else:
            badge, hover = "assumed", row.get("basis") or ""
        style = ' style="background:#fde2e2"' if row.get("conflict") else ""
        name = html.escape(FIELD_LABELS.get(row["field"], row["field"]))
        badge_class = f"badge badge-{badge.replace(' ', '-')}"
        value = html.escape(str(row["value"])) if row["value"] is not None else "<em>not given</em>"
        after = ""
        if renovated:
            plan = row.get("renovation")
            evidence = html.escape(row.get("renovation_evidence") or "", quote=True)
            after = (
                f'<td><span title="{evidence}" class="badge-renovation">{html.escape(plan)}</span></td>'
                if (plan)
                else "<td></td>"
            )
        rows.append(
            f"<tr{style}><td>{name}</td><td>{value}</td>"
            f'<td><span title="{html.escape(hover, quote=True)}" class="{badge_class}">{badge}</span>'
            f"</td>{after}</tr>"
        )
    missing = card.get("missing_required") or []
    needed = ", ".join(FIELD_LABELS.get(name, name) for name in missing)
    note = f"<p><strong>Still needed:</strong> {html.escape(needed)}</p>" if missing else ""
    conflicts = "".join(
        f"<p style='color:#a40000'>Two answers for {html.escape(FIELD_LABELS.get(c['field'], c['field']))}: "
        f"{html.escape(str(c['current']))} or {html.escape(str(c['proposed']))}?</p>"
        for c in card.get("conflicts") or []
    )
    header = "<th>Field</th><th>Value</th><th>Source</th>" + (
        "<th>After renovation</th>" if renovated else ""
    )
    table = f"<table><thead><tr>{header}</tr></thead><tbody>"
    return f"{table}{''.join(rows)}</tbody></table>{note}{conflicts}"


def _fmt(value: Any, unit: str, digits: int = 0) -> str:
    return "-" if value is None else f"{value:,.{digits}f} {unit}"


def kpi_markdown(
    kpis: dict[str, Any] | None,
    renovation_kpis: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
) -> str:
    """The results table drawn by code: one column per scenario and the saving, then the heat-loss split."""
    if not kpis:
        return ""
    energy = kpis.get("energy") or {}
    renovation_energy = (renovation_kpis or {}).get("energy") or {}
    fuel = energy.get("fuel") or renovation_energy.get("fuel")
    lines_spec: list[tuple[str, str, str, int]] = [
        ("Heating energy", "heating_energy_kwh", "kWh", 0),
        ("Per m2 of heated floor area", "heating_energy_kwh_per_m2", "kWh/m2", 1),
        ("Peak heating power (hourly mean)", "peak_heating_w", "W", 0),
    ]
    if fuel:
        lines_spec += [
            (f"Final energy ({fuel})", "energy.final_energy_kwh", "kWh", 0),
            ("Energy cost (at the prices assumed)", "energy.cost_eur", "EUR/year", 0),
            ("CO2 (at the factors assumed)", "energy.co2_kg", "kg/year", 0),
        ]

    def pick(source: dict[str, Any] | None, key: str) -> Any:
        value: Any = source or {}
        for part in key.split("."):
            value = (value or {}).get(part)
        return value

    header = "| Result (one year, Uccle weather) | House as it is |"
    rule = "| --- | --- |"
    if renovation_kpis:
        header += " After the renovation | Saving |"
        rule += " --- | --- |"
    lines = [header, rule]
    for label, key, unit, digits in lines_spec:
        line = f"| {label} | {_fmt(pick(kpis, key), unit, digits)} |"
        if renovation_kpis:
            saving = (comparison or {}).get(key.split(".")[-1]) or {}
            saved = _fmt(saving.get("saving"), unit, digits)
            percent = f" ({saving['saving_percent']}%)" if saving.get("saving_percent") is not None else ""
            line += f" {_fmt(pick(renovation_kpis, key), unit, digits)} | {saved}{percent} |"
        lines.append(line)
    lines.append(
        f"| Heated floor area | {kpis['heated_floor_area_m2']:.0f} m2 |" + (" | |" if renovation_kpis else "")
    )
    if energy.get("note"):
        lines += ["", f"_{energy['note']}_"]
    split = kpis.get("heat_loss_split") or {}
    if split.get("share_percent"):
        lines += [
            "",
            "| Where the heat goes (estimate) | Share | kWh/year |",
            "| --- | --- | --- |",
            *(
                f"| {name.capitalize()} | {share}% | {split['estimated_kwh'][name]:,} |"
                for name, share in split["share_percent"].items()
            ),
        ]
    return "\n".join(lines)


@dataclass
class View:
    """The ui state of one browser session (kept in a ``gr.State``)."""

    session_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    card: dict[str, Any] | None = None
    questions: list[dict[str, Any]] = field(default_factory=list)
    phase: str = "intake"
    progress: list[str] = field(default_factory=list)
    kpis: dict[str, Any] | None = None
    renovation_kpis: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    message_ids: dict[int, str] = field(default_factory=dict)  # history index -> stored message id

    def assistant(self, text: str = "") -> int:
        self.history.append({"role": "assistant", "content": text})
        return len(self.history) - 1

    def apply(self, event: SseEvent, reply: dict[str, int]) -> None:
        """One api event into the view; ``reply`` tracks the index of the assistant message being streamed."""
        data = event.data
        match event.event:
            case "token":
                key, attempt = data.get("stream", "reply"), data.get("attempt", 1)
                if key not in reply:
                    reply[key] = self.assistant()
                if reply.get(f"{key}_attempt", attempt) != attempt:  # a retry replaces the first text
                    self.history[reply[key]]["content"] = ""
                reply[f"{key}_attempt"] = attempt
                self.history[reply[key]]["content"] += data["text"]
            case "message" if data.get("id") and "reply" in reply:
                self.message_ids[reply["reply"]] = data["id"]
            case "explanation":
                if "interpretation" not in reply:
                    reply["interpretation"] = self.assistant()
                index = reply["interpretation"]
                self.history[index]["content"] = data["text"]
                if data.get("message_id"):
                    self.message_ids[index] = data["message_id"]
            case "guardrail":
                action = "rewritten" if data["action"] == "retry" else "replaced by the results"
                self.progress.append(
                    f"Number check: {', '.join(data['wrong'])} not in the results, {action}."
                )
            case "run_phase":
                scenario = f" ({data['scenario']})" if data.get("scenario") == "renovation" else ""
                self.progress.append(f"{data.get('kind', 'run')}{scenario}: {data['phase']}")
            case "kpis" if data.get("scenario") == "renovation":
                self.renovation_kpis = data["kpis"]
            case "kpis":
                self.kpis, self.renovation_kpis, self.comparison = data["kpis"], None, None
            case "comparison":
                self.comparison = data["comparison"]
            case "brief":
                self.card = data
            case "questions":
                self.questions = data[:MAX_QUESTIONS]
            case "phase":
                self.phase = data["phase"]
            case "error":
                self.assistant(f"[error] {data.get('text', '')}")

    def option(self, index: int) -> dict[str, str] | None:
        """The answer of button ``index`` of the pool (question by question, MAX_OPTIONS each)."""
        question, option = divmod(index, MAX_OPTIONS)
        if question >= len(self.questions) or option >= len(self.questions[question]["options"]):
            return None
        chosen = self.questions[question]["options"][option]
        return {
            "field": self.questions[question]["field"],
            "value": chosen["value"],
            "label": chosen["label"],
        }


def option_labels(view: View) -> list[tuple[str | None, list[str | None]]]:
    """For each question slot, its title and the labels of its buttons (None: hidden)."""
    slots: list[tuple[str | None, list[str | None]]] = []
    for slot in range(MAX_QUESTIONS):
        if slot < len(view.questions) and view.questions[slot]["options"]:
            question = view.questions[slot]
            labels: list[str | None] = [o["label"] for o in question["options"][:MAX_OPTIONS]]
            slots.append((FIELD_LABELS.get(question["field"], question["field"]), labels))
        else:
            slots.append((None, []))
    return [(title, labels + [None] * (MAX_OPTIONS - len(labels))) for title, labels in slots]


def build_ui(api: ApiClient, settings: Settings) -> gr.Blocks:
    def outputs(view: View, box: str = "") -> tuple[Any, ...]:
        buttons: list[Any] = []
        titles: list[Any] = []
        for title, labels in option_labels(view):
            titles.append(gr.update(value=f"**{title}**" if title else "", visible=title is not None))
            buttons += [gr.update(value=label or "", visible=label is not None) for label in labels]
        phase = PHASE_LABELS.get(view.phase, view.phase)
        return (
            view.history,
            card_html(view.card),
            f"**{phase}**"
            + ("\n\n" + "\n".join(f"- {line}" for line in view.progress[-8:]) if view.progress else ""),
            kpi_markdown(view.kpis, view.renovation_kpis, view.comparison),
            gr.update(interactive=view.phase == "confirm"),
            *titles,
            *buttons,
            view,
            box,
        )

    def start() -> tuple[Any, ...]:
        view = View()
        try:
            view.session_id = api.create_session()
            state = api.get_session(view.session_id)
            view.card = state["card"]
            view.kpis, view.renovation_kpis = state.get("kpis"), state.get("renovation_kpis")
            view.comparison = state.get("comparison")
        except httpx.HTTPError as error:
            log.warning("session unavailable error=%r", error)
            view.assistant(f"[error] the api is not reachable: {error}")
        try:
            ids = api.models()
            default = (
                settings.llm_default_model if settings.llm_default_model in ids else (ids[0] if ids else None)
            )
            model = gr.update(choices=ids, value=default, label="Model for the conversation")
        except httpx.HTTPError:
            model = gr.update(choices=[], value=None, label="Model (LLM unavailable)")
        return (*outputs(view), model)

    def stream(view: View, events: Iterator[SseEvent]) -> Iterator[tuple[Any, ...]]:
        reply: dict[str, int] = {}
        try:
            for event in events:
                view.apply(event, reply)
                yield outputs(view)
        except httpx.HTTPError as error:
            log.warning("api error session=%s error=%r", view.session_id, error)
            view.assistant(f"[error] {error}")
            yield outputs(view)

    def send(message: str, view: View, model: str | None) -> Iterator[tuple[Any, ...]]:
        if not message.strip() or view.session_id is None:
            yield outputs(view, message)
            return
        view.history.append({"role": "user", "content": message})
        view.progress = []
        yield outputs(view)
        yield from stream(view, api.send(view.session_id, message=message, model=model))

    def answer(index: int) -> Any:
        def click(view: View, model: str | None) -> Iterator[tuple[Any, ...]]:
            chosen = view.option(index)
            if chosen is None or view.session_id is None:
                yield outputs(view)
                return
            view.history.append({"role": "user", "content": chosen["label"]})
            yield outputs(view)
            yield from stream(view, api.send(view.session_id, answer=chosen, model=model))

        return click

    def confirm(view: View) -> Iterator[tuple[Any, ...]]:
        if view.session_id is None or view.phase != "confirm":
            yield outputs(view)
            return
        view.phase, view.progress = "compiling", ["Confirmed: building and simulating the house"]
        yield outputs(view)
        yield from stream(view, api.confirm(view.session_id))

    def like(view: View, data: gr.LikeData) -> None:
        index = data.index if isinstance(data.index, int) else data.index[0]
        message_id = view.message_ids.get(index)
        if message_id is None:
            return
        try:
            api.feedback(message_id, 1 if data.liked is True else -1)
        except httpx.HTTPError as error:
            log.warning("feedback failed message=%s error=%r", message_id, error)

    ui = gr.Blocks(title="llmtrano")
    with ui:
        gr.Markdown(
            "# llmtrano\nDescribe your house; confirm the card; get its yearly heating need explained."
        )
        view = gr.State(View())
        with gr.Row():
            with gr.Column(scale=3):
                model = gr.Dropdown(label="Model for the conversation", choices=[], interactive=True)
                # one bubble per message: each answer gets its own thumbs up or down
                chatbot = gr.Chatbot(label="Chat", height=460, group_consecutive_messages=False)
                titles, buttons = [], []
                for _ in range(MAX_QUESTIONS):
                    titles.append(gr.Markdown(visible=False))
                    with gr.Row():
                        buttons += [gr.Button(visible=False, size="sm") for _ in range(MAX_OPTIONS)]
                with gr.Row():
                    box = gr.Textbox(placeholder="Describe your house", show_label=False, scale=5)
                    send_button = gr.Button("Send", variant="primary", scale=1)
            with gr.Column(scale=2):
                gr.Markdown("### House Brief")
                card = gr.HTML()
                confirm_button = gr.Button("Confirm and simulate", variant="primary", interactive=False)
                progress = gr.Markdown()
                kpis = gr.Markdown()

        shown = [chatbot, card, progress, kpis, confirm_button, *titles, *buttons, view, box]
        ui.load(start, outputs=[*shown, model])
        box.submit(send, [box, view, model], shown)
        send_button.click(send, [box, view, model], shown)
        for index, button in enumerate(buttons):
            button.click(answer(index), [view, model], shown)
        confirm_button.click(confirm, view, shown)
        chatbot.like(like, view, None)
    return ui


def main() -> None:
    configure_logging()
    settings = Settings()
    log.info("ui start api_url=%s", settings.api_url)
    api = ApiClient(settings.api_url, token=settings.api_token)
    build_ui(api, settings).launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
    main()
