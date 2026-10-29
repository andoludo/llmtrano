"""The renovation overlay, the card's renovation column and the scenario comparison (phase 2)."""

from typing import Any

from llmtrano.brief.card import build_card
from llmtrano.brief.model import BuildingType, Fact, FieldProvenance, Glazing, HouseBrief, Period, Source
from llmtrano.brief.overlay import renovation_brief, renovation_changes, renovation_facts
from llmtrano.kpi.compare import compare

BASELINE = HouseBrief(
    building_type=BuildingType.detached,
    construction_period=Period.y1971_1990,
    heated_floor_area_m2=140,
    storeys=2,
    glazing=Glazing.double_old,
)
PROVENANCE = {
    name: FieldProvenance(source=Source.user, evidence="said", turn=1)
    for name in ("building_type", "construction_period", "heated_floor_area_m2", "storeys", "glazing")
}


def _fact(field: str, value: Any, turn: int = 2, scenario: str = "renovation") -> Fact:
    return Fact.model_validate(
        {
            "field": field,
            "value": value,
            "raw_value": str(value),
            "scenario": scenario,
            "evidence": f"we plan {value}",
            "confidence": "high",
            "turn": turn,
        }
    )


def test_last_renovation_fact_per_field_wins_and_unknown_is_ignored() -> None:
    facts = [
        _fact("glazing", "double_low_e", turn=2),
        _fact("glazing", "triple", turn=3),
        _fact("wall_insulation", "unknown"),
    ]
    chosen = renovation_facts(facts)
    assert {name: fact.value for name, fact in chosen.items()} == {"glazing": "triple"}


def test_overlay_replaces_fields_and_marks_their_source() -> None:
    overlay = renovation_brief(
        BASELINE, PROVENANCE, [_fact("glazing", "triple"), _fact("wall_insulation", "good")]
    )
    assert overlay is not None
    renovated, provenance = overlay
    assert renovated.glazing is Glazing.triple and renovated.wall_insulation.value == "good"
    assert renovated.heated_floor_area_m2 == 140  # the rest is the baseline
    assert (
        provenance["glazing"].source is Source.renovation
        and provenance["glazing"].evidence == "we plan triple"
    )
    assert provenance["storeys"].source is Source.user
    assert BASELINE.glazing is Glazing.double_old  # the baseline is untouched


def test_overlay_is_none_without_renovation_facts() -> None:
    assert renovation_brief(BASELINE, PROVENANCE, [_fact("glazing", "triple", scenario="baseline")]) is None
    assert renovation_brief(BASELINE, PROVENANCE, []) is None


def test_invalid_renovation_value_is_skipped() -> None:
    brief = BASELINE.model_copy(update={"day_start_h": 7, "day_end_h": 23})
    assert renovation_brief(brief, PROVENANCE, [_fact("day_end_h", 6)]) is None  # ends before it starts


def test_changes_are_listed_as_labels() -> None:
    overlay = renovation_brief(BASELINE, PROVENANCE, [_fact("glazing", "triple")])
    assert overlay is not None
    assert renovation_changes(BASELINE, overlay[0]) == [("glazing", "Old double glazing", "Triple glazing")]


def test_card_renovation_column_covers_defaulted_fields_too() -> None:
    card = build_card(
        BASELINE, PROVENANCE, [], ready=True, renovation_facts=[_fact("roof_insulation", "good")]
    )
    rows = {row.field: row for row in card.rows}
    assert rows["roof_insulation"].source is Source.default  # thin, assumed from the period
    assert rows["roof_insulation"].renovation == "Good (renovated or recent)"
    assert rows["glazing"].renovation is None
    assert card.renovation_fields == ["roof_insulation"]


def test_compare_gives_savings_and_skips_missing_metrics() -> None:
    baseline = {
        "heating_energy_kwh": 10000.0,
        "heating_energy_kwh_per_m2": 100.0,
        "peak_heating_w": 8000.0,
        "energy": {"fuel": "gas", "final_energy_kwh": 12000.0, "cost_eur": 1200, "co2_kg": 2400},
    }
    renovation = {
        "heating_energy_kwh": 4000.0,
        "heating_energy_kwh_per_m2": 40.0,
        "peak_heating_w": 3000.0,
        "energy": {"fuel": "electricity", "final_energy_kwh": 1500.0, "cost_eur": 525, "co2_kg": None},
    }
    comparison = compare(baseline, renovation)
    assert comparison["heating_energy_kwh"] == {
        "baseline": 10000.0,
        "renovation": 4000.0,
        "saving": 6000.0,
        "saving_percent": 60,
    }
    assert comparison["cost_eur"]["saving"] == 675
    assert "co2_kg" not in comparison  # missing on one side
    assert comparison["fuel"] == {"baseline": "gas", "renovation": "electricity"}
