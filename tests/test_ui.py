"""ui logic without a browser: SSE parsing, the api client, the card, the KPI table, the event view."""

import json
from typing import Any

import httpx

from llmtrano.settings import Settings
from llmtrano.ui.app import (
    MAX_OPTIONS,
    ApiClient,
    SseEvent,
    View,
    build_ui,
    card_html,
    kpi_markdown,
    option_labels,
    parse_sse,
)

CARD = {
    "rows": [
        {"field": "building_type", "value": "Detached", "source": "user", "evidence": "a detached house",
         "basis": None, "conflict": False},
        {"field": "room_height_m", "value": "2.6", "source": "default", "evidence": None,
         "basis": "trano reference house", "conflict": False},
        {"field": "heated_floor_area_m2", "value": "120", "source": "user", "evidence": "120 <m2>",
         "basis": None, "conflict": True},
        {"field": "glazing", "value": None, "source": None, "evidence": None, "basis": None,
         "conflict": False},
    ],
    "assumptions": [],
    "missing_required": ["storeys"],
    "conflicts": [{"field": "heated_floor_area_m2", "current": 120.0, "proposed": 135.0}],
    "ready": False,
}  # fmt: skip
QUESTIONS = [
    {"field": "storeys", "kind": "missing", "prompt_seed": "How many?", "options": [
        {"value": "1", "label": "1"}, {"value": "2", "label": "2"}, {"value": "3", "label": "3"}]},
    {"field": "glazing", "kind": "missing", "prompt_seed": "Glazing?", "options": [
        {"value": "triple", "label": "Triple glazing"}, {"value": "unknown", "label": "I don't know"}]},
]  # fmt: skip


