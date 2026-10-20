"""Heater sizing on a simplified design heat loss."""

import pytest

from llmtrano.brief.model import BuildingType, Glazing, InsulationLevel, Orientation
from llmtrano.compiler.constructions import Element, u_value, window_values
from llmtrano.compiler.geometry import build_geometry
from llmtrano.compiler.sizing import design_heat_loss_w, heating_capacity_w


def test_design_loss_by_hand() -> None:
    """A 100 m2 one-storey detached house: the formula written out."""
    geometry = build_geometry(
        building_type=BuildingType.detached,
        heated_floor_area_m2=100,
        storeys=1,
        room_height_m=2.5,
        street_facade_orientation=Orientation.north,
        window_to_wall_ratio=0.2,
    )
    gross = sum(s.area_m2 for s in geometry.surfaces if s.kind.value == "external_wall")
    windows = sum(s.area_m2 for s in geometry.surfaces if s.kind.value == "window")
    by_hand = (
        u_value(Element.wall, InsulationLevel.none) * (gross - windows)
        + window_values(Glazing.double_old)[0] * windows
        + u_value(Element.roof, InsulationLevel.none) * 100
        + 0.34 * 12 / 20 * 250
    ) * (21 + 8) + u_value(Element.floor, InsulationLevel.none) * 100 * (21 - 10)
    loss = design_heat_loss_w(
        geometry,
        wall=InsulationLevel.none,
        roof=InsulationLevel.none,
        floor=InsulationLevel.none,
        glazing=Glazing.double_old,
        n50=12,
        setpoint_c=21,
    )
    assert loss == pytest.approx(by_hand)


@pytest.mark.parametrize(("loss", "capacity"), [(1000, 1500), (5357, 7500), (5358, 8000), (0, 0)])
def test_capacity_rounds_up_to_500_w(loss: float, capacity: float) -> None:
    assert heating_capacity_w(loss) == capacity
