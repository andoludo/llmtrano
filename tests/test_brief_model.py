"""House Brief: enums, bounds, defaults and provenance."""

import typing
from typing import Any

import pytest
from pydantic import ValidationError

from llmtrano.brief.defaults import DEFAULTS, apply_defaults, assumptions
from llmtrano.brief.model import (
    BRIEF_FIELDS,
    REQUIRED_FIELDS,
    Airtightness,
    BriefField,
    BuildingType,
    FieldProvenance,
    Glazing,
    HeatingSystem,
    HouseBrief,
    InsulationLevel,
    Orientation,
    Period,
    Source,
    Ventilation,
    Zoning,
    missing_required,
)


def test_enum_members() -> None:
    assert [m.value for m in BuildingType] == ["detached", "semi_detached", "terraced", "apartment"]
    assert [m.value for m in Period] == [
        "before_1946",
        "y1946_1970",
        "y1971_1990",
        "y1991_2005",
        "y2006_2020",
        "after_2020",
    ]
    assert [m.value for m in InsulationLevel] == ["none", "thin", "medium", "good", "unknown"]
    assert [m.value for m in Glazing] == ["single", "double_old", "double_low_e", "triple", "unknown"]
    assert [m.value for m in Airtightness] == ["leaky", "average", "tight", "unknown"]
    assert [m.value for m in Orientation] == ["north", "east", "south", "west"]
    assert [m.value for m in Ventilation] == [
        "natural",
        "mechanical_extract",
        "balanced_heat_recovery",
        "unknown",
    ]
    assert [m.value for m in HeatingSystem] == [
        "gas_boiler_old",
        "gas_boiler_condensing",
        "heat_pump_air_water",
        "unknown",
    ]
    assert [m.value for m in Zoning] == ["one", "day_night"]


def test_brief_field_literal_lists_every_field() -> None:
    assert typing.get_args(BriefField) == BRIEF_FIELDS


def test_empty_brief_is_valid_and_all_none() -> None:
    brief = HouseBrief()
    assert all(getattr(brief, name) is None for name in BRIEF_FIELDS)
    assert missing_required(brief) == list(REQUIRED_FIELDS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heated_floor_area_m2", 19.9),
        ("heated_floor_area_m2", 1000.1),
        ("storeys", 0),
        ("storeys", 5),
        ("room_height_m", 2.1),
        ("room_height_m", 4.1),
        ("window_to_wall_ratio", 0.04),
        ("window_to_wall_ratio", 0.61),
        ("heating_setpoint_day_c", 14.9),
        ("heating_setpoint_day_c", 25.1),
        ("heating_setpoint_night_c", 11.9),
        ("heating_setpoint_night_c", 22.1),
        ("day_start_h", -1),
        ("day_start_h", 24),
        ("day_end_h", 0),
        ("day_end_h", 25),
        ("occupants", 0),
        ("occupants", 13),
        ("building_type", "castle"),
    ],
)
def test_out_of_bounds_values_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        HouseBrief.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("heated_floor_area_m2", 20), ("heated_floor_area_m2", 1000), ("storeys", 4), ("day_end_h", 24)],
)
def test_bounds_are_inclusive(field: str, value: Any) -> None:
    assert getattr(HouseBrief.model_validate({field: value}), field) == value


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HouseBrief.model_validate({"swimming_pool": True})


def test_day_must_start_before_it_ends() -> None:
    with pytest.raises(ValidationError, match="before day_end_h"):
        HouseBrief(day_start_h=20, day_end_h=8)


def test_assignment_is_validated() -> None:
    brief = HouseBrief()
    with pytest.raises(ValidationError):
        brief.storeys = 9


def _user(evidence: str, turn: int = 1) -> FieldProvenance:
    return FieldProvenance(source=Source.user, evidence=evidence, confidence="high", turn=turn)


def test_defaults_fill_every_optional_field_of_a_dated_house() -> None:
    brief = HouseBrief(
        building_type=BuildingType.detached,
        construction_period=Period.y1946_1970,
        heated_floor_area_m2=120,
        storeys=2,
    )
    filled, provenance = apply_defaults(brief)
    assert filled.room_height_m == 2.6
    assert (filled.heating_setpoint_day_c, filled.heating_setpoint_night_c) == (21.0, 16.0)
    assert (filled.day_start_h, filled.day_end_h) == (7, 23)
    assert filled.window_to_wall_ratio == 0.20
    assert filled.airtightness is Airtightness.average
    assert filled.wall_insulation is filled.roof_insulation is filled.floor_insulation is InsulationLevel.none
    assert filled.glazing is Glazing.double_old
    assert filled.street_facade_orientation is Orientation.north
    assert filled.occupants == 2
    assert filled.ventilation is Ventilation.natural  # before 2006
    assert filled.heating_system is HeatingSystem.unknown  # the ideal heater
    assert filled.zoning is Zoning.one
    assert filled.municipality is None  # stored only when given; never defaulted
    assert filled.main_window_orientation is None  # windows spread evenly
    assert set(provenance) == set(DEFAULTS)
    assert all(item.source is Source.default and item.basis for item in provenance.values())


