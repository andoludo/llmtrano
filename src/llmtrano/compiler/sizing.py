"""Design heat loss and heater sizing, as trano's reference cases (``examples/residential_cases.py``).

The design heat loss (EN 12831, simplified, -8 degC in Uccle) sizes the ideal heater, the radiators and the
generator, and gives the steady-state heat-loss split per element (walls, windows, roof, floor, air) that the
explanation uses to name the drivers of the heating need.

trano's default heater capacity (1 MW) with its PI controller makes the heater deliver hundreds of kW for a
few minutes after each set point step: the annual energy stays right in the heater's meter, but the peak
becomes meaningless and the hourly output grid misses the spikes. The reference cases size the boiler at 1.4
times the design heat loss to allow the reheat after the night setback; the compiler does the same with the
U-values and areas it writes.
"""

import math

from pydantic import BaseModel, ConfigDict

from llmtrano.brief.model import Glazing, InsulationLevel, Ventilation
from llmtrano.compiler.constructions import Element, u_value, window_values
from llmtrano.compiler.geometry import Geometry, SurfaceKind
from llmtrano.compiler.ventilation import design_ach

T_DESIGN_C = -8.0  # design outdoor temperature of Uccle (NBN EN 12831), residential_cases.T_DESIGN
T_GROUND_C = 10.0  # trano's default ground temperature under the slab (283.15 K, Buildings)
AIR_HEAT_CAPACITY = 0.34  # [Wh/(m3.K)] volumetric heat capacity of air
SIZING_FACTOR = 1.4  # residential_cases.boiler_power: reheat after the setback
STEP_W = 500.0
ELEMENTS = ("walls", "windows", "roof", "floor", "air")


class DesignLoss(BaseModel):
    """Heat loss [W] at the design outdoor temperature, per element (party walls are adiabatic)."""

    model_config = ConfigDict(frozen=True)

    walls_w: float
    windows_w: float
    roof_w: float
    floor_w: float
    air_w: float  # infiltration and ventilation

    @property
    def total_w(self) -> float:
        return self.walls_w + self.windows_w + self.roof_w + self.floor_w + self.air_w

    def by_element(self) -> dict[str, float]:
        return dict(
            zip(ELEMENTS, (self.walls_w, self.windows_w, self.roof_w, self.floor_w, self.air_w), strict=True)
        )

    def shares(self) -> dict[str, float]:
        """Share of each element in the total, as a fraction; all zero when nothing is lost."""
        total = self.total_w
        return {name: (value / total if total > 0 else 0.0) for name, value in self.by_element().items()}

    def __add__(self, other: "DesignLoss") -> "DesignLoss":
        return DesignLoss(
            walls_w=self.walls_w + other.walls_w,
            windows_w=self.windows_w + other.windows_w,
            roof_w=self.roof_w + other.roof_w,
            floor_w=self.floor_w + other.floor_w,
            air_w=self.air_w + other.air_w,
        )


def design_heat_loss(
    geometry: Geometry,
    *,
    wall: InsulationLevel,
    roof: InsulationLevel,
    floor: InsulationLevel,
    glazing: Glazing,
    n50: float,
    setpoint_c: float,
    ventilation: Ventilation = Ventilation.natural,
) -> DesignLoss:
    """Transmission and air losses of one zone [W] at the design outdoor temperature."""
    window_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.window))
    opaque_wall = sum(s.area_m2 for s in geometry.of(SurfaceKind.external_wall)) - window_area
    roof_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.roof))
    floor_area = sum(s.area_m2 for s in geometry.of(SurfaceKind.floor_on_ground))
    volume = geometry.footprint_m2 * geometry.facade_height_m
    outdoor_dt = setpoint_c - T_DESIGN_C
    return DesignLoss(
        walls_w=u_value(Element.wall, wall) * opaque_wall * outdoor_dt,
        windows_w=window_values(glazing)[0] * window_area * outdoor_dt,
        roof_w=u_value(Element.roof, roof) * roof_area * outdoor_dt,
        floor_w=u_value(Element.floor, floor) * floor_area * (setpoint_c - T_GROUND_C),
        air_w=AIR_HEAT_CAPACITY * design_ach(n50, ventilation) * volume * outdoor_dt,
    )


def design_heat_loss_w(
    geometry: Geometry,
    *,
    wall: InsulationLevel,
    roof: InsulationLevel,
    floor: InsulationLevel,
    glazing: Glazing,
    n50: float,
    setpoint_c: float,
    ventilation: Ventilation = Ventilation.natural,
) -> float:
    """The total of ``design_heat_loss`` [W]."""
    return design_heat_loss(
        geometry,
        wall=wall,
        roof=roof,
        floor=floor,
        glazing=glazing,
        n50=n50,
        setpoint_c=setpoint_c,
        ventilation=ventilation,
    ).total_w


def heating_capacity_w(design_loss_w: float) -> float:
    """1.4 times the design loss, rounded up to 500 W."""
    return math.ceil(SIZING_FACTOR * design_loss_w / STEP_W) * STEP_W