def _sse(*events: tuple[str, Any]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def test_parse_sse() -> None:
    lines = ["event: token", 'data: {"text": "Hi"}', "", "event: phase", 'data: {"phase": "intake"}', ""]
    assert list(parse_sse(lines)) == [
        SseEvent("token", {"text": "Hi"}),
        SseEvent("phase", {"phase": "intake"}),
    ]


def test_parse_sse_last_event_without_blank_line() -> None:
    assert list(parse_sse(["event: done", "data: {}"])) == [SseEvent("done", {})]


def test_card_html_badges_hover_conflicts_and_escaping() -> None:
    text = card_html(CARD)
    assert '<td>Building type</td><td>Detached</td><td><span title="a detached house"' in text
    assert "badge-you-said" in text and "badge-assumed" in text
    assert 'title="trano reference house"' in text
    assert "120 &lt;m2&gt;" in text  # the user's words are escaped
    assert 'style="background:#fde2e2"' in text  # the conflicted row is highlighted
    assert "Glazing" not in text  # unknown values are not shown
    assert "Still needed:</strong> Storeys" in text
    assert "Two answers for Heated floor area (m2): 120.0 or 135.0?" in text
    assert card_html(None) == "<p><em>No description yet.</em></p>"


def test_kpi_table_is_drawn_by_code() -> None:
    table = kpi_markdown(
        {"heating_energy_kwh": 6469.2, "heating_energy_kwh_per_m2": 53.91, "peak_heating_w": 4335.0,
         "heated_floor_area_m2": 120.0}
    )  # fmt: skip
    assert "| Heating energy | 6,469 kWh |" in table
    assert "| Per m2 of heated floor area | 53.9 kWh/m2 |" in table
    assert "| Peak heating power (hourly mean) | 4,335 W |" in table
    assert kpi_markdown(None) == ""


def test_view_streams_a_reply_and_its_closing_events() -> None:
    view, reply = View(), {}
    for event in [
        SseEvent("token", {"text": "Hel", "stream": "reply"}),
        SseEvent("token", {"text": "lo", "stream": "reply"}),
        SseEvent("message", {"id": "m-1", "stream": "reply"}),
        SseEvent("brief", CARD),
        SseEvent("questions", QUESTIONS),
        SseEvent("phase", {"phase": "intake"}),
    ]:
        view.apply(event, reply)
    assert view.history == [{"role": "assistant", "content": "Hello"}]
    assert view.message_ids == {0: "m-1"}
    assert view.card == CARD and view.phase == "intake"
    assert view.option(1) == {"field": "storeys", "value": "2", "label": "2"}
    assert view.option(MAX_OPTIONS + 1) == {"field": "glazing", "value": "unknown", "label": "I don't know"}
    assert view.option(5) is None


def test_view_interpretation_retry_replaces_the_text() -> None:
    view, reply = View(), {}
    for event in [
        SseEvent("run_phase", {"phase": "simulating", "kind": "simulate"}),
        SseEvent("kpis", {"kpis": {"heating_energy_kwh": 1.0}}),
        SseEvent("token", {"text": "It needs 7000 kWh.", "stream": "interpretation", "attempt": 1}),
        SseEvent("guardrail", {"action": "retry", "wrong": ["7000"]}),
        SseEvent("token", {"text": "It needs 1 kWh.", "stream": "interpretation", "attempt": 2}),
        SseEvent("explanation", {"text": "It needs 1 kWh.", "message_id": "m-9"}),
    ]:
        view.apply(event, reply)
    assert view.history == [{"role": "assistant", "content": "It needs 1 kWh."}]
    assert view.message_ids == {0: "m-9"}
    assert view.kpis == {"heating_energy_kwh": 1.0}
    assert view.progress == ["simulate: simulating", "Number check: 7000 not in the results, rewritten."]


def test_option_labels_fill_the_button_pool() -> None:
    view = View(questions=QUESTIONS)
    (title1, labels1), (title2, labels2) = option_labels(view)
    assert (title1, labels1[:4]) == ("Storeys", ["1", "2", "3", None])
    assert (title2, labels2[:3]) == ("Glazing", ["Triple glazing", "I don't know", None])
    assert all(len(labels) == MAX_OPTIONS for _, labels in option_labels(view))
    assert option_labels(View()) == [(None, [None] * MAX_OPTIONS)] * 2


def test_api_client_round_trip() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/sessions":
            return httpx.Response(201, json={"id": "s-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, text=_sse(("token", {"text": "Hi"}), ("phase", {"phase": "intake"})))
        if request.url.path.endswith("/confirm"):
            return httpx.Response(200, text=_sse(("explanation", {"text": "done"})))
        return httpx.Response(201, json={"status": "ok"})

    api = ApiClient("http://api", transport=httpx.MockTransport(handler))
    assert api.create_session() == "s-1"
    events = list(api.send("s-1", message="hello", model="qwen3.5-9b"))
    assert [e.event for e in events] == ["token", "phase"]
    list(api.send("s-1", answer={"field": "storeys", "value": "2", "label": "2"}, model=None))
    assert [e.data for e in api.confirm("s-1")] == [{"text": "done"}]
    api.feedback("m-1", -1)
    assert seen[1] == ("POST", "/sessions/s-1/messages", {"message": "hello", "model": "qwen3.5-9b"})
    assert seen[2][2] == {"answer": {"field": "storeys", "value": "2", "label": "2"}}
    assert seen[3] == ("POST", "/sessions/s-1/confirm", {"approved": True})
    assert seen[4] == ("POST", "/feedback", {"message_id": "m-1", "thumbs": -1})


def test_ui_builds() -> None:
    api = ApiClient("http://api", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert build_ui(api, Settings()) is not None


# ---------- phase 2: the renovation column, two scenarios and the split


def test_card_renovation_column() -> None:
    glazing = {
        "field": "glazing",
        "value": "Old double glazing",
        "source": "user",
        "evidence": "double",
        "basis": None,
        "conflict": False,
        "renovation": "Triple glazing",
        "renovation_evidence": "we want <triple>",
    }
    storeys = {**glazing, "field": "storeys", "value": "2", "renovation": None, "renovation_evidence": None}
    card = {
        "rows": [glazing, storeys],
        "assumptions": [],
        "missing_required": [],
        "conflicts": [],
        "ready": True,
    }
    text = card_html(card)
    assert "<th>After renovation</th>" in text
    assert 'title="we want &lt;triple&gt;" class="badge-renovation">Triple glazing' in text
    assert text.count("<td></td>") == 1  # storeys: no change
    assert "After renovation" not in card_html(CARD)  # no plans: no column


BASELINE_KPIS = {
    "heating_energy_kwh": 9000.0,
    "heating_energy_kwh_per_m2": 75.0,
    "peak_heating_w": 6000.0,
    "heated_floor_area_m2": 120.0,
    "energy": {"fuel": "gas", "final_energy_kwh": 11000.0, "cost_eur": 1100, "co2_kg": 2222, "note": None},
    "heat_loss_split": {
        "share_percent": {"walls": 40, "air": 10},
        "estimated_kwh": {"walls": 3600, "air": 900},
    },
}


def test_kpi_table_with_two_scenarios_cost_and_split() -> None:
    renovation = {
        **BASELINE_KPIS,
        "heating_energy_kwh": 4000.0,
        "heating_energy_kwh_per_m2": 33.3,
        "energy": {"fuel": "gas", "final_energy_kwh": 4900.0, "cost_eur": 490, "co2_kg": 990},
    }
    comparison = {
        "heating_energy_kwh": {
            "baseline": 9000.0,
            "renovation": 4000.0,
            "saving": 5000.0,
            "saving_percent": 56,
        },
        "cost_eur": {"baseline": 1100, "renovation": 490, "saving": 610, "saving_percent": 55},
    }
    table = kpi_markdown(BASELINE_KPIS, renovation, comparison)
    assert "| Result (one year, Uccle weather) | House as it is | After the renovation | Saving |" in table
    assert "| Heating energy | 9,000 kWh | 4,000 kWh | 5,000 kWh (56%) |" in table
    assert "| Final energy (gas) | 11,000 kWh | 4,900 kWh | - |" in table  # no comparison row given
    assert (
        "| Energy cost (at the prices assumed) | 1,100 EUR/year | 490 EUR/year | 610 EUR/year (55%) |"
        in table
    )
    assert "| Walls | 40% | 3,600 |" in table and "| Air | 10% | 900 |" in table
    single = kpi_markdown(BASELINE_KPIS)
    assert (
        "After the renovation" not in single and "| CO2 (at the factors assumed) | 2,222 kg/year |" in single
    )


def test_kpi_table_without_a_system_shows_the_note() -> None:
    kpis = {
        "heating_energy_kwh": 1.0,
        "heating_energy_kwh_per_m2": 1.0,
        "peak_heating_w": 1.0,
        "heated_floor_area_m2": 1.0,
        "energy": {"fuel": None, "note": "no heating system stated"},
    }
    table = kpi_markdown(kpis)
    assert "_no heating system stated_" in table and "Final energy" not in table


def test_view_keeps_both_scenarios_and_the_comparison() -> None:
    view, reply = View(), {}
    for event in [
        SseEvent("run_phase", {"phase": "simulating", "kind": "simulate", "scenario": "baseline"}),
        SseEvent("kpis", {"kpis": {"heating_energy_kwh": 9.0}, "scenario": "baseline"}),
        SseEvent("run_phase", {"phase": "simulating", "kind": "simulate", "scenario": "renovation"}),
        SseEvent("kpis", {"kpis": {"heating_energy_kwh": 4.0}, "scenario": "renovation"}),
        SseEvent("comparison", {"comparison": {"heating_energy_kwh": {"saving": 5.0}}}),
    ]:
        view.apply(event, reply)
    assert (view.kpis, view.renovation_kpis) == ({"heating_energy_kwh": 9.0}, {"heating_energy_kwh": 4.0})
    assert view.comparison == {"heating_energy_kwh": {"saving": 5.0}}
    assert view.progress == ["simulate: simulating", "simulate (renovation): simulating"]
    view.apply(SseEvent("kpis", {"kpis": {"heating_energy_kwh": 8.0}, "scenario": "baseline"}), reply)
    assert view.renovation_kpis is None and view.comparison is None  # a new baseline clears the rest
