"""Parse the extraction's string values into typed Brief values, one parser per field.

The extraction schema keeps ``value`` a plain string (a simple grammar for llama.cpp); this module does the
part that must be exact: numbers with units ("120 m2", "1.290 sq ft", "2,5 m"), years and decades to periods,
insulation thicknesses to levels, and English, Dutch and French synonyms to enum members.
"""

import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from llmtrano.brief.model import (
    Airtightness,
    BuildingType,
    Glazing,
    HeatingSystem,
    HouseBrief,
    InsulationLevel,
    Orientation,
    Period,
    Ventilation,
    Zoning,
)

SQUARE_FOOT_M2 = 0.09290304
# "1.290", "1,290" or "1 290" with thousands separators (not "0.250": no leading zero)
THOUSANDS = r"[1-9]\d{0,2}(?:[.,  ]\d{3})+"
FOOT_M = 0.3048


class ParseError(ValueError):
    """A value that does not fit its field; the message is shown to the LLM on the retry."""


UNKNOWN_WORDS = (
    "unknown",
    "don't know",
    "dont know",
    "do not know",
    "no idea",
    "not sure",
    "no clue",
    "geen idee",
    "weet niet",
    "weet ik niet",
    "onbekend",
    "je ne sais pas",
    "aucune idée",
    "inconnu",
)
# Articles ("een", "un", "une") are left out: "een huis met twee verdiepingen" is two storeys.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "a couple": 2,
    "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
    "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
}  # fmt: skip


def is_unknown(text: str) -> bool:
    lowered = _normal(text)
    return any(word in lowered for word in UNKNOWN_WORDS)


def _normal(text: str) -> str:
    return " ".join(text.lower().replace("\u2019", "'").split())  # typographic apostrophe


class _NoMatch:
    def start(self) -> int:
        return -1


def _word(word: str, text: str) -> re.Match[str] | _NoMatch:
    """``word`` in ``text`` as a whole word (not inside another word); ``start()`` is -1 when absent."""
    return re.search(rf"(?<![a-zà-ÿ]){re.escape(word)}(?![a-zà-ÿ])", text) or _NoMatch()


def parse_number(text: str) -> float:
    """The first number in ``text``: "1.290" and "1,290" are thousands, "2,5" and "2.5" decimals."""
    lowered = _normal(text)
    match = re.search(THOUSANDS + r"(?!\d)|\d+(?:[.,]\d+)?", lowered)
    if match is None:
        words = [
            (found.start(), value) for word, value in NUMBER_WORDS.items() for found in [_word(word, lowered)]
        ]
        first = min(((start, value) for start, value in words if start >= 0), default=None)
        if first is None:
            raise ParseError(f"no number in {text!r}")
        return float(first[1])
    token = match.group(0)
    if re.fullmatch(THOUSANDS, token):
        return float(re.sub(r"[.,  ]", "", token))
    return float(token.replace(",", "."))


def parse_area(text: str) -> float:
    value = parse_number(text)
    if re.search(r"sq\.? ?ft|square f(oo|ee)t|ft2|ft²|sqft", _normal(text)):
        return round(value * SQUARE_FOOT_M2, 1)
    return value


def parse_height(text: str) -> float:
    lowered, value = _normal(text), parse_number(text)
    if re.search(r"\bcm\b", lowered):
        return value / 100
    if re.search(r"\bft\b|feet|foot|'", lowered):
        return round(value * FOOT_M, 2)
    return value


def parse_temperature(text: str) -> float:
    lowered, value = _normal(text), parse_number(text)
    if re.search(r"°\s*f\b|\bdeg ?f\b|fahrenheit|\d\s*f\b", lowered):
        return round((value - 32) * 5 / 9, 1)
    return value


def parse_hour(text: str) -> int:
    """'7h', '07:30', '7 am', '11 pm', 'midnight' -> the hour (minutes are dropped)."""
    lowered = _normal(text)
    if "midnight" in lowered or "middernacht" in lowered or "minuit" in lowered:
        return 24
    if "noon" in lowered or "middag" in lowered or "midi" in lowered:
        return 12
    match = re.search(r"(\d{1,2})(?:[:h.u](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", lowered)
    if match is None:
        raise ParseError(f"no hour in {text!r}")
    hour, suffix = int(match.group(1)), match.group(3)
    if suffix and suffix.startswith("p") and hour < 12:
        hour += 12
    if suffix and suffix.startswith("a") and hour == 12:
        hour = 0
    return hour


def parse_count(text: str) -> int:
    value = parse_number(text)
    if value != int(value):
        raise ParseError(f"{text!r} is not a whole number")
    return int(value)


