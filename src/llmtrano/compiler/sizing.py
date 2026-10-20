"""Heating capacity of the ideal heater: sized on a simplified design heat loss, as trano's reference cases.

trano's default capacity (1 MW) with its PI controller makes the heater deliver hundreds of kW for a few
minutes after each set point step: the annual energy stays right in the heater's meter, but the peak becomes
meaningless and the hourly output grid misses the spikes. ``examples/residential_cases.py`` sizes the boiler
at 1.4 times the design heat loss (EN 12831, simplified, -8 degC in Uccle) to allow the reheat after the night
setback; the compiler does the same with the U-values and areas it writes.
"""

import math

from llmtrano.brief.model import Glazing, InsulationLevel
from llmtrano.compiler.constructions import Element, u_value, window_values
from llmtrano.compiler.geometry import Geometry, SurfaceKind

T_DESIGN_C = -8.0  # design outdoor temperature of Uccle (NBN EN 12831), residential_cases.T_DESIGN
T_GROUND_C = 10.0  # trano's default ground temperature under the slab (283.15 K, Buildings)
N50_TO_ACH = 20.0  # trano's default ratio between n50 and the infiltration rate
AIR_HEAT_CAPACITY = 0.34  # [Wh/(m3.K)] volumetric heat capacity of air
SIZING_FACTOR = 1.4  # residential_cases.boiler_power: reheat after the setback
STEP_W = 500.0


def design_heat_loss_w(
    geometry: Geometry,
    *,
    wall: InsulationLevel,
    roof: InsulationLevel,
    floor: InsulationLevel,
    glazing: Glazing,
    n50: float,
    setpoint_c: float,
) -> float:
    """Transmission and infiltration loss [W] at the design outdoor temperature (party walls adiabatic)."""
    window_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.window))
    opaque_wall = sum(s.area_m2 for s in geometry.of(SurfaceKind.external_wall)) - window_area
    roof_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.roof))
    floor_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.floor_on_ground))
    to_outdoor = (
        u_value(Element.wall, wall) * opaque_wall
        + window_values(glazing)[0] * window_area
        + u_value(Element.roof, roof) * roof_area
    )
    volume = geometry.footprint_m2 * geometry.facade_height_m
    infiltration = AIR_HEAT_CAPACITY * n50 / N50_TO_ACH * volume
    outdoor_dt = setpoint_c - T_DESIGN_C
    return (to_outdoor + infiltration) * outdoor_dt + u_value(Element.floor, floor) * floor_area * (
        setpoint_c - T_GROUND_C
    )


def heating_capacity_w(design_loss_w: float) -> float:
    """1.4 times the design loss, rounded up to 500 W."""
    return math.ceil(SIZING_FACTOR * design_loss_w / STEP_W) * STEP_W
