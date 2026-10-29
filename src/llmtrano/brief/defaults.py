"""Defaults for the optional Brief fields the user did not give, each with the source it comes from.

A default never replaces a user value. ``unknown`` ("I don't know") counts as not given: the default is
applied and the user's answer stays visible as the evidence.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from llmtrano.brief.model import (
    Airtightness,
    FieldProvenance,
    Glazing,
    HeatingSystem,
    HouseBrief,
    InsulationLevel,
    Orientation,
    Period,
    Provenance,
    Source,
    Ventilation,
    Zoning,
)

TRANO_REFERENCE = "trano reference house"
RULE_OF_THUMB = "rule of thumb (assumption)"
BY_PERIOD = "assumed from the construction period (rule of thumb until TABULA data)"
VENTILATION_BY_PERIOD_BASIS = (
    "assumed from the construction period (the Flemish EPB requires a ventilation system since 2006)"
)
HEATING_UNKNOWN_BASIS = "not stated: an ideal heater gives the heating need, not the fuel or electricity use"
ZONING_BASIS = "one zone unless stated; day and night zones can be chosen on the card"

INSULATION_BY_PERIOD: dict[Period, InsulationLevel] = {
    Period.before_1946: InsulationLevel.none,
    Period.y1946_1970: InsulationLevel.none,
    Period.y1971_1990: InsulationLevel.thin,
    Period.y1991_2005: InsulationLevel.medium,
    Period.y2006_2020: InsulationLevel.good,
    Period.after_2020: InsulationLevel.good,
}
VENTILATION_BY_PERIOD: dict[Period, Ventilation] = {
    Period.before_1946: Ventilation.natural,
    Period.y1946_1970: Ventilation.natural,
    Period.y1971_1990: Ventilation.natural,
    Period.y1991_2005: Ventilation.natural,
    Period.y2006_2020: Ventilation.mechanical_extract,
    Period.after_2020: Ventilation.mechanical_extract,
}
GLAZING_BY_PERIOD: dict[Period, Glazing] = {
    Period.before_1946: Glazing.double_old,
    Period.y1946_1970: Glazing.double_old,
    Period.y1971_1990: Glazing.double_old,
    Period.y1991_2005: Glazing.double_low_e,
    Period.y2006_2020: Glazing.double_low_e,
    Period.after_2020: Glazing.double_low_e,
}


@dataclass(frozen=True, slots=True)
class Default:
    value: Callable[[HouseBrief], object | None]  # None when it cannot be derived yet
    basis: str


def _constant(value: object) -> Callable[[HouseBrief], object]:
    return lambda _brief: value


def _from_period(
    table: dict[Period, InsulationLevel] | dict[Period, Glazing] | dict[Period, Ventilation],
) -> Callable[[HouseBrief], object]:
    def value(brief: HouseBrief) -> object | None:
        return table[brief.construction_period] if brief.construction_period else None

    return value


def _day_start(brief: HouseBrief) -> int:
    """7 h, or midnight when the user's day ends at 7 h or earlier."""
    return 7 if brief.day_end_h is None or brief.day_end_h > 7 else 0


def _day_end(brief: HouseBrief) -> int:
    """23 h, or midnight when the user's day starts at 23 h."""
    return 23 if brief.day_start_h is None or brief.day_start_h < 23 else 24


DEFAULTS: dict[str, Default] = {
    "room_height_m": Default(_constant(2.6), TRANO_REFERENCE),
    "heating_setpoint_day_c": Default(_constant(21.0), f"{TRANO_REFERENCE} (21 degC living)"),
    "heating_setpoint_night_c": Default(_constant(16.0), f"{TRANO_REFERENCE} (16 degC setback)"),
    "day_start_h": Default(_day_start, f"{TRANO_REFERENCE} schedule"),
    "day_end_h": Default(_day_end, f"{TRANO_REFERENCE} schedule"),
    "window_to_wall_ratio": Default(_constant(0.20), RULE_OF_THUMB),
    "airtightness": Default(_constant(Airtightness.average), RULE_OF_THUMB),
    "wall_insulation": Default(_from_period(INSULATION_BY_PERIOD), BY_PERIOD),
    "roof_insulation": Default(_from_period(INSULATION_BY_PERIOD), BY_PERIOD),
    "floor_insulation": Default(_from_period(INSULATION_BY_PERIOD), BY_PERIOD),
    "glazing": Default(_from_period(GLAZING_BY_PERIOD), BY_PERIOD),
    "street_facade_orientation": Default(
        _constant(Orientation.north), f"{TRANO_REFERENCE} (street facade north)"
    ),
    "occupants": Default(_constant(2), RULE_OF_THUMB),
    # phase 2
    "ventilation": Default(_from_period(VENTILATION_BY_PERIOD), VENTILATION_BY_PERIOD_BASIS),
    "heating_system": Default(_constant(HeatingSystem.unknown), HEATING_UNKNOWN_BASIS),
    "zoning": Default(_constant(Zoning.one), ZONING_BASIS),
}
UNKNOWN = {
    InsulationLevel.unknown,
    Glazing.unknown,
    Airtightness.unknown,
    Ventilation.unknown,
    HeatingSystem.unknown,
}
# Optional fields that stay empty without a default: the compiler has a rule for "not given".
NO_DEFAULT: frozenset[str] = frozenset({"municipality", "main_window_orientation"})


def is_given(value: object) -> bool:
    """A value the user actually stated: not missing and not "I don't know"."""
    return value is not None and value not in UNKNOWN


def apply_defaults(brief: HouseBrief, provenance: Provenance | None = None) -> tuple[HouseBrief, Provenance]:
    """Fill the optional fields that have no value (or ``unknown``) and mark them ``Source.default``.

    Returns new objects; the inputs are not changed. A field whose default depends on another field that is
    still missing (insulation without a construction period) stays None.
    """
    provenance = dict(provenance or {})
    updates: dict[str, object] = {}
    for name, default in DEFAULTS.items():
        current = getattr(brief, name)
        if is_given(current):
            continue
        value = default.value(brief)
        if value is None or (name == "heating_system" and current is HeatingSystem.unknown):
            continue  # "I don't know" about the heating system already is the ideal-heater value
        updates[name] = value
        said = provenance.get(name)
        provenance[name] = FieldProvenance(
            source=Source.default,
            evidence=said.evidence if said else None,  # keeps "I don't know" visible
            turn=said.turn if said else None,
            basis=default.basis,
        )
    return HouseBrief.model_validate(brief.model_dump() | updates), provenance


def assumptions(brief: HouseBrief, provenance: Provenance) -> list[str]:
    """One readable line per defaulted field, for the Brief card and the interpretation."""
    return [
        f"{name.replace('_', ' ')} = {_text(getattr(brief, name))} ({item.basis})"
        for name, item in provenance.items()
        if item.source is Source.default and item.basis
    ]


def _text(value: object) -> str:
    return str(value.value if isinstance(value, StrEnum) else value).replace("_", " ")