def parse_storeys(text: str) -> int:
    lowered = _normal(text)
    if re.search(r"bungalow|single[- ]stor(e)?y|one level|gelijkvloers|plain[- ]pied", lowered):
        return 1
    return parse_count(text)


def parse_ratio(text: str) -> float:
    lowered, value = _normal(text), parse_number(text)
    return (
        value / 100 if "%" in lowered or "percent" in lowered or "procent" in lowered or value > 1 else value
    )


# ---------- enums
BUILDING_TYPES: dict[str, BuildingType] = {
    "semi_detached": BuildingType.semi_detached,
    "semi-detached": BuildingType.semi_detached,
    "semi detached": BuildingType.semi_detached,
    "halfopen": BuildingType.semi_detached,
    "half-open": BuildingType.semi_detached,
    "twee-onder-een-kap": BuildingType.semi_detached,
    "twee onder een kap": BuildingType.semi_detached,
    "jumelée": BuildingType.semi_detached,
    "mitoyenne d'un côté": BuildingType.semi_detached,
    "terraced": BuildingType.terraced,
    "terrace": BuildingType.terraced,
    "row house": BuildingType.terraced,
    "rowhouse": BuildingType.terraced,
    "townhouse": BuildingType.terraced,
    "rijwoning": BuildingType.terraced,
    "rijhuis": BuildingType.terraced,
    "gesloten bebouwing": BuildingType.terraced,
    "tussenwoning": BuildingType.terraced,
    "mitoyenne": BuildingType.terraced,
    "apartment": BuildingType.apartment,
    "flat": BuildingType.apartment,
    "appartement": BuildingType.apartment,
    "studio": BuildingType.apartment,
    "detached": BuildingType.detached,
    "vrijstaand": BuildingType.detached,
    "vrijstaande": BuildingType.detached,
    "open bebouwing": BuildingType.detached,
    "villa": BuildingType.detached,
    "bungalow": BuildingType.detached,
    "fermette": BuildingType.detached,
    "maison quatre façades": BuildingType.detached,
    "4 façades": BuildingType.detached,
    "4-gevel": BuildingType.detached,
    "3-gevel": BuildingType.semi_detached,
    "3 façades": BuildingType.semi_detached,
}
GLAZINGS: dict[str, Glazing] = {
    "double_low_e": Glazing.double_low_e,
    "low-e": Glazing.double_low_e,
    "low e": Glazing.double_low_e,
    "hr++": Glazing.double_low_e,
    "hr+": Glazing.double_low_e,
    "hr glas": Glazing.double_low_e,
    "hr-glas": Glazing.double_low_e,
    "hr glass": Glazing.double_low_e,
    "hr": Glazing.double_low_e,
    "superisolerend": Glazing.double_low_e,
    "high performance": Glazing.double_low_e,
    "argon": Glazing.double_low_e,
    "triple": Glazing.triple,
    "driedubbel": Glazing.triple,
    "drievoudig": Glazing.triple,
    "triple vitrage": Glazing.triple,
    "double_old": Glazing.double_old,
    "dubbel": Glazing.double_old,
    "double": Glazing.double_old,
    "single": Glazing.single,
    "enkel": Glazing.single,
    "simple vitrage": Glazing.single,
}
AIRTIGHTNESS: dict[str, Airtightness] = {
    "leaky": Airtightness.leaky,
    "draughty": Airtightness.leaky,
    "drafty": Airtightness.leaky,
    "draft": Airtightness.leaky,
    "draught": Airtightness.leaky,
    "tochtig": Airtightness.leaky,
    "it blows": Airtightness.leaky,
    "courants d'air": Airtightness.leaky,
    "tight": Airtightness.tight,
    "airtight": Airtightness.tight,
    "luchtdicht": Airtightness.tight,
    "étanche": Airtightness.tight,
    "average": Airtightness.average,
    "normal": Airtightness.average,
    "gemiddeld": Airtightness.average,
    "moyen": Airtightness.average,
}
ORIENTATIONS: dict[str, Orientation] = {
    "north": Orientation.north,
    "noord": Orientation.north,
    "noorden": Orientation.north,
    "nord": Orientation.north,
    "n": Orientation.north,
    "east": Orientation.east,
    "oost": Orientation.east,
    "oosten": Orientation.east,
    "est": Orientation.east,
    "e": Orientation.east,
    "o": Orientation.east,
    "south": Orientation.south,
    "zuid": Orientation.south,
    "zuiden": Orientation.south,
    "sud": Orientation.south,
    "s": Orientation.south,
    "z": Orientation.south,
    "west": Orientation.west,
    "westen": Orientation.west,
    "ouest": Orientation.west,
    "w": Orientation.west,
}
# Specific words first: "system D" and "heat recovery" before "mechanical"; "condensing" before "gas boiler".
VENTILATIONS: dict[str, Ventilation] = {
    "balanced_heat_recovery": Ventilation.balanced_heat_recovery,
    "heat recovery": Ventilation.balanced_heat_recovery,
    "balanced": Ventilation.balanced_heat_recovery,
    "system d": Ventilation.balanced_heat_recovery,
    "systeem d": Ventilation.balanced_heat_recovery,
    "système d": Ventilation.balanced_heat_recovery,
    "type d": Ventilation.balanced_heat_recovery,
    "warmteterugwinning": Ventilation.balanced_heat_recovery,
    "wtw": Ventilation.balanced_heat_recovery,
    "double flux": Ventilation.balanced_heat_recovery,
    "récupération de chaleur": Ventilation.balanced_heat_recovery,
    "mechanical_extract": Ventilation.mechanical_extract,
    "extract": Ventilation.mechanical_extract,
    "extractor": Ventilation.mechanical_extract,
    "system c": Ventilation.mechanical_extract,
    "systeem c": Ventilation.mechanical_extract,
    "système c": Ventilation.mechanical_extract,
    "type c": Ventilation.mechanical_extract,
    "afzuiging": Ventilation.mechanical_extract,
    "afvoer": Ventilation.mechanical_extract,
    "simple flux": Ventilation.mechanical_extract,
    "mechanical": Ventilation.mechanical_extract,
    "mechanisch": Ventilation.mechanical_extract,
    "mécanique": Ventilation.mechanical_extract,
    "natural": Ventilation.natural,
    "natuurlijk": Ventilation.natural,
    "naturelle": Ventilation.natural,
    "windows": Ventilation.natural,
    "ramen": Ventilation.natural,
    "fenêtres": Ventilation.natural,
    "grilles": Ventilation.natural,
    "roosters": Ventilation.natural,
    "no ventilation": Ventilation.natural,
    "geen ventilatie": Ventilation.natural,
    "none": Ventilation.natural,
}
HEATING_SYSTEMS: dict[str, HeatingSystem] = {
    "heat_pump_air_water": HeatingSystem.heat_pump_air_water,
    "heat pump": HeatingSystem.heat_pump_air_water,
    "heatpump": HeatingSystem.heat_pump_air_water,
    "warmtepomp": HeatingSystem.heat_pump_air_water,
    "pompe à chaleur": HeatingSystem.heat_pump_air_water,
    "pac": HeatingSystem.heat_pump_air_water,
    "gas_boiler_condensing": HeatingSystem.gas_boiler_condensing,
    "condensing": HeatingSystem.gas_boiler_condensing,
    "condensatieketel": HeatingSystem.gas_boiler_condensing,
    "condensatie": HeatingSystem.gas_boiler_condensing,
    "condenserend": HeatingSystem.gas_boiler_condensing,
    "condensation": HeatingSystem.gas_boiler_condensing,
    "hr-ketel": HeatingSystem.gas_boiler_condensing,
    "hr ketel": HeatingSystem.gas_boiler_condensing,
    "new boiler": HeatingSystem.gas_boiler_condensing,
    "nieuwe ketel": HeatingSystem.gas_boiler_condensing,
    "gas_boiler_old": HeatingSystem.gas_boiler_old,
    "old boiler": HeatingSystem.gas_boiler_old,
    "oude ketel": HeatingSystem.gas_boiler_old,
    "vieille chaudière": HeatingSystem.gas_boiler_old,
    "non-condensing": HeatingSystem.gas_boiler_old,
    "gas boiler": HeatingSystem.gas_boiler_old,
    "gasketel": HeatingSystem.gas_boiler_old,
    "chaudière": HeatingSystem.gas_boiler_old,
    "boiler": HeatingSystem.gas_boiler_old,
    "ketel": HeatingSystem.gas_boiler_old,
    "gas": HeatingSystem.gas_boiler_old,
}
ZONINGS: dict[str, Zoning] = {
    "day_night": Zoning.day_night,
    "day and night": Zoning.day_night,
    "day/night": Zoning.day_night,
    "night zone": Zoning.day_night,
    "dag en nacht": Zoning.day_night,
    "nachtzone": Zoning.day_night,
    "jour et nuit": Zoning.day_night,
    "zone nuit": Zoning.day_night,
    "two zones": Zoning.day_night,
    "twee zones": Zoning.day_night,
    "one": Zoning.one,
    "single": Zoning.one,
    "one zone": Zoning.one,
    "whole house": Zoning.one,
    "één zone": Zoning.one,
    "une zone": Zoning.one,
}
INSULATION_WORDS: dict[str, InsulationLevel] = {
    "none": InsulationLevel.none,
    "no insulation": InsulationLevel.none,
    "not insulated": InsulationLevel.none,
    "uninsulated": InsulationLevel.none,
    "bare": InsulationLevel.none,
    "geen": InsulationLevel.none,
    "niet geïsoleerd": InsulationLevel.none,
    "niet geisoleerd": InsulationLevel.none,
    "ongeïsoleerd": InsulationLevel.none,
    "pas isolé": InsulationLevel.none,
    "aucune": InsulationLevel.none,
    "thin": InsulationLevel.thin,
    "a little": InsulationLevel.thin,
    "a few": InsulationLevel.thin,
    "een paar": InsulationLevel.thin,
    "quelques": InsulationLevel.thin,
    "some": InsulationLevel.thin,
    "a bit": InsulationLevel.thin,
    "weinig": InsulationLevel.thin,
    "beetje": InsulationLevel.thin,
    "dun": InsulationLevel.thin,
    "peu": InsulationLevel.thin,
    "medium": InsulationLevel.medium,
    "moderate": InsulationLevel.medium,
    "partly": InsulationLevel.medium,
    "partially": InsulationLevel.medium,
    "average": InsulationLevel.medium,
    "redelijk": InsulationLevel.medium,
    "matig": InsulationLevel.medium,
    "good": InsulationLevel.good,
    "well": InsulationLevel.good,
    "thick": InsulationLevel.good,
    "fully": InsulationLevel.good,
    "renovated": InsulationLevel.good,
    "goed": InsulationLevel.good,
    "dik": InsulationLevel.good,
    "bien": InsulationLevel.good,
    "insulated": InsulationLevel.medium,  # "insulated" without a qualifier: checked last
    "geïsoleerd": InsulationLevel.medium,
    "geisoleerd": InsulationLevel.medium,
    "isolé": InsulationLevel.medium,
}
# Thickness [cm] up to which each level applies, halfway between the library's levels (constructions.py:
# walls 4/8/12 cm EPS, roof 6/12/22 cm mineral wool, floor 3/5/8 cm PUR).
THICKNESS_LIMITS_CM: dict[str, tuple[float, float, float]] = {
    "wall_insulation": (1, 6, 10),
    "roof_insulation": (2, 9, 17),
    "floor_insulation": (1, 4, 6.5),
}


