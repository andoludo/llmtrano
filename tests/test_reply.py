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


def test_interpret_input_with_a_system_a_split_and_a_renovation() -> None:
    kpis = {
        "heating_energy_kwh": 9000.0,
        "heating_energy_kwh_per_m2": 75.0,
        "peak_heating_w": 6000.0,
        "heated_floor_area_m2": 120.0,
        "zones": [],
        "system": {"kind": "without_storage", "fuel": "gas", "seasonal_efficiency": 0.8123},
        "energy": {
            "fuel": "gas",
            "final_energy_kwh": 11000.0,
            "auxiliary_electricity_kwh": 20.0,
            "cost_eur": 1100,
            "co2_kg": 2222,
            "note": None,
            "prices": {"gas_eur_per_kwh": 0.1, "electricity_eur_per_kwh": 0.35},
            "emission_factors": {"gas_kg_per_kwh": 0.202, "electricity_kg_per_kwh": 0.2},
        },
        "heat_loss_split": {
            "share_percent": {"walls": 40, "windows": 30},
            "estimated_kwh": {"walls": 3600, "windows": 2700},
        },
        "sizing": {
            "design_heat_loss_w": 8000,
            "heater_capacity_w": 11500.0,
            "generator": {"nominal_power_w": 12000.0},
        },
    }
    renovation = {**kpis, "heating_energy_kwh": 4000.0, "heating_energy_kwh_per_m2": 33.3}
    comparison = {
        "heating_energy_kwh": {
            "baseline": 9000.0,
            "renovation": 4000.0,
            "saving": 5000.0,
            "saving_percent": 56,
        }
    }
    text = interpret_input(
        kpis=kpis,
        brief=HouseBrief(storeys=2),
        provenance={},
        assumptions=["weather: Uccle"],
        language="English",
        renovation_kpis=renovation,
        renovation_changes=[("glazing", "Old double glazing", "Triple glazing")],
        comparison=comparison,
    )
    assert '"heating_system": {"kind": "without_storage", "uses": "gas", "final_energy_kwh": 11000.0' in text
    assert '"heat_per_unit_of_fuel": 0.8' in text and '"cost_eur_per_year": 1100' in text
    assert '"share_percent": {"walls": 40, "windows": 30}' in text
    assert '"generator_kw": 12.0' in text
    assert "Renovation changes:\n- glazing: Old double glazing -> Triple glazing" in text
    assert "Renovation results (the house after the renovation, rounded):" in text
    assert '"saving_percent": 56' in text
    assert "zones" not in text and text.endswith("Assumptions:\n- weather: Uccle")


def test_interpret_input_without_a_system_carries_the_note() -> None:
    kpis = {
        "heating_energy_kwh": 1.0,
        "heating_energy_kwh_per_m2": 1.0,
        "peak_heating_w": 1.0,
        "system": {"kind": "ideal", "fuel": None},
        "energy": {"fuel": None, "note": "no heating system stated"},
    }
    text = interpret_input(kpis=kpis, brief=HouseBrief(), provenance={}, assumptions=[], language="Dutch")
    assert '"heating_system": {"kind": "none stated", "note": "no heating system stated"}' in text
    assert "Renovation" not in text