@pytest.mark.parametrize(
    ("period", "insulation", "glazing"),
    [
        (Period.before_1946, InsulationLevel.none, Glazing.double_old),
        (Period.y1946_1970, InsulationLevel.none, Glazing.double_old),
        (Period.y1971_1990, InsulationLevel.thin, Glazing.double_old),
        (Period.y1991_2005, InsulationLevel.medium, Glazing.double_low_e),
        (Period.y2006_2020, InsulationLevel.good, Glazing.double_low_e),
        (Period.after_2020, InsulationLevel.good, Glazing.double_low_e),
    ],
)
def test_period_defaults(period: Period, insulation: InsulationLevel, glazing: Glazing) -> None:
    filled, _ = apply_defaults(HouseBrief(construction_period=period))
    expected_ventilation = (
        Ventilation.mechanical_extract
        if period in (Period.y2006_2020, Period.after_2020)
        else Ventilation.natural
    )
    assert filled.ventilation is expected_ventilation
    assert filled.wall_insulation is insulation
    assert filled.glazing is glazing


def test_insulation_has_no_default_without_a_period() -> None:
    filled, provenance = apply_defaults(HouseBrief())
    assert filled.wall_insulation is None
    assert "wall_insulation" not in provenance


def test_defaults_never_overwrite_user_values() -> None:
    given = {
        "room_height_m": 3.0,
        "heating_setpoint_day_c": 20.0,
        "heating_setpoint_night_c": 18.0,
        "day_start_h": 6,
        "day_end_h": 22,
        "window_to_wall_ratio": 0.35,
        "airtightness": Airtightness.tight,
        "wall_insulation": InsulationLevel.good,
        "roof_insulation": InsulationLevel.thin,
        "floor_insulation": InsulationLevel.medium,
        "glazing": Glazing.triple,
        "street_facade_orientation": Orientation.east,
        "occupants": 5,
        "ventilation": Ventilation.balanced_heat_recovery,
        "heating_system": HeatingSystem.heat_pump_air_water,
        "zoning": Zoning.day_night,
    }
    brief = HouseBrief(construction_period=Period.before_1946, **given)
    provenance = {name: _user(str(value)) for name, value in given.items()}
    filled, after = apply_defaults(brief, provenance)
    assert filled == brief
    assert after == provenance


def test_unknown_gets_the_default_and_keeps_the_users_words() -> None:
    brief = HouseBrief(construction_period=Period.y1971_1990, wall_insulation=InsulationLevel.unknown)
    filled, provenance = apply_defaults(brief, {"wall_insulation": _user("no idea", turn=2)})
    assert filled.wall_insulation is InsulationLevel.thin
    assert provenance["wall_insulation"].source is Source.default
    assert provenance["wall_insulation"].evidence == "no idea"
    assert provenance["wall_insulation"].turn == 2


def test_apply_defaults_does_not_mutate_its_inputs() -> None:
    brief, provenance = HouseBrief(construction_period=Period.after_2020), {"x": _user("x")}
    apply_defaults(brief, provenance)
    assert brief.room_height_m is None
    assert list(provenance) == ["x"]


@pytest.mark.parametrize(("start", "end", "expected"), [(23, None, (23, 24)), (None, 6, (0, 6))])
def test_default_hours_stay_consistent_with_the_users(
    start: int | None, end: int | None, expected: tuple[int, int]
) -> None:
    filled, _ = apply_defaults(HouseBrief(day_start_h=start, day_end_h=end))
    assert (filled.day_start_h, filled.day_end_h) == expected


def test_provenance_is_complete_after_defaults() -> None:
    """Every filled field has a provenance: the user's for stated values, a default's for the rest."""
    brief = HouseBrief(
        building_type=BuildingType.terraced,
        construction_period=Period.y1991_2005,
        heated_floor_area_m2=95,
        storeys=2,
        glazing=Glazing.triple,
    )
    stated = {
        name: _user("said") for name in ("building_type", "construction_period", "heated_floor_area_m2")
    }
    stated |= {"storeys": _user("said"), "glazing": _user("said")}
    filled, provenance = apply_defaults(brief, stated)
    filled_fields = {name for name in BRIEF_FIELDS if getattr(filled, name) is not None}
    assert filled_fields == set(provenance)


def test_assumption_lines_name_the_value_and_the_basis() -> None:
    filled, provenance = apply_defaults(HouseBrief(construction_period=Period.before_1946))
    lines = assumptions(filled, provenance)
    assert "window to wall ratio = 0.2 (rule of thumb (assumption))" in lines
    assert (
        "wall insulation = none (assumed from the construction period (rule of thumb until TABULA data))"
        in lines
    )
    assert "ventilation = natural (" in " ".join(lines)
    assert "heating system = unknown (not stated: an ideal heater gives the heating need" in " ".join(lines)
