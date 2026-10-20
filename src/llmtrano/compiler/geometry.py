"""Geometry heuristics: a rectangular one-zone house from a few Brief values.

The plan's "geometry strategy" step: the user gives a floor area, storeys and an orientation, never the
walls, so walls, windows, roof and slab come from fixed rules here (each one an assumption on the card).
Azimuths follow trano's convention (``residential_cases.py``): south 0, west pi/2, north pi, east -pi/2 [rad].
"""

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llmtrano.brief.model import BuildingType, Orientation

ASPECT_RATIO = 1.5  # long side / short side of the footprint (assumption)
AZIMUTH: dict[Orientation, float] = {
    Orientation.south: 0.0,
    Orientation.west: 1.5708,
    Orientation.north: 3.1416,
    Orientation.east: -1.5708,
}
CLOCKWISE: tuple[Orientation, ...] = (
    Orientation.north,
    Orientation.east,
    Orientation.south,
    Orientation.west,
)
ASSUMPTIONS = (
    f"rectangular footprint with aspect ratio {ASPECT_RATIO}, the long side facing the street",
    "one window per exposed facade, sized by the window-to-wall ratio",
    "flat ceiling under a cold attic, slab on the ground",
)


class SurfaceKind(StrEnum):
    external_wall = "external_wall"
    window = "window"
    party_wall = "party_wall"
    roof = "roof"
    floor_on_ground = "floor_on_ground"


class Surface(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SurfaceKind
    area_m2: float = Field(gt=0)
    azimuth_rad: float | None = None  # None for the slab
    tilt: Literal["wall", "ceiling", "floor"]
    facade: Orientation | None = None


class Geometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    footprint_m2: float
    long_side_m: float  # along the street facade
    short_side_m: float
    facade_height_m: float
    exposed_perimeter_m: float  # of the slab, party walls excluded
    surfaces: tuple[Surface, ...]

    def of(self, kind: SurfaceKind) -> list[Surface]:
        return [surface for surface in self.surfaces if surface.kind is kind]


def rotate(orientation: Orientation, quarter_turns: int) -> Orientation:
    """``orientation`` turned clockwise by ``quarter_turns`` times 90 degrees."""
    return CLOCKWISE[(CLOCKWISE.index(orientation) + quarter_turns) % 4]


def party_wall_facades(building_type: BuildingType, street: Orientation) -> list[Orientation]:
    """Short sides against a heated neighbour: one for semi-detached (clockwise from the street, as the east
    party wall of trano's house with its street facade north), both for terraced, none for detached."""
    match building_type:
        case BuildingType.detached:
            return []
        case BuildingType.semi_detached:
            return [rotate(street, 1)]
        case BuildingType.terraced:
            return [rotate(street, 1), rotate(street, 3)]
        case BuildingType.apartment:
            raise ValueError("apartments are not supported in phase 1")


def build_geometry(
    *,
    building_type: BuildingType,
    heated_floor_area_m2: float,
    storeys: int,
    room_height_m: float,
    street_facade_orientation: Orientation,
    window_to_wall_ratio: float,
) -> Geometry:
    if heated_floor_area_m2 <= 0 or storeys < 1 or room_height_m <= 0:
        raise ValueError("floor area, storeys and room height must be positive")
    if not 0 <= window_to_wall_ratio < 1:
        raise ValueError("window_to_wall_ratio must be in [0, 1)")
    footprint = heated_floor_area_m2 / storeys
    long_side = math.sqrt(ASPECT_RATIO * footprint)
    short_side = footprint / long_side
    height = storeys * room_height_m
    party = party_wall_facades(building_type, street_facade_orientation)

    surfaces: list[Surface] = []
    exposed_perimeter = 0.0
    for quarter in range(4):
        facade = rotate(street_facade_orientation, quarter)
        side = long_side if quarter % 2 == 0 else short_side  # street and back facades are the long sides
        gross = _round(side * height)
        azimuth = AZIMUTH[facade]
        if facade in party:
            surfaces.append(
                Surface(
                    kind=SurfaceKind.party_wall,
                    area_m2=gross,
                    azimuth_rad=azimuth,
                    tilt="wall",
                    facade=facade,
                )
            )
            continue
        exposed_perimeter += side
        surfaces.append(
            Surface(
                kind=SurfaceKind.external_wall, area_m2=gross, azimuth_rad=azimuth, tilt="wall", facade=facade
            )
        )
        if window_to_wall_ratio > 0:
            surfaces.append(
                Surface(
                    kind=SurfaceKind.window,
                    area_m2=_round(window_to_wall_ratio * gross),
                    azimuth_rad=azimuth,
                    tilt="wall",
                    facade=facade,
                )
            )
    surfaces.append(
        Surface(kind=SurfaceKind.roof, area_m2=_round(footprint), azimuth_rad=0.0, tilt="ceiling")
    )
    surfaces.append(Surface(kind=SurfaceKind.floor_on_ground, area_m2=_round(footprint), tilt="floor"))
    return Geometry(
        footprint_m2=_round(footprint),
        long_side_m=_round(long_side),
        short_side_m=_round(short_side),
        facade_height_m=_round(height),
        exposed_perimeter_m=_round(exposed_perimeter),
        surfaces=tuple(surfaces),
    )


def _round(value: float) -> float:
    """Two decimals: centimetres and square decimetres are below what the heuristics can claim."""
    return round(value, 2)
