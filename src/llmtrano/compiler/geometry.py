"""Geometry heuristics: a rectangular house from a few Brief values, as one zone or a day and a night zone.

The plan's "geometry strategy" step: the user gives a floor area, storeys and an orientation, never the
walls, so walls, windows, roof and slab come from fixed rules here (each one an assumption on the card).
Azimuths follow trano's convention (``residential_cases.py``): south 0, west pi/2, north pi, east -pi/2 [rad].

Zoning (phase 2): ``one`` is the whole house; ``day_night`` puts the ground floor in a day zone and the
storeys above in a night zone, with the intermediate floor between them. Window placement: by default the
window area of each facade is the window-to-wall ratio times its gross area; with a main window
orientation, half of the glass goes to that facade and the rest is spread over the other exposed facades.
"""

import math
from enum import StrEnum
from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llmtrano.brief.model import BuildingType, Orientation, Zoning

ASPECT_RATIO = 1.5  # long side / short side of the footprint (assumption)
MAIN_FACADE_SHARE = 0.5  # of the window area, on the facade with "the big windows" (assumption)
MAX_WINDOW_FRACTION = 0.8  # a window never covers more than this fraction of its facade
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
MAIN_WINDOW_ASSUMPTION = (
    f"{MAIN_FACADE_SHARE:.0%} of the window area on the facade with the big windows, the rest spread evenly"
)
ZONE_NAMES = {Zoning.one: ("HOUSE",), Zoning.day_night: ("DAY", "NIGHT")}


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
    """The surfaces of one thermal zone."""

    model_config = ConfigDict(frozen=True)

    name: str = "HOUSE"  # HOUSE, DAY or NIGHT: the space id is ``<name>:001``
    storeys: int = 1
    floor_area_m2: float
    footprint_m2: float
    long_side_m: float  # along the street facade
    short_side_m: float
    facade_height_m: float  # of this zone
    exposed_perimeter_m: float  # of the slab, party walls excluded
    surfaces: tuple[Surface, ...]

    def of(self, kind: SurfaceKind) -> list[Surface]:
        return [surface for surface in self.surfaces if surface.kind is kind]

    @property
    def volume_m3(self) -> float:
        return self.footprint_m2 * self.facade_height_m


class InternalFloor(BaseModel):
    """The floor between an upper and a lower zone (trano ``internal_walls`` with floor and ceiling tilts)."""

    model_config = ConfigDict(frozen=True)

    upper: str
    lower: str
    area_m2: float


class HouseGeometry(BaseModel):
    model_config = ConfigDict(frozen=True)

    zones: tuple[Geometry, ...]
    internal_floors: tuple[InternalFloor, ...]
    footprint_m2: float
    long_side_m: float
    short_side_m: float

    @property
    def floor_area_m2(self) -> float:
        return sum(zone.floor_area_m2 for zone in self.zones)

    @property
    def volume_m3(self) -> float:
        return sum(zone.volume_m3 for zone in self.zones)


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


def window_areas(
    gross: dict[Orientation, float], ratio: float, main: Orientation | None
) -> dict[Orientation, float]:
    """Window area per exposed facade [m2].

    Without a main orientation each facade gets ``ratio`` times its gross area. With one, that facade gets
    half of the total window area (capped at 80% of the facade) and the others share the rest in proportion
    to their gross areas, with the same cap.
    """
    if ratio <= 0 or not gross:
        return {}
    if main is None or main not in gross or len(gross) == 1:
        return {facade: ratio * area for facade, area in gross.items()}
    total = ratio * sum(gross.values())
    areas = {main: min(MAIN_FACADE_SHARE * total, MAX_WINDOW_FRACTION * gross[main])}
    others = {facade: area for facade, area in gross.items() if facade is not main}
    rest = total - areas[main]
    others_gross = sum(others.values())
    for facade, area in others.items():
        areas[facade] = min(rest * area / others_gross, MAX_WINDOW_FRACTION * area)
    return areas


