"""Merge and plan: table-driven unit tests."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from llmtrano.brief.merge import Conflict, merge_facts
from llmtrano.brief.model import (
    BriefField,
    BuildingType,
    Confidence,
    Fact,
    FieldProvenance,
    Glazing,
    HouseBrief,
    InsulationLevel,
    Period,
    Provenance,
    Scenario,
    Source,
)
from llmtrano.brief.plan import DONT_KNOW, next_questions, options_for, ready_to_confirm


def fact(
    name: BriefField,
    value: Any,
    *,
    scenario: Scenario = "baseline",
    confidence: Confidence = "high",
    turn: int = 2,
    evidence: str | None = None,
) -> Fact:
    return Fact(
        field=name,
        value=value,
        raw_value=str(value),
        scenario=scenario,
        evidence=evidence or f"said {value}",
        confidence=confidence,
        turn=turn,
    )


def user(evidence: str = "earlier", confidence: Confidence = "high", turn: int = 1) -> FieldProvenance:
    return FieldProvenance(source=Source.user, evidence=evidence, confidence=confidence, turn=turn)


DEFAULT = FieldProvenance(source=Source.default, basis="rule of thumb")


@dataclass
class MergeCase:
    name: str
    brief: dict[str, Any]
    provenance: Provenance
    facts: list[Fact]
    expected: dict[str, Any]
    conflicts: list[str] = field(default_factory=list)
    renovation: list[str] = field(default_factory=list)
    open_conflicts: list[Conflict] = field(default_factory=list)


MERGE_CASES = [
    MergeCase("new value applied", {}, {}, [fact("storeys", 2)], {"storeys": 2}),
    MergeCase(
        "user overrides default",
        {"room_height_m": 2.6},
        {"room_height_m": DEFAULT},
        [fact("room_height_m", 3.0)],
        {"room_height_m": 3.0},
    ),
    MergeCase(
        "conflict with earlier user value",
        {"heated_floor_area_m2": 120.0},
        {"heated_floor_area_m2": user("120 m2")},
        [fact("heated_floor_area_m2", 135.0)],
        {"heated_floor_area_m2": 120.0},
        conflicts=["heated_floor_area_m2"],
    ),
    MergeCase(
        "same value again is not a conflict",
        {"storeys": 2},
        {"storeys": user()},
        [fact("storeys", 2)],
        {"storeys": 2},
    ),
    MergeCase(
        "answer resolves an open conflict",
        {"heated_floor_area_m2": 120.0},
        {"heated_floor_area_m2": user()},
        [fact("heated_floor_area_m2", 135.0, turn=3)],
        {"heated_floor_area_m2": 135.0},
        open_conflicts=[
            Conflict(
                field="heated_floor_area_m2",
                current=120.0,
                proposed=135.0,
                current_evidence="120",
                proposed_evidence="135",
                turn=2,
            )
        ],
    ),
    MergeCase(
        "renovation facts are kept apart",
        {"glazing": Glazing.double_old},
        {"glazing": user()},
        [fact("glazing", Glazing.triple, scenario="renovation")],
        {"glazing": Glazing.double_old},
        renovation=["glazing"],
    ),
    MergeCase(
        "unknown never replaces a stated value",
        {"wall_insulation": InsulationLevel.none},
        {"wall_insulation": user()},
        [fact("wall_insulation", InsulationLevel.unknown)],
        {"wall_insulation": InsulationLevel.none},
    ),
    MergeCase(
        "a value replaces an earlier unknown",
        {"wall_insulation": InsulationLevel.unknown},
        {"wall_insulation": user("no idea")},
        [fact("wall_insulation", InsulationLevel.thin)],
        {"wall_insulation": InsulationLevel.thin},
    ),
    MergeCase(
        "two different values in one turn",
        {},
        {},
        [fact("storeys", 2), fact("storeys", 3)],
        {"storeys": 2},
        conflicts=["storeys"],
    ),
    MergeCase(
        "invalid combination becomes a conflict",
        {"day_end_h": 22},
        {"day_end_h": user()},
        [fact("day_start_h", 23)],
        {"day_end_h": 22, "day_start_h": None},
        conflicts=["day_start_h"],
    ),
]


@pytest.mark.parametrize("case", MERGE_CASES, ids=[c.name for c in MERGE_CASES])
def test_merge(case: MergeCase) -> None:
    brief = HouseBrief.model_validate(case.brief)
    result = merge_facts(brief, case.provenance, case.open_conflicts, case.facts)
    for name, value in case.expected.items():
        assert getattr(result.brief, name) == value, name
    assert [c.field for c in result.conflicts] == case.conflicts
    assert [f.field for f in result.renovation] == case.renovation


def test_merge_records_provenance_and_does_not_mutate() -> None:
    brief, provenance = HouseBrief(), {"storeys": DEFAULT}
    result = merge_facts(
        brief, provenance, [], [fact("storeys", 2, confidence="medium", evidence="two floors")]
    )
    assert result.provenance["storeys"] == FieldProvenance(
        source=Source.user, evidence="two floors", confidence="medium", turn=2
    )
    assert brief.storeys is None and provenance == {"storeys": DEFAULT}


def test_card_edits_override_without_conflict() -> None:
    brief, provenance = HouseBrief(heated_floor_area_m2=120.0), {"heated_floor_area_m2": user("120 m2")}
    result = merge_facts(
        brief, provenance, [], [fact("heated_floor_area_m2", 130.0)], overrides={"heated_floor_area_m2"}
    )
    assert result.brief.heated_floor_area_m2 == 130.0
    assert result.conflicts == []


def test_unclear_scenario_is_applied_with_low_confidence() -> None:
    result = merge_facts(HouseBrief(), {}, [], [fact("glazing", Glazing.triple, scenario="unclear")])
    assert result.brief.glazing is Glazing.triple
    assert result.provenance["glazing"].confidence == "low"


def test_confirming_raises_the_confidence() -> None:
    brief = HouseBrief(roof_insulation=InsulationLevel.thin)
    provenance = {"roof_insulation": user("maybe 6 cm", confidence="low")}
    result = merge_facts(
        brief, provenance, [], [fact("roof_insulation", InsulationLevel.thin, evidence="yes")]
    )
    assert result.provenance["roof_insulation"].confidence == "high"
    assert result.conflicts == []


# ---------- plan

REQUIRED = {
    "building_type": BuildingType.detached,
    "heated_floor_area_m2": 120.0,
    "storeys": 2,
    "construction_period": Period.y1971_1990,
}


def _stated(values: dict[str, Any], confidence: Confidence = "high") -> tuple[HouseBrief, Provenance]:
    return HouseBrief.model_validate(values), {name: user(confidence=confidence) for name in values}


PLAN_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    ("empty brief", {}, ["building_type", "heated_floor_area_m2"]),
    ("type known", {"building_type": BuildingType.terraced}, ["heated_floor_area_m2", "storeys"]),
    ("required known", REQUIRED, ["wall_insulation", "glazing"]),
    (
        "walls and glazing known",
        {**REQUIRED, "wall_insulation": InsulationLevel.none, "glazing": Glazing.double_old},
        ["roof_insulation", "airtightness"],
    ),
    (
        "envelope known",
        {
            **REQUIRED,
            "wall_insulation": InsulationLevel.none,
            "glazing": Glazing.double_old,
            "roof_insulation": InsulationLevel.unknown,
            "airtightness": "leaky",
            "floor_insulation": InsulationLevel.none,
        },
        ["heating_system", "ventilation"],
    ),
    (
        "systems known",
        {
            **REQUIRED,
            "wall_insulation": InsulationLevel.none,
            "glazing": Glazing.double_old,
            "roof_insulation": InsulationLevel.unknown,
            "airtightness": "leaky",
            "floor_insulation": InsulationLevel.none,
            "heating_system": "gas_boiler_old",
            "ventilation": "unknown",
        },
        ["heating_setpoint_day_c", "heating_setpoint_night_c"],
    ),
]


@pytest.mark.parametrize(("name", "values", "fields"), PLAN_CASES, ids=[c[0] for c in PLAN_CASES])
def test_next_questions_priority(name: str, values: dict[str, Any], fields: list[str]) -> None:
    brief, provenance = _stated(values)
    assert [q.field for q in next_questions(brief, provenance, [])] == fields


def test_conflicts_come_first_with_both_values_as_options() -> None:
    brief, provenance = _stated({"heated_floor_area_m2": 120.0})
    conflict = Conflict(
        field="heated_floor_area_m2",
        current=120.0,
        proposed=135.0,
        current_evidence="120 m2",
        proposed_evidence="about 135",
        turn=2,
    )
    questions = next_questions(brief, provenance, [conflict])
    assert [(q.field, q.kind) for q in questions] == [
        ("heated_floor_area_m2", "conflict"),
        ("building_type", "missing"),
    ]
    assert [o.label for o in questions[0].options] == ["120", "135"]
    assert "Earlier you said '120'" in questions[0].prompt_seed


def test_low_confidence_values_are_asked_back() -> None:
    brief, provenance = _stated(REQUIRED)
    brief = brief.model_copy(update={"wall_insulation": InsulationLevel.thin})
    provenance["wall_insulation"] = user("maybe a bit", confidence="low")
    question = next_questions(brief, provenance, [])[0]
    assert (question.field, question.kind) == ("wall_insulation", "confirm")
    assert question.prompt_seed == "You said 'maybe a bit': is 'A little (a few cm)' right?"


def test_defaults_do_not_count_as_answers() -> None:
    brief, provenance = _stated(REQUIRED)
    brief = brief.model_copy(update={"wall_insulation": InsulationLevel.thin})
    provenance["wall_insulation"] = DEFAULT
    assert next_questions(brief, provenance, [])[0].field == "wall_insulation"


def test_optional_fields_asked_twice_are_skipped() -> None:
    brief, provenance = _stated(REQUIRED)
    fields = [
        q.field for q in next_questions(brief, provenance, [], asked={"wall_insulation": 2, "storeys": 5})
    ]
    assert fields == ["glazing", "roof_insulation"]


def test_required_fields_are_asked_again() -> None:
    assert next_questions(HouseBrief(), {}, [], asked={"building_type": 9})[0].field == "building_type"


def test_apartment_is_answered_as_unsupported() -> None:
    brief, provenance = _stated({"building_type": BuildingType.apartment})
    question = next_questions(brief, provenance, [])[0]
    assert (question.field, question.kind) == ("building_type", "unsupported")
    assert "not supported yet" in question.prompt_seed
    assert "apartment" not in {o.value for o in question.options}


def test_options() -> None:
    assert [o.label for o in options_for("glazing")] == [
        "Single glazing",
        "Old double glazing",
        "Double low-e (HR) glazing",
        "Triple glazing",
        DONT_KNOW,
    ]
    assert options_for("glazing")[-1].value == "unknown"
    assert DONT_KNOW not in {o.label for o in options_for("construction_period")}  # required: no default
    assert [o.value for o in options_for("storeys")] == ["1", "2", "3"]
    assert options_for("heated_floor_area_m2") == []
    assert [o.label for o in options_for("heating_system")] == [
        "Old gas boiler",
        "Condensing gas boiler",
        "Air-water heat pump",
        DONT_KNOW,
    ]
    assert [o.value for o in options_for("ventilation")] == [
        "natural",
        "mechanical_extract",
        "balanced_heat_recovery",
        "unknown",
    ]


READY_CASES: list[tuple[str, dict[str, Any], bool, bool]] = [
    ("nothing", {}, True, False),
    ("required only", REQUIRED, False, False),
    ("required and complete", REQUIRED, True, True),
    (
        "required and envelope",
        {
            **REQUIRED,
            "wall_insulation": InsulationLevel.none,
            "roof_insulation": InsulationLevel.unknown,
            "glazing": Glazing.double_old,
        },
        False,
        True,
    ),
    ("apartment", {**REQUIRED, "building_type": BuildingType.apartment}, True, False),
    (
        "missing storeys",
        {k: v for k, v in REQUIRED.items() if k != "storeys"},
        True,
        False,
    ),
]


@pytest.mark.parametrize(
    ("name", "values", "complete", "ready"), READY_CASES, ids=[c[0] for c in READY_CASES]
)
def test_ready_to_confirm(name: str, values: dict[str, Any], complete: bool, ready: bool) -> None:
    brief, provenance = _stated(values)
    assert ready_to_confirm(brief, provenance, [], user_says_complete=complete) is ready


def test_not_ready_with_a_conflict_or_a_defaulted_required_field() -> None:
    brief, provenance = _stated(REQUIRED)
    conflict = Conflict(
        field="storeys", current=2, proposed=3, current_evidence=None, proposed_evidence="3", turn=2
    )
    assert not ready_to_confirm(brief, provenance, [conflict], user_says_complete=True)
    provenance["storeys"] = DEFAULT
    assert not ready_to_confirm(brief, provenance, [], user_says_complete=True)
