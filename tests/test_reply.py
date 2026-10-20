"""Inputs of respond and interpret, and language detection."""

import pytest

from llmtrano.brief.model import BRIEF_FIELDS, Fact, FieldProvenance, Glazing, HouseBrief, Period, Source
from llmtrano.brief.plan import Option, Question
from llmtrano.llm.reply import FIELD_NAMES, brief_lines, detect_language, interpret_input, respond_input


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We live in a detached house built in 1985", "English"),
        ("Wij wonen in een rijwoning van 1962 met dubbel glas", "Dutch"),
        ("Nous avons une maison construite en 1970 avec double vitrage", "French"),
        ("120", "Dutch"),  # too short: keeps the current language
        ("ja", "Dutch"),
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text, current="Dutch" if expected == "Dutch" else "English") == expected


def _fact(field: str, value: object, scenario: str = "baseline") -> Fact:
    return Fact.model_validate(
        {"field": field, "value": value, "raw_value": str(value), "scenario": scenario, "evidence": "x",
         "confidence": "high", "turn": 1}
    )  # fmt: skip


def test_every_field_has_a_human_name() -> None:
    assert set(FIELD_NAMES) == set(BRIEF_FIELDS)


def test_respond_input() -> None:
    question = Question(
        field="storeys",
        kind="missing",
        prompt_seed="How many floors?",
        options=[Option(value="1", label="1")],
    )
    text = respond_input(
        noted=[_fact("construction_period", Period.y1971_1990), _fact("heated_floor_area_m2", 140.0)],
        renovation=[_fact("glazing", Glazing.triple, "renovation")],
        questions=[question],
        ready_to_confirm=False,
        language="English",
    )
    assert text == (
        "Language: English\n"
        "Noted this turn: construction period = 1971 to 1990; heated floor area (m2) = 140\n"
        "Renovation plans noted: glazing = Triple glazing\n"
        "Questions to ask:\n1. How many floors?"
    )


def test_respond_input_ready_and_unsupported() -> None:
    ready = respond_input(noted=[], renovation=[], questions=[], ready_to_confirm=True, language="Dutch")
    assert ready == "Language: Dutch\nReady to confirm: yes"
    unsupported = Question(
        field="building_type", kind="unsupported", prompt_seed="Apartments are not supported yet.", options=[]
    )
    text = respond_input(
        noted=[], renovation=[], questions=[unsupported], ready_to_confirm=False, language="English"
    )
    assert "Not supported: Apartments are not supported yet." in text
    assert "Questions to ask" not in text


def test_brief_lines_show_the_source() -> None:
    brief = HouseBrief(glazing=Glazing.double_old, room_height_m=2.6)
    provenance = {
        "glazing": FieldProvenance(source=Source.user, evidence="dubbel glas"),
        "room_height_m": FieldProvenance(source=Source.default, basis="trano reference house"),
    }
    assert brief_lines(brief, provenance) == [
        "- room height (m): 2.6 (assumed: trano reference house)",
        '- glazing: Old double glazing (you said "dubbel glas")',
    ]


def test_interpret_input_has_only_rounded_results_brief_and_assumptions() -> None:
    kpis = {
        "heating_energy_kwh": 6085.484434,
        "heating_energy_kwh_per_m2": 60.854844,
        "peak_heating_w": 3862.30469,
        "heated_floor_area_m2": 100.0,
        "zones": [],
    }
    text = interpret_input(
        kpis=kpis,
        brief=HouseBrief(storeys=1),
        provenance={},
        assumptions=["weather: Uccle"],
        language="English",
    )
    assert '"heating_energy_kwh": 6085.5' in text
    assert '"peak_heating_w": 3862.3' in text
    assert "zones" not in text
    assert text.endswith("Assumptions:\n- weather: Uccle")