def _lookup[T](table: dict[str, T], text: str, field: str) -> T:
    """The value of the first word of ``table`` found in ``text``; the tables list specific words first."""
    lowered = _normal(text)
    if lowered in table:
        return table[lowered]
    for word, value in table.items():
        if len(word) > 2 and _word(word, lowered).start() >= 0:  # one or two letters only as the whole text
            return value
    raise ParseError(
        f"{field}: {text!r} matches none of {', '.join(sorted({str(v) for v in table.values()}))}"
    )


def parse_building_type(text: str) -> BuildingType:
    return _lookup(BUILDING_TYPES, text, "building_type")


def parse_glazing(text: str) -> Glazing:
    if is_unknown(text):
        return Glazing.unknown
    return _lookup(GLAZINGS, text, "glazing")


def parse_airtightness(text: str) -> Airtightness:
    if is_unknown(text):
        return Airtightness.unknown
    return _lookup(AIRTIGHTNESS, text, "airtightness")


def parse_ventilation(text: str) -> Ventilation:
    if is_unknown(text):
        return Ventilation.unknown
    return _lookup(VENTILATIONS, text, "ventilation")


def parse_heating_system(text: str) -> HeatingSystem:
    if is_unknown(text):
        return HeatingSystem.unknown
    return _lookup(HEATING_SYSTEMS, text, "heating_system")


