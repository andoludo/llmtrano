"""House KPIs on real (reduced) result files: the tutorial house (ideal heater, two days) and trano's
three-zone hydronic tutorial (radiators and a gas boiler, fourteen days)."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from buildingspy.io.outputfile import Reader
from trano.data_models.conversion import convert_network
from trano.elements.library.library import Library

from llmtrano.data import tutorial_house_yaml
from llmtrano.kpi.compute import _cumulative_j, _hourly_mean_peak_w, house_kpis

FIXTURE = Path(__file__).parent / "fixtures" / "kpi" / "first_model_buildings_2days.mat"
METER = "building.emission1.heating_001.EHea"


@pytest.fixture(scope="module")
def kpis(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    path = tmp_path_factory.mktemp("model") / "first_model.yaml"
    path.write_text(tutorial_house_yaml())
    network = convert_network("first_model", path, library=Library.from_configuration("Buildings"))
    return house_kpis(FIXTURE, network)


def test_energy_is_the_meter_increase(kpis: dict[str, object]) -> None:
    _, energy = Reader(str(FIXTURE), "openmodelica").values(METER)
    assert kpis["heating_energy_kwh"] == pytest.approx((energy[-1] - energy[0]) / 3.6e6)
    assert kpis["heating_energy_kwh_per_m2"] == pytest.approx(kpis["heating_energy_kwh"] / 100)
    assert kpis["period_end_s"] == pytest.approx(2 * 86400)


def test_trapezoid_on_the_hourly_grid_reads_high(kpis: dict[str, object]) -> None:
    trapezoid, meter = kpis["heating_energy_kwh_trapezoid"], kpis["heating_energy_kwh"]
    assert trapezoid > meter


def test_peak_is_an_hourly_mean_below_the_instant_peak(kpis: dict[str, object]) -> None:
    assert 0 < kpis["peak_heating_w"] <= kpis["peak_heating_instant_w"]
    assert kpis["peak_heating_w"] <= 8000  # the tutorial heater's capacity


def test_sources_are_traceable(kpis: dict[str, object]) -> None:
    sources = kpis["source_variables"]
    assert sources["living_001.heating_power"] == "building.emission1.heating_001.QHea_flow"
    assert sources["living_001.heating_energy"] == f"{METER} (end - start)"


def test_hourly_mean_peak() -> None:
    time = np.array([0.0, 1800.0, 3600.0, 3600.0, 7200.0])
    energy = np.array([0.0, 1.8e6, 3.6e6, 3.6e6, 3.6e6 + 7.2e6])  # 1 kW, then 2 kW
    assert _hourly_mean_peak_w(time, energy) == pytest.approx(2000.0)


# ---------- phase 2: radiators and a boiler (trano's three-zone hydronic tutorial, reduced to 16 variables)

HYDRONIC = Path(__file__).parent / "fixtures" / "kpi" / "three_zones_hydronic_buildings_14days.mat"
HYDRONIC_YAML = Path(__file__).parent / "fixtures" / "kpi" / "three_zones_hydronic_heating.yaml"


@pytest.fixture(scope="module")
def hydronic() -> dict[str, Any]:
    network = convert_network(
        "three_zones_hydronic_heating", HYDRONIC_YAML, library=Library.from_configuration("Buildings")
    )
    return house_kpis(HYDRONIC, network)


def _trapezoid_kwh(name: str) -> float:
    time, values = Reader(str(HYDRONIC), "openmodelica").values(name)
    return float(np.trapezoid(values, time)) / 3.6e6


def test_hydronic_zones_read_their_own_radiators(hydronic: dict[str, Any]) -> None:
    """SPACE:001 is heated by RADIATOR:003 in the tutorial: the mapping follows the network, not the ids."""
    sources = hydronic["source_variables"]
    assert sources["space_001.heating_power"] == "radiator_003.Q_flow"
    assert sources["space_003.heating_power"] == "radiator_002.Q_flow"
    zones = {zone["zone_id"]: zone for zone in hydronic["zones"]}
    assert zones["space_001"]["heating_energy_kwh"] == pytest.approx(_trapezoid_kwh("radiator_003.Q_flow"))
    assert hydronic["heating_energy_kwh"] == pytest.approx(
        sum(z["heating_energy_kwh"] for z in zones.values())
    )
    assert hydronic["heated_floor_area_m2"] == pytest.approx(49 + 40 + 50)
    assert hydronic["period_end_s"] == pytest.approx(14 * 86400)
    assert hydronic["missing_variables"] == []


def test_hydronic_generator_fuel_heat_and_pumps(hydronic: dict[str, Any]) -> None:
    system = hydronic["system"]
    assert (system["kind"], system["fuel"]) == ("default", "gas")
    assert system["gas_kwh"] == pytest.approx(_trapezoid_kwh("boiler_001.boi.QFue_flow"))
    assert system["generator_heat_kwh"] == pytest.approx(_trapezoid_kwh("boiler_001.boi.QWat_flow"))
    assert system["final_energy_kwh"] == system["gas_kwh"] and system["electricity_kwh"] is None
    assert system["auxiliary_electricity_kwh"] == pytest.approx(
        _trapezoid_kwh("boiler_001.pumBoi.P") + _trapezoid_kwh("pump_001.pumRad.P")
    )
    assert hydronic["source_variables"]["generator.gas"] == "boiler_001.boi.QFue_flow (trapezoid)"
    assert "pumBoi.P" in hydronic["source_variables"]["auxiliary_electricity"]


def test_ideal_heater_kpis_name_their_system(kpis: dict[str, object]) -> None:
    assert kpis["system"] == {"kind": "ideal", "fuel": None} and kpis["missing_variables"] == []


def test_cumulative_integral_matches_the_trapezoid() -> None:
    time = np.array([0.0, 1800.0, 3600.0, 7200.0])
    power = np.array([1000.0, 1000.0, 2000.0, 2000.0])
    assert _cumulative_j(time, power).tolist() == [0.0, 1.8e6, 1.8e6 + 2.7e6, 1.8e6 + 2.7e6 + 7.2e6]
