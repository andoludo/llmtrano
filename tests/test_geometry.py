"""Geometry heuristics: areas, party walls, orientation."""

import math

import pytest

from llmtrano.brief.model import BuildingType, Orientation
from llmtrano.compiler.geometry import (
    AZIMUTH,
    Geometry,
    SurfaceKind,
    build_geometry,
    party_wall_facades,
    rotate,
)


def _geometry(
    building_type: BuildingType = BuildingType.detached,
    area: float = 120.0,
    storeys: int = 2,
    height: float = 2.5,
    street: Orientation = Orientation.north,
    wwr: float = 0.2,
) -> Geometry:
    return build_geometry(
        building_type=building_type,
        heated_floor_area_m2=area,
        storeys=storeys,
        room_height_m=height,
        street_facade_orientation=street,
        window_to_wall_ratio=wwr,
    )


def _area(geometry: Geometry, kind: SurfaceKind) -> float:
    return sum(surface.area_m2 for surface in geometry.of(kind))


def test_footprint_and_sides() -> None:
    geometry = _geometry(area=120, storeys=2)
    assert geometry.footprint_m2 == 60
    assert geometry.long_side_m == pytest.approx(math.sqrt(90), abs=0.01)
    assert geometry.long_side_m / geometry.short_side_m == pytest.approx(1.5, abs=0.01)
    assert geometry.facade_height_m == 5.0


def test_detached_areas_add_up() -> None:
    geometry = _geometry()
    perimeter = 2 * (geometry.long_side_m + geometry.short_side_m)
    assert _area(geometry, SurfaceKind.external_wall) == pytest.approx(perimeter * 5.0, abs=0.05)
    assert _area(geometry, SurfaceKind.window) == pytest.approx(
        0.2 * _area(geometry, SurfaceKind.external_wall), abs=0.05
    )
    assert _area(geometry, SurfaceKind.roof) == _area(geometry, SurfaceKind.floor_on_ground) == 60
    assert geometry.exposed_perimeter_m == pytest.approx(perimeter, abs=0.02)
    assert geometry.of(SurfaceKind.party_wall) == []


@pytest.mark.parametrize(
    ("building_type", "party"),
    [
        (BuildingType.detached, []),
        (BuildingType.semi_detached, [Orientation.east]),
        (BuildingType.terraced, [Orientation.east, Orientation.west]),
    ],
)
def test_party_walls_remove_the_short_facades(building_type: BuildingType, party: list[Orientation]) -> None:
    geometry = _geometry(building_type, street=Orientation.north)
    assert [s.facade for s in geometry.of(SurfaceKind.party_wall)] == party
    exposed = {s.facade for s in geometry.of(SurfaceKind.external_wall)}
    assert exposed == {Orientation.north, Orientation.east, Orientation.south, Orientation.west} - set(party)
    assert {s.facade for s in geometry.of(SurfaceKind.window)} == exposed  # no window in a party wall
    for wall in geometry.of(SurfaceKind.party_wall):
        assert wall.area_m2 == pytest.approx(geometry.short_side_m * geometry.facade_height_m, abs=0.05)
    expected_perimeter = (
        2 * (geometry.long_side_m + geometry.short_side_m) - len(party) * geometry.short_side_m
    )
    assert geometry.exposed_perimeter_m == pytest.approx(expected_perimeter, abs=0.02)


def test_apartment_is_not_supported() -> None:
    with pytest.raises(ValueError, match="apartments"):
        party_wall_facades(BuildingType.apartment, Orientation.north)


@pytest.mark.parametrize("street", list(Orientation))
def test_long_side_faces_the_street(street: Orientation) -> None:
    geometry = _geometry(street=street)
    walls = {s.facade: s for s in geometry.of(SurfaceKind.external_wall)}
    assert walls[street].area_m2 == pytest.approx(geometry.long_side_m * geometry.facade_height_m, abs=0.05)
    assert walls[rotate(street, 2)].area_m2 == walls[street].area_m2
    assert walls[rotate(street, 1)].area_m2 < walls[street].area_m2
    assert walls[street].azimuth_rad == AZIMUTH[street]


def test_azimuths_follow_trano() -> None:
    assert {
        Orientation.south: 0.0,
        Orientation.west: 1.5708,
        Orientation.north: 3.1416,
        Orientation.east: -1.5708,
    } == AZIMUTH


def test_rotation() -> None:
    assert rotate(Orientation.north, 1) is Orientation.east
    assert rotate(Orientation.west, 1) is Orientation.north
    assert rotate(Orientation.south, 2) is Orientation.north
    assert rotate(Orientation.east, 3) is Orientation.north


@pytest.mark.parametrize(
    "building_type", [BuildingType.detached, BuildingType.semi_detached, BuildingType.terraced]
)
@pytest.mark.parametrize(("area", "storeys", "wwr"), [(20, 1, 0.05), (1000, 4, 0.6), (85, 3, 0.2)])
def test_no_negative_or_zero_areas(
    building_type: BuildingType, area: float, storeys: int, wwr: float
) -> None:
    geometry = _geometry(building_type, area=area, storeys=storeys, wwr=wwr)
    assert all(surface.area_m2 > 0 for surface in geometry.surfaces)
    for window in geometry.of(SurfaceKind.window):
        wall = next(s for s in geometry.of(SurfaceKind.external_wall) if s.facade is window.facade)
        assert window.area_m2 < wall.area_m2


def test_zero_window_ratio_gives_no_window() -> None:
    assert _geometry(wwr=0).of(SurfaceKind.window) == []


@pytest.mark.parametrize(
    ("area", "storeys", "height", "wwr"), [(0, 1, 2.5, 0.2), (100, 0, 2.5, 0.2), (100, 1, 2.5, 1)]
)
def test_invalid_inputs(area: float, storeys: int, height: float, wwr: float) -> None:
    with pytest.raises(ValueError):
        _geometry(area=area, storeys=storeys, height=height, wwr=wwr)
