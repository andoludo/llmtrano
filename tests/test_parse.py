"""Field parsers: every field, units, separators, synonyms, unknowns and bounds."""

from typing import Any

import pytest

from llmtrano.brief.model import (
    BRIEF_FIELDS,
    Airtightness,
    BuildingType,
    Glazing,
    HeatingSystem,
    InsulationLevel,
    Orientation,
    Period,
    Ventilation,
    Zoning,
)
from llmtrano.brief.parse import PARSERS, ParseError, parse_number, parse_value, period_of_year


def test_every_field_has_a_parser() -> None:
    assert set(PARSERS) == set(BRIEF_FIELDS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("120", 120),
        ("120 m2", 120),
        ("about 135 m²", 135),
        ("2,5 m", 2.5),
        ("2.5", 2.5),
        ("1.290 sq ft", 1290),
        ("1,290", 1290),
        ("1 290", 1290),
        ("0.250", 0.25),
        ("two", 2),
        ("a couple", 2),
        ("family of four", 4),
        ("een huis met twee verdiepingen", 2),
        ("drie", 3),
    ],
)
def test_parse_number(text: str, expected: float) -> None:
    assert parse_number(text) == expected


CASES: list[tuple[str, str, Any]] = [
    # building type
    ("building_type", "detached", BuildingType.detached),
    ("building_type", "semi-detached house", BuildingType.semi_detached),
    ("building_type", "halfopen bebouwing", BuildingType.semi_detached),
    ("building_type", "twee-onder-een-kap", BuildingType.semi_detached),
    ("building_type", "rijwoning", BuildingType.terraced),
    ("building_type", "row house", BuildingType.terraced),
    ("building_type", "vrijstaande woning", BuildingType.detached),
    ("building_type", "open bebouwing", BuildingType.detached),
    ("building_type", "appartement", BuildingType.apartment),
    ("building_type", "a flat on the 3rd floor", BuildingType.apartment),
    ("building_type", "maison mitoyenne", BuildingType.terraced),
    # construction period
    ("construction_period", "y1971_1990", Period.y1971_1990),
    ("construction_period", "1985", Period.y1971_1990),
    ("construction_period", "built in 1930", Period.before_1946),
    ("construction_period", "1946", Period.y1946_1970),
    ("construction_period", "1970", Period.y1946_1970),
    ("construction_period", "1960s", Period.y1946_1970),
    ("construction_period", "the 60s", Period.y1946_1970),
    ("construction_period", "jaren 70", Period.y1971_1990),
    ("construction_period", "années 50", Period.y1946_1970),
    ("construction_period", "2010s", Period.y2006_2020),
    ("construction_period", "1975-1980", Period.y1971_1990),
    ("construction_period", "pre-war", Period.before_1946),
    ("construction_period", "voor de oorlog", Period.before_1946),
    ("construction_period", "new build", Period.after_2020),
    ("construction_period", "2022", Period.after_2020),
    # floor area
    ("heated_floor_area_m2", "120 m2", 120.0),
    ("heated_floor_area_m2", "135,5 m²", 135.5),
    ("heated_floor_area_m2", "1,290 sq ft", 119.8),
    ("heated_floor_area_m2", "1300 square feet", 120.8),
    # storeys
    ("storeys", "2", 2),
    ("storeys", "two floors", 2),
    ("storeys", "twee verdiepingen", 2),
    ("storeys", "bungalow", 1),
    ("storeys", "gelijkvloers", 1),
    # room height
    ("room_height_m", "2.5 m", 2.5),
    ("room_height_m", "2,7m", 2.7),
    ("room_height_m", "260 cm", 2.6),
    ("room_height_m", "9 ft", 2.74),
    # orientation
    ("street_facade_orientation", "north", Orientation.north),
    ("street_facade_orientation", "facing south", Orientation.south),
    ("street_facade_orientation", "noord", Orientation.north),
    ("street_facade_orientation", "zuid", Orientation.south),
    ("street_facade_orientation", "W", Orientation.west),
    ("street_facade_orientation", "oost", Orientation.east),
    # insulation
    ("wall_insulation", "none", InsulationLevel.none),
    ("wall_insulation", "not insulated", InsulationLevel.none),
    ("wall_insulation", "geen isolatie", InsulationLevel.none),
    ("wall_insulation", "4 cm", InsulationLevel.thin),
    ("wall_insulation", "a few centimetres of insulation", InsulationLevel.thin),
    ("roof_insulation", "een paar centimeter", InsulationLevel.thin),
    ("wall_insulation", "8 cm EPS", InsulationLevel.medium),
    ("wall_insulation", "12cm", InsulationLevel.good),
    ("wall_insulation", "well insulated", InsulationLevel.good),
    ("wall_insulation", "goed geïsoleerd", InsulationLevel.good),
    ("wall_insulation", "partially insulated", InsulationLevel.medium),
    ("wall_insulation", "insulated", InsulationLevel.medium),
    ("wall_insulation", "I don't know", InsulationLevel.unknown),
    ("wall_insulation", "geen idee", InsulationLevel.unknown),
    ("roof_insulation", "maybe 6 cm of wool", InsulationLevel.thin),
    ("roof_insulation", "12 cm", InsulationLevel.medium),
    ("roof_insulation", "20 cm", InsulationLevel.good),
    ("roof_insulation", "220 mm", InsulationLevel.good),
    ("floor_insulation", "3 cm PUR", InsulationLevel.thin),
    ("floor_insulation", "5 cm", InsulationLevel.medium),
    ("floor_insulation", "8 cm", InsulationLevel.good),
    ("floor_insulation", "uninsulated", InsulationLevel.none),
    # glazing
    ("glazing", "double_low_e", Glazing.double_low_e),
    ("glazing", "dubbel glas", Glazing.double_old),
    ("glazing", "double glazing", Glazing.double_old),
    ("glazing", "HR glass", Glazing.double_low_e),
    ("glazing", "HR++", Glazing.double_low_e),
    ("glazing", "dubbel HR glas", Glazing.double_low_e),
    ("glazing", "low-e double glazing", Glazing.double_low_e),
    ("glazing", "driedubbel glas", Glazing.triple),
    ("glazing", "triple glazing", Glazing.triple),
    ("glazing", "enkel glas", Glazing.single),
    ("glazing", "no idea", Glazing.unknown),
    # window ratio
    ("window_to_wall_ratio", "20%", 0.2),
    ("window_to_wall_ratio", "0.35", 0.35),
    ("window_to_wall_ratio", "15 percent", 0.15),
    # airtightness
    ("airtightness", "draughty", Airtightness.leaky),
    ("airtightness", "very drafty", Airtightness.leaky),
    ("airtightness", "tochtig", Airtightness.leaky),
    ("airtightness", "airtight", Airtightness.tight),
    ("airtightness", "normal", Airtightness.average),
    ("airtightness", "not sure", Airtightness.unknown),
    # set points
    ("heating_setpoint_day_c", "21 degC", 21.0),
    ("heating_setpoint_day_c", "20,5°C", 20.5),
    ("heating_setpoint_day_c", "70 F", 21.1),
    ("heating_setpoint_night_c", "16", 16.0),
    # hours
    ("day_start_h", "7h", 7),
    ("day_start_h", "07:30", 7),
    ("day_start_h", "6 am", 6),
    ("day_end_h", "11 pm", 23),
    ("day_end_h", "23u", 23),
    ("day_end_h", "midnight", 24),
    # occupants
    ("occupants", "4", 4),
    ("occupants", "a couple", 2),
    ("occupants", "family of five", 5),
    # ventilation (phase 2)
    ("ventilation", "balanced_heat_recovery", Ventilation.balanced_heat_recovery),
    ("ventilation", "balanced ventilation with heat recovery", Ventilation.balanced_heat_recovery),
    ("ventilation", "systeem D", Ventilation.balanced_heat_recovery),
    ("ventilation", "ventilatie met warmteterugwinning", Ventilation.balanced_heat_recovery),
    ("ventilation", "VMC double flux", Ventilation.balanced_heat_recovery),
    ("ventilation", "mechanical extract in the bathroom and kitchen", Ventilation.mechanical_extract),
    ("ventilation", "system C", Ventilation.mechanical_extract),
    ("ventilation", "mechanische afzuiging", Ventilation.mechanical_extract),
    ("ventilation", "VMC simple flux", Ventilation.mechanical_extract),
    ("ventilation", "we open the windows", Ventilation.natural),
    ("ventilation", "natural", Ventilation.natural),
    ("ventilation", "no ventilation system", Ventilation.natural),
    ("ventilation", "no idea", Ventilation.unknown),
    # heating system (phase 2)
    ("heating_system", "heat_pump_air_water", HeatingSystem.heat_pump_air_water),
    ("heating_system", "an air-water heat pump", HeatingSystem.heat_pump_air_water),
    ("heating_system", "warmtepomp", HeatingSystem.heat_pump_air_water),
    ("heating_system", "pompe à chaleur", HeatingSystem.heat_pump_air_water),
    ("heating_system", "a condensing gas boiler", HeatingSystem.gas_boiler_condensing),
    ("heating_system", "condensatieketel op gas", HeatingSystem.gas_boiler_condensing),
    ("heating_system", "chaudière à condensation", HeatingSystem.gas_boiler_condensing),
    ("heating_system", "an old gas boiler from 1995", HeatingSystem.gas_boiler_old),
    ("heating_system", "gas boiler", HeatingSystem.gas_boiler_old),
    ("heating_system", "oude gasketel", HeatingSystem.gas_boiler_old),
    ("heating_system", "chaudière au gaz", HeatingSystem.gas_boiler_old),
    ("heating_system", "I don't know", HeatingSystem.unknown),
    # zoning (phase 2)
    ("zoning", "day_night", Zoning.day_night),
    ("zoning", "a day and night zone", Zoning.day_night),
    ("zoning", "the bedrooms upstairs are a separate night zone", Zoning.day_night),
    ("zoning", "dag- en nachtzone", Zoning.day_night),
    ("zoning", "one zone", Zoning.one),
    ("zoning", "the whole house at the same temperature", Zoning.one),
    # main window orientation (phase 2)
    ("main_window_orientation", "big windows to the south", Orientation.south),
    ("main_window_orientation", "zuid", Orientation.south),
    # municipality
    ("municipality", "Gent", "Gent"),
    ("municipality", "  Sint-Niklaas ", "Sint-Niklaas"),
]


