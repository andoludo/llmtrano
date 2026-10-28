"""Hydronic heating systems: radiators with thermostatic valves on a boiler or an air-water heat pump.

Everything is copied from trano's residential reference cases (``examples/residential_cases.py``, trano
0.56.0): the loop (generator, pump, three-way valve, temperature sensor, split valve), the radiator rating
(EN 442-2 at 75/65/20 degC), the valve flow (10 K drop) and the generator sizing (a boiler at 1.4 times the
design loss in 1 kW steps, a heat pump at 1.25 times in 0.5 kW steps, with a 100 l buffer and a 40 degC
heating curve). The one rule added here: a system with a lower supply temperature gets larger radiators, by
the EN 442 exponent 1.3 on the ratio of the log mean temperature differences, so that every system covers
the design loss.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict

from llmtrano.brief.model import HeatingSystem

KELVIN = 273.15
WATER_CP = 4186.0  # [J/(kg.K)]
RADIATOR_DT = 10.0  # [K] water temperature drop of the radiators at their rating
RADIATOR_MARGIN = 1.2  # residential_cases.RADIATOR_MARGIN: radiators at 1.2 times the room loss
RADIATOR_MIN_W = 400.0  # residential_cases.radiator_powers: smallest radiator
RADIATOR_STEP_W = 100.0
RADIATOR_EXPONENT = 1.3  # EN 442-2 radiator exponent (residential_cases.kept_radiators_supply_temperature)
RATING_SUPPLY_C, RATING_RETURN_C, RATING_ROOM_C = 75.0, 65.0, 20.0  # EN 442-2 rating of the radiators
PUMP_MARGIN = 1.1  # residential_cases.case_yaml: pump flow 1.1 times the sum of the radiator flows
HEAT_PUMP_TANK_M3 = 0.1
HEAT_PUMP_TANK_HEIGHT_M = 0.8
GENERATOR_ID = "GENERATOR:001"
GENERATOR_CONTROL_ID = "GENERATOR_CONTROL:001"
PUMP_ID = "PUMP:001"
PUMP_CONTROL_ID = "PUMP_CONTROL:001"
THREE_WAY_VALVE_ID = "THREE_WAY_VALVE:001"
THREE_WAY_VALVE_CONTROL_ID = "THREE_WAY_VALVE_CONTROL:001"
TEMPERATURE_SENSOR_ID = "TEMPERATURE_SENSOR:001"
SPLIT_VALVE_ID = "SPLIT_VALVE:001"


class HydronicSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    variant: str  # trano boiler variant
    supply_c: float
    return_c: float
    sizing_factor: float
    step_w: float
    efficiency: float | None = None  # boilers: constant efficiency on the lower heating value
    fuel: str  # what the generator consumes: "gas" or "electricity"


SPECS: dict[HeatingSystem, HydronicSpec] = {
    # residential_before_renovation.yaml: non-condensing gas boiler, efficiency 0.85 (LHV), 75 degC supply
    HeatingSystem.gas_boiler_old: HydronicSpec(
        label="non-condensing gas boiler",
        variant="without_storage",
        supply_c=75.0,
        return_c=65.0,
        sizing_factor=1.4,
        step_w=1000.0,
        efficiency=0.85,
        fuel="gas",
    ),
    # a condensing boiler at 60/50 degC: constant efficiency 0.95 on the lower heating value (assumption)
    HeatingSystem.gas_boiler_condensing: HydronicSpec(
        label="condensing gas boiler",
        variant="without_storage",
        supply_c=60.0,
        return_c=50.0,
        sizing_factor=1.4,
        step_w=1000.0,
        efficiency=0.95,
        fuel="gas",
    ),
    # residential_after_renovation.yaml: air-water heat pump, 40 degC supply at -8 degC, 100 l buffer
    HeatingSystem.heat_pump_air_water: HydronicSpec(
        label="air-water heat pump",
        variant="air_water_heat_pump",
        supply_c=40.0,
        return_c=35.0,
        sizing_factor=1.25,
        step_w=500.0,
        fuel="electricity",
    ),
}


def log_mean_dt(supply_c: float, return_c: float, room_c: float) -> float:
    return (supply_c - return_c) / math.log((supply_c - room_c) / (return_c - room_c))


def radiator_oversizing(spec: HydronicSpec) -> float:
    """Factor on the rated power so the radiators deliver the loss at the system's supply and return."""
    rating = log_mean_dt(RATING_SUPPLY_C, RATING_RETURN_C, RATING_ROOM_C)
    actual = log_mean_dt(spec.supply_c, spec.return_c, RATING_ROOM_C)
    return float((rating / actual) ** RADIATOR_EXPONENT)


def radiator_power_w(zone_loss_w: float, spec: HydronicSpec) -> float:
    """Rated power at 75/65/20 degC: 1.2 times the zone loss, oversized for the supply temperature."""
    needed = RADIATOR_MARGIN * zone_loss_w * radiator_oversizing(spec)
    return max(RADIATOR_MIN_W, math.ceil(needed / RADIATOR_STEP_W) * RADIATOR_STEP_W)


