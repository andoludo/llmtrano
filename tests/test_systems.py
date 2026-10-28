"""Hydronic systems: radiator oversizing by supply temperature, generator sizing and the loop wiring."""

import pytest

from llmtrano.brief.model import HeatingSystem
from llmtrano.compiler.systems import (
    SPECS,
    generator_power_w,
    hydronic_loop,
    log_mean_dt,
    radiator_emissions,
    radiator_flow_kg_s,
    radiator_oversizing,
    radiator_power_w,
)


def test_log_mean_temperature_difference_at_the_rating() -> None:
    assert log_mean_dt(75, 65, 20) == pytest.approx(49.83, abs=0.01)


@pytest.mark.parametrize(
    ("system", "factor"),
    [
        (HeatingSystem.gas_boiler_old, 1.0),  # the rating condition itself
        (HeatingSystem.gas_boiler_condensing, 1.60),  # 60/50 degC
        (HeatingSystem.heat_pump_air_water, 3.93),  # 40/35 degC: low-temperature radiators
    ],
)
def test_radiator_oversizing(system: HeatingSystem, factor: float) -> None:
    assert radiator_oversizing(SPECS[system]) == pytest.approx(factor, abs=0.01)


def test_radiator_power_is_rounded_up_with_a_minimum() -> None:
    old = SPECS[HeatingSystem.gas_boiler_old]
    assert radiator_power_w(3239, old) == 3900  # the living room of trano's reference house
    assert radiator_power_w(100, old) == 400
    assert radiator_power_w(3239, SPECS[HeatingSystem.heat_pump_air_water]) == pytest.approx(15300, abs=100)


def test_generator_sizing_as_the_reference_cases() -> None:
    assert (
        generator_power_w(10750, SPECS[HeatingSystem.gas_boiler_old]) == 16000
    )  # 1.4 x 10.75 kW, 1 kW steps
    assert generator_power_w(2340, SPECS[HeatingSystem.heat_pump_air_water]) == 3000  # 1.25 x 2.34 kW, 0.5 kW


def test_radiator_flow_follows_the_temperature_drop() -> None:
    assert radiator_flow_kg_s(3239, SPECS[HeatingSystem.gas_boiler_old]) == pytest.approx(0.093, abs=0.001)
    assert radiator_flow_kg_s(3239, SPECS[HeatingSystem.heat_pump_air_water]) == pytest.approx(
        0.186, abs=0.001
    )


def test_emissions_and_loop_are_wired_like_the_reference_case() -> None:
    emissions = radiator_emissions(
        "DAY", 3900, flow_kg_s=0.093, schedule="3600*{7, 23}", setpoint_c=21, setback_c=16
    )
    radiator, valve = emissions[0]["radiator"], emissions[1]["valve"]
    assert radiator["id"] == "RADIATOR_DAY:001"
    assert radiator["parameters"]["water_inlet_temperature_at_nominal_condition"] == 348.15
    control = valve["control"]["emission_control"]
    assert (control["variant"], control["parameters"]["temperature_heating_setpoint"]) == (
        "PController",
        294.15,
    )
    loop = hydronic_loop(SPECS[HeatingSystem.gas_boiler_old], 16000, ["DAY", "NIGHT"], [0.093, 0.09])
    kinds = [next(iter(item)) for item in loop]
    assert kinds == ["boiler", "pump", "three_way_valve", "temperature_sensor", "split_valve"]
    boiler = loop[0]["boiler"]
    assert boiler["variant"] == "without_storage" and boiler["inlets"] == ["SPLIT_VALVE:001"]
    assert boiler["parameters"]["coefficients_for_efficiency_curve"] == "{0.85}"
    assert loop[1]["pump"]["parameters"]["m_flow_nominal"] == pytest.approx(0.201)
    assert loop[3]["temperature_sensor"]["outlets"] == ["RADIATOR_DAY:001", "RADIATOR_NIGHT:001"]
    assert loop[4]["split_valve"]["inlets"] == ["VALVE_DAY:001", "VALVE_NIGHT:001"]
    heat_pump = hydronic_loop(SPECS[HeatingSystem.heat_pump_air_water], 3000, ["HOUSE"], [0.2])[0]["boiler"]
    assert heat_pump["variant"] == "air_water_heat_pump"
    assert heat_pump["parameters"]["temperature_supply_setpoint_heat_pump"] == 313.15
    assert heat_pump["parameters"]["use_storage_tank"] is True