def _zone(
    *,
    name: str,
    storeys: int,
    footprint: float,
    long_side: float,
    short_side: float,
    room_height_m: float,
    street: Orientation,
    party: list[Orientation],
    window_to_wall_ratio: float,
    main_window_orientation: Orientation | None,
    has_floor: bool,
    has_roof: bool,
) -> Geometry:
    height = storeys * room_height_m
    surfaces: list[Surface] = []
    exposed_perimeter = 0.0
    gross_by_facade: dict[Orientation, float] = {}
    for quarter in range(4):
        facade = rotate(street, quarter)
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
        gross_by_facade[facade] = gross
        surfaces.append(
            Surface(
                kind=SurfaceKind.external_wall, area_m2=gross, azimuth_rad=azimuth, tilt="wall", facade=facade
            )
        )
    for facade, area in window_areas(gross_by_facade, window_to_wall_ratio, main_window_orientation).items():
        if area > 0:
            surfaces.append(
                Surface(
                    kind=SurfaceKind.window,
                    area_m2=_round(area),
                    azimuth_rad=AZIMUTH[facade],
                    tilt="wall",
                    facade=facade,
                )
            )
    if has_roof:
        surfaces.append(
            Surface(kind=SurfaceKind.roof, area_m2=_round(footprint), azimuth_rad=0.0, tilt="ceiling")
        )
    if has_floor:
        surfaces.append(Surface(kind=SurfaceKind.floor_on_ground, area_m2=_round(footprint), tilt="floor"))
    return Geometry(
        name=name,
        storeys=storeys,
        floor_area_m2=_round(footprint * storeys),
        footprint_m2=_round(footprint),
        long_side_m=_round(long_side),
        short_side_m=_round(short_side),
        facade_height_m=_round(height),
        exposed_perimeter_m=_round(exposed_perimeter),
        surfaces=tuple(surfaces),
    )


def build_house(
    *,
    building_type: BuildingType,
    heated_floor_area_m2: float,
    storeys: int,
    room_height_m: float,
    street_facade_orientation: Orientation,
    window_to_wall_ratio: float,
    zoning: Zoning = Zoning.one,
    main_window_orientation: Orientation | None = None,
) -> HouseGeometry:
    """The zones of the house. A one-storey house is always one zone."""
    if heated_floor_area_m2 <= 0 or storeys < 1 or room_height_m <= 0:
        raise ValueError("floor area, storeys and room height must be positive")
    if not 0 <= window_to_wall_ratio < 1:
        raise ValueError("window_to_wall_ratio must be in [0, 1)")
    footprint = heated_floor_area_m2 / storeys
    long_side = math.sqrt(ASPECT_RATIO * footprint)
    short_side = footprint / long_side
    party = party_wall_facades(building_type, street_facade_orientation)
    zone = partial(
        _zone,
        footprint=footprint,
        long_side=long_side,
        short_side=short_side,
        room_height_m=room_height_m,
        street=street_facade_orientation,
        party=party,
        window_to_wall_ratio=window_to_wall_ratio,
        main_window_orientation=main_window_orientation,
    )
    floors: tuple[InternalFloor, ...] = ()
    if zoning is Zoning.day_night and storeys >= 2:
        day = zone(name="DAY", storeys=1, has_floor=True, has_roof=False)
        night = zone(name="NIGHT", storeys=storeys - 1, has_floor=False, has_roof=True)
        floors = (InternalFloor(upper="NIGHT", lower="DAY", area_m2=_round(footprint)),)
        zones: tuple[Geometry, ...] = (day, night)
    else:
        zones = (zone(name="HOUSE", storeys=storeys, has_floor=True, has_roof=True),)
    return HouseGeometry(
        zones=zones,
        internal_floors=floors,
        footprint_m2=_round(footprint),
        long_side_m=_round(long_side),
        short_side_m=_round(short_side),
    )


def build_geometry(
    *,
    building_type: BuildingType,
    heated_floor_area_m2: float,
    storeys: int,
    room_height_m: float,
    street_facade_orientation: Orientation,
    window_to_wall_ratio: float,
    main_window_orientation: Orientation | None = None,
) -> Geometry:
    """The whole house as one zone (phase 1)."""
    return build_house(
        building_type=building_type,
        heated_floor_area_m2=heated_floor_area_m2,
        storeys=storeys,
        room_height_m=room_height_m,
        street_facade_orientation=street_facade_orientation,
        window_to_wall_ratio=window_to_wall_ratio,
        zoning=Zoning.one,
        main_window_orientation=main_window_orientation,
    ).zones[0]


def _round(value: float) -> float:
    """Two decimals: centimetres and square decimetres are below what the heuristics can claim."""
    return round(value, 2)