def generator_power_w(total_loss_w: float, spec: HydronicSpec) -> float:
    return math.ceil(spec.sizing_factor * total_loss_w / spec.step_w) * spec.step_w


def radiator_flow_kg_s(zone_loss_w: float, spec: HydronicSpec) -> float:
    """Water flow of a radiator at the system's temperature drop, for 1.2 times the zone loss [kg/s]."""
    return round(RADIATOR_MARGIN * zone_loss_w / WATER_CP / (spec.supply_c - spec.return_c), 3)


def radiator_id(zone_name: str) -> str:
    return f"RADIATOR_{zone_name}:001"


def valve_id(zone_name: str) -> str:
    return f"VALVE_{zone_name}:001"


def radiator_emissions(
    zone_name: str,
    radiator_w: float,
    *,
    flow_kg_s: float,
    schedule: str,
    setpoint_c: float,
    setback_c: float,
) -> list[dict[str, Any]]:
    """The radiator and its thermostatic valve (P control on the zone's schedule), as the reference cases."""
    return [
        {
            "radiator": {
                "id": radiator_id(zone_name),
                "parameters": {
                    "nominal_heating_power_positive_for_heating": radiator_w,
                    "water_inlet_temperature_at_nominal_condition": RATING_SUPPLY_C + KELVIN,
                    "water_outlet_temperature_at_nominal_condition": RATING_RETURN_C + KELVIN,
                    "air_temperature_at_nominal_condition": RATING_ROOM_C + KELVIN,
                },
            }
        },
        {
            "valve": {
                "id": valve_id(zone_name),
                "parameters": {"m_flow_nominal": flow_kg_s},
                "control": {
                    "emission_control": {
                        "id": f"EMISSION_CONTROL_{zone_name}:001",
                        "variant": "PController",
                        "parameters": {
                            "schedule": schedule,
                            "temperature_heating_setpoint": round(setpoint_c + KELVIN, 2),
                            "temperature_heating_setback": round(setback_c + KELVIN, 2),
                        },
                    }
                },
            }
        },
    ]


def generator_parameters(spec: HydronicSpec, power_w: float) -> dict[str, Any]:
    if spec.variant == "air_water_heat_pump":
        return {
            "nominal_heating_power": power_w,
            "temperature_supply_setpoint_heat_pump": round(spec.supply_c + KELVIN, 2),
            "use_storage_tank": True,
            "tank_volume": HEAT_PUMP_TANK_M3,
            "height_of_tank_without_insulation": HEAT_PUMP_TANK_HEIGHT_M,
        }
    return {
        "nominal_heating_power": power_w,
        "supply_temperature_setpoint": round(spec.supply_c + KELVIN, 2),
        "coefficients_for_efficiency_curve": f"{{{spec.efficiency}}}",
    }


def hydronic_loop(
    spec: HydronicSpec, generator_w: float, zone_names: list[str], flows_kg_s: list[float]
) -> list[dict[str, Any]]:
    """Generator, pump, three-way valve, temperature sensor and split valve, wired as the reference cases."""
    pump_flow = round(PUMP_MARGIN * sum(flows_kg_s), 3)
    return [
        {
            "boiler": {
                "id": GENERATOR_ID,
                "control": {"boiler_control": {"id": GENERATOR_CONTROL_ID, "variant": "simple"}},
                "parameters": generator_parameters(spec, generator_w),
                "variant": spec.variant,
                "inlets": [SPLIT_VALVE_ID],
            }
        },
        {
            "pump": {
                "id": PUMP_ID,
                "parameters": {"m_flow_nominal": pump_flow},
                "control": {"collector_control": {"id": PUMP_CONTROL_ID}},
                "inlets": [GENERATOR_ID],
            }
        },
        {
            "three_way_valve": {
                "id": THREE_WAY_VALVE_ID,
                "parameters": {"m_flow_nominal": pump_flow},
                "control": {"three_way_valve_control": {"id": THREE_WAY_VALVE_CONTROL_ID}},
                "inlets": [SPLIT_VALVE_ID, PUMP_ID],
                "outlets": [TEMPERATURE_SENSOR_ID],
            }
        },
        {
            "temperature_sensor": {
                "id": TEMPERATURE_SENSOR_ID,
                "outlets": [radiator_id(name) for name in zone_names],
            }
        },
        {
            "split_valve": {
                "id": SPLIT_VALVE_ID,
                "inlets": [valve_id(name) for name in zone_names],
                "outlets": [GENERATOR_ID],
            }
        },
    ]


def describe(spec: HydronicSpec, generator_w: float, radiators_w: list[float]) -> str:
    efficiency = f", efficiency {spec.efficiency:g} on the lower heating value" if spec.efficiency else ""
    return (
        f"heating: {spec.label} of {generator_w / 1000:g} kW ({spec.sizing_factor:g} times the design heat "
        f"loss at -8 degC){efficiency}, {spec.supply_c:g} degC supply, radiators of "
        f"{sum(radiators_w) / 1000:g} kW rated at 75/65/20 degC ({RADIATOR_MARGIN:g} times the zone loss, "
        f"{radiator_oversizing(spec):.2f} times for the supply temperature) with thermostatic valves, as "
        "trano's residential cases"
    )