def parse_zoning(text: str) -> Zoning:
    return _lookup(ZONINGS, text, "zoning")


def parse_orientation(text: str) -> Orientation:
    lowered = _normal(text)
    found = {orientation for word, orientation in ORIENTATIONS.items() if _word(word, lowered).start() >= 0}
    if len(found) != 1:
        raise ParseError(f"orientation: {text!r} is not one of north, east, south, west")
    return found.pop()


def insulation_parser(field: str) -> Callable[[str], InsulationLevel]:
    def parse(text: str) -> InsulationLevel:
        lowered = _normal(text)
        if is_unknown(text):
            return InsulationLevel.unknown
        if lowered in InsulationLevel.__members__:
            return InsulationLevel(lowered)
        thickness = re.search(r"(\d+(?:[.,]\d+)?)\s*(cm|mm)\b", lowered)
        if thickness:
            cm = float(thickness.group(1).replace(",", ".")) / (10 if thickness.group(2) == "mm" else 1)
            limits = THICKNESS_LIMITS_CM[field]
            levels = (InsulationLevel.none, InsulationLevel.thin, InsulationLevel.medium)
            below = (level for level, limit in zip(levels, limits, strict=True) if cm < limit)
            return next(below, InsulationLevel.good)
        return _lookup(INSULATION_WORDS, text, field)

    return parse