@pytest.mark.parametrize(("field", "text", "expected"), CASES)
def test_parse_value(field: str, text: str, expected: Any) -> None:
    value = parse_value(field, text)
    if isinstance(expected, float):
        assert value == pytest.approx(expected, abs=0.05)
    else:
        assert value == expected


@pytest.mark.parametrize("field", ["heated_floor_area_m2", "storeys", "occupants", "heating_setpoint_day_c"])
def test_unknown_number_gives_no_value(field: str) -> None:
    assert parse_value(field, "I don't know") is None


@pytest.mark.parametrize(
    ("field", "text", "message"),
    [
        ("heated_floor_area_m2", "5000 m2", "less than or equal to 1000"),
        ("storeys", "seven", "less than or equal to 4"),
        ("heating_setpoint_day_c", "30", "less than or equal to 25"),
        ("building_type", "castle", "matches none of"),
        ("street_facade_orientation", "north-east", "not one of"),
        ("construction_period", "old", "no year or period"),
        ("glazing", "plexiglass", "matches none of"),
        ("ventilation", "a fan in the attic", "matches none of"),
        ("heating_system", "wood stove", "matches none of"),
        ("zoning", "three", "matches none of"),
        ("occupants", "2.5", "not a whole number"),
        ("municipality", "Kerkstraat 12, 9000 Gent", "municipality only"),
        ("swimming_pool", "yes", "unknown field"),
    ],
)
def test_parse_errors_explain_the_problem(field: str, text: str, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse_value(field, text)


@pytest.mark.parametrize(
    ("year", "period"),
    [
        (1945, Period.before_1946),
        (1946, Period.y1946_1970),
        (1970, Period.y1946_1970),
        (1971, Period.y1971_1990),
        (1990, Period.y1971_1990),
        (1991, Period.y1991_2005),
        (2005, Period.y1991_2005),
        (2006, Period.y2006_2020),
        (2020, Period.y2006_2020),
        (2021, Period.after_2020),
    ],
)
def test_period_boundaries(year: int, period: Period) -> None:
    assert period_of_year(year) is period
