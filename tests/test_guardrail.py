"""Number guardrail: good and bad texts, units, thousands separators, decimal comma and point."""

import pytest

from llmtrano.guardrails.numbers import (
    check_numbers,
    fallback_text,
    known_values,
    numbers_in,
    parse_number,
    retry_note,
)

KPIS = {
    "heating_energy_kwh": 6085.484434,
    "heating_energy_kwh_per_m2": 60.854844,
    "peak_heating_w": 3862.30469,
    "heated_floor_area_m2": 100.0,
    "zones": [{"zone_id": "living_001", "hours_below_setpoint": 273.99}],
}
BRIEF = {
    "heated_floor_area_m2": 100.0,
    "storeys": 1,
    "heating_setpoint_day_c": 21.0,
    "construction_period": "1971 to 1990",
}


@pytest.mark.parametrize(
    ("token", "value"),
    [
        ("6085", 6085),
        ("6,085", 6085),
        ("6.085", 6085),
        ("6 085", 6085),
        ("6\u202f085", 6085),
        ("60.9", 60.9),
        ("60,9", 60.9),
        ("6,085.5", 6085.5),
        ("6.085,5", 6085.5),
        ("1,234,567", 1234567),
        ("0,250", 0.25),
    ],
)
def test_parse_number(token: str, value: float) -> None:
    assert parse_number(token) == pytest.approx(value)


@pytest.mark.parametrize(
    "text",
    [
        "Your house needs about 6,085 kWh of heat per year, or 60.9 kWh per m2.",
        "Uw huis heeft 6.085 kWh warmte per jaar nodig, 60,9 kWh per m2.",
        "Votre maison a besoin de 6 085 kWh, soit 61 kWh/m2, avec une pointe de 3,9 kW.",
        "The heater peaked at 3862 W on the coldest hour of the 100 m2 house.",
        "At 21 degC from 7h to 23h the single storey needs 6085.5 kWh.",
        "It needs 6100 kWh.",  # within 1% of 6085.5
    ],
)
def test_good_texts_pass(text: str) -> None:
    result = check_numbers(text, KPIS, BRIEF)
    assert result.passed, result.wrong


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        ("Your house needs 7,200 kWh per year.", ["7,200"]),
        ("That is 30% more than an average house.", ["30"]),
        ("It would cost about 1.450 euro.", ["1.450"]),
        ("The heat need is 72,5 kWh per m2.", ["72,5"]),
        ("The peak is 4.5 kW and the need 6085 kWh.", ["4.5"]),
    ],
)
def test_bad_texts_fail_with_the_wrong_numbers(text: str, wrong: list[str]) -> None:
    result = check_numbers(text, KPIS, BRIEF)
    assert not result.passed
    assert result.wrong == wrong


def test_kilowatts_and_megawatt_hours_are_scaled() -> None:
    assert check_numbers("The peak was 3.9 kW for a need of 6.1 MWh.", KPIS, BRIEF).passed
    assert check_numbers("The peak was 3.9 W.", KPIS, BRIEF).wrong == ["3.9"]  # no unit scaling for W


def test_hours_and_list_markers_are_ignored() -> None:
    assert numbers_in("Heated from 7h to 23:00, 6 am to 11 pm.") == []


def test_known_values_walk_nested_structures() -> None:
    assert {6085.484434, 273.99, 100.0, 1.0, 21.0, 1971.0, 1990.0} <= known_values(KPIS, BRIEF)


def test_retry_note_and_fallback() -> None:
    assert "7,200, 30" in retry_note(["7,200", "30"])
    text = fallback_text(KPIS)
    assert "6085 kWh" in text and "60.9 kWh per m2" in text and "3862 W" in text
    assert check_numbers(text, KPIS, BRIEF).passed
