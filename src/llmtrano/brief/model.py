"""The House Brief: what the user said about the house, as typed slots (the intermediate representation).

The LLM fills a ``HouseBrief``; it never writes trano YAML. ``llmtrano.compiler`` turns a confirmed Brief into
a model. Every value has a ``FieldProvenance``: who gave it (the user or a default) and on which evidence.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BuildingType(StrEnum):
    detached = "detached"
    semi_detached = "semi_detached"
    terraced = "terraced"
    apartment = "apartment"


class Period(StrEnum):
    before_1946 = "before_1946"
    y1946_1970 = "y1946_1970"
    y1971_1990 = "y1971_1990"
    y1991_2005 = "y1991_2005"
    y2006_2020 = "y2006_2020"
    after_2020 = "after_2020"


class InsulationLevel(StrEnum):
    none = "none"
    thin = "thin"
    medium = "medium"
    good = "good"
    unknown = "unknown"


class Glazing(StrEnum):
    single = "single"
    double_old = "double_old"
    double_low_e = "double_low_e"
    triple = "triple"
    unknown = "unknown"


class Airtightness(StrEnum):
    leaky = "leaky"
    average = "average"
    tight = "tight"
    unknown = "unknown"


class Orientation(StrEnum):
    north = "north"
    east = "east"
    south = "south"
    west = "west"


class Ventilation(StrEnum):
    natural = "natural"
    mechanical_extract = "mechanical_extract"
    balanced_heat_recovery = "balanced_heat_recovery"
    unknown = "unknown"


class HeatingSystem(StrEnum):
    gas_boiler_old = "gas_boiler_old"
    gas_boiler_condensing = "gas_boiler_condensing"
    heat_pump_air_water = "heat_pump_air_water"
    unknown = "unknown"


class Zoning(StrEnum):
    one = "one"
    day_night = "day_night"


class HouseBrief(BaseModel):
    """Every field is None until filled."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    building_type: BuildingType | None = Field(
        default=None, description="Detached, semi-detached (one party wall), terraced (two) or apartment."
    )
    construction_period: Period | None = Field(default=None, description="When the house was built.")
    heated_floor_area_m2: float | None = Field(
        default=None, ge=20, le=1000, description="Heated floor area of all storeys together [m2]."
    )
    storeys: int | None = Field(default=None, ge=1, le=4, description="Number of heated storeys.")
    zoning: Zoning | None = Field(
        default=None,
        description="One thermal zone, or a day zone (ground floor) and a night zone (the storeys above).",
    )
    room_height_m: float | None = Field(
        default=None, ge=2.2, le=4.0, description="Floor to ceiling height of the rooms [m]."
    )
    street_facade_orientation: Orientation | None = Field(
        default=None, description="Direction the street facade faces."
    )
    main_window_orientation: Orientation | None = Field(
        default=None, description='Facade with most of the glass ("big windows to the south").'
    )
    wall_insulation: InsulationLevel | None = Field(
        default=None, description="Insulation of the outside walls."
    )
    roof_insulation: InsulationLevel | None = Field(
        default=None, description="Insulation of the roof or of the ceiling under the attic."
    )
    floor_insulation: InsulationLevel | None = Field(
        default=None, description="Insulation of the ground floor slab."
    )
    glazing: Glazing | None = Field(default=None, description="Type of the window glass.")
    window_to_wall_ratio: float | None = Field(
        default=None, ge=0.05, le=0.6, description="Window area as a fraction of the gross facade area."
    )
    airtightness: Airtightness | None = Field(default=None, description="How draughty the house is.")
    ventilation: Ventilation | None = Field(
        default=None,
        description="Natural (windows and grilles), mechanical extract (system C) or balanced with heat "
        "recovery (system D).",
    )
    heating_system: HeatingSystem | None = Field(
        default=None,
        description="Old (non-condensing) gas boiler, condensing gas boiler or air-water heat pump, on "
        "radiators.",
    )
    heating_setpoint_day_c: float | None = Field(
        default=None, ge=15, le=25, description="Indoor temperature during the day [degC]."
    )
    heating_setpoint_night_c: float | None = Field(
        default=None, ge=12, le=22, description="Indoor temperature at night [degC]."
    )
    day_start_h: int | None = Field(default=None, ge=0, le=23, description="Hour the day set point starts.")
    day_end_h: int | None = Field(default=None, ge=1, le=24, description="Hour the day set point ends.")
    occupants: int | None = Field(
        default=None, ge=1, le=12, description="Number of people living in the house."
    )
    municipality: str | None = Field(
        default=None,
        max_length=100,
        description="Municipality, no street address (phase 1 uses Uccle weather).",
    )

    @model_validator(mode="after")
    def _day_starts_before_it_ends(self) -> Self:
        if self.day_start_h is not None and self.day_end_h is not None and self.day_start_h >= self.day_end_h:
            raise ValueError(f"day_start_h ({self.day_start_h}) must be before day_end_h ({self.day_end_h})")
        return self


BriefField = Literal[
    "building_type",
    "construction_period",
    "heated_floor_area_m2",
    "storeys",
    "zoning",
    "room_height_m",
    "street_facade_orientation",
    "main_window_orientation",
    "wall_insulation",
    "roof_insulation",
    "floor_insulation",
    "glazing",
    "window_to_wall_ratio",
    "airtightness",
    "ventilation",
    "heating_system",
    "heating_setpoint_day_c",
    "heating_setpoint_night_c",
    "day_start_h",
    "day_end_h",
    "occupants",
    "municipality",
]
BRIEF_FIELDS: tuple[str, ...] = tuple(HouseBrief.model_fields)
# No default is allowed for these: the user must state them before the Brief can be confirmed.
REQUIRED_FIELDS: tuple[str, ...] = ("building_type", "heated_floor_area_m2", "storeys", "construction_period")


class Source(StrEnum):
    user = "user"
    default = "default"


Confidence = Literal["low", "medium", "high"]


class FieldProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Source
    evidence: str | None = Field(default=None, description="The user's words.")
    confidence: Confidence | None = None
    turn: int | None = Field(default=None, description="User turn the value came from (1 is the first).")
    basis: str | None = Field(
        default=None, description="Where a default comes from; shown on the Brief card."
    )


Provenance = dict[str, FieldProvenance]
Scenario = Literal["baseline", "renovation", "unclear"]
BriefValue = str | int | float


class Fact(BaseModel):
    """One fact of a turn, as extracted, merged and stored (``facts`` table), with the model's raw text."""

    model_config = ConfigDict(frozen=True)

    field: BriefField
    value: BriefValue
    raw_value: str
    scenario: Scenario
    evidence: str
    confidence: Confidence
    turn: int


def missing_required(brief: HouseBrief) -> list[str]:
    return [name for name in REQUIRED_FIELDS if getattr(brief, name) is None]