def parse_period(text: str) -> Period:
    """A period name, a year, a decade ('1960s', 'jaren 60', 'années 70') or words ('pre-war')."""
    lowered = _normal(text)
    if lowered in Period.__members__:
        return Period(lowered)
    if re.search(r"pre-?war|before the war|voor de oorlog|vooroorlogs|avant[- ]guerre|before 1946", lowered):
        return Period.before_1946
    if re.search(r"new[- ]?build|nieuwbouw|brand new|neuve|recently built", lowered):
        return Period.after_2020
    decade = re.search(r"(?:(1[89]|20)?(\d)0)\s*'?s\b|jaren\s*'?(\d)0|années\s*(\d)0", lowered)
    year: int | None = None
    if decade:
        if decade.group(2) is not None:
            century = decade.group(1) or "19"
            year = int(f"{century}{decade.group(2)}5")  # the middle of the decade
        else:
            year = 1905 + 10 * int(decade.group(3) or decade.group(4))
    else:
        years = [int(y) for y in re.findall(r"\b(1[6-9]\d{2}|20\d{2})\b", lowered)]
        if years:
            year = round(sum(years) / len(years))  # a range "1975-1980" -> its middle
    if year is None:
        raise ParseError(f"construction_period: {text!r} gives no year or period")
    return period_of_year(year)


def period_of_year(year: int) -> Period:
    if year <= 1945:
        return Period.before_1946
    if year <= 1970:
        return Period.y1946_1970
    if year <= 1990:
        return Period.y1971_1990
    if year <= 2005:
        return Period.y1991_2005
    if year <= 2020:
        return Period.y2006_2020
    return Period.after_2020


def parse_municipality(text: str) -> str:
    """The municipality only: values with digits or street words look like an address (GDPR minimisation)."""
    value = " ".join(text.split()).strip(" ,.")
    if not value:
        raise ParseError("municipality: empty")
    if re.search(r"\d|\b(street|straat|laan|rue|avenue|weg|plein|place|chaussée|steenweg)\b", value.lower()):
        raise ParseError("municipality: give the municipality only, without street, number or postal code")
    return value


PARSERS: dict[str, Callable[[str], Any]] = {
    "building_type": parse_building_type,
    "construction_period": parse_period,
    "heated_floor_area_m2": parse_area,
    "storeys": parse_storeys,
    "zoning": parse_zoning,
    "room_height_m": parse_height,
    "street_facade_orientation": parse_orientation,
    "main_window_orientation": parse_orientation,
    "wall_insulation": insulation_parser("wall_insulation"),
    "roof_insulation": insulation_parser("roof_insulation"),
    "floor_insulation": insulation_parser("floor_insulation"),
    "glazing": parse_glazing,
    "window_to_wall_ratio": parse_ratio,
    "airtightness": parse_airtightness,
    "ventilation": parse_ventilation,
    "heating_system": parse_heating_system,
    "heating_setpoint_day_c": parse_temperature,
    "heating_setpoint_night_c": parse_temperature,
    "day_start_h": parse_hour,
    "day_end_h": parse_hour,
    "occupants": parse_count,
    "municipality": parse_municipality,
}
NUMERIC_FIELDS = {
    "heated_floor_area_m2",
    "storeys",
    "room_height_m",
    "window_to_wall_ratio",
    "heating_setpoint_day_c",
    "heating_setpoint_night_c",
    "day_start_h",
    "day_end_h",
    "occupants",
}


def parse_value(field: str, text: str) -> Any | None:
    """The typed value of ``field``, checked against the Brief's bounds; None for "I don't know" on a number.

    Raises:
        ParseError: the text does not give a valid value for the field.
    """
    if field not in PARSERS:
        raise ParseError(f"unknown field {field!r}")
    if field in NUMERIC_FIELDS and is_unknown(text):
        return None
    value = PARSERS[field](text)
    try:
        HouseBrief.model_validate({field: value})
    except ValidationError as error:
        reason = error.errors()[0]["msg"]
        raise ParseError(f"{field}: {text!r} gives {value!r}: {reason}") from error
    return value
