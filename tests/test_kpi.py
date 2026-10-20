"""House KPIs on a real (reduced) result file of trano's tutorial house: two days, hourly grid."""

from pathlib import Path

import numpy as np
import pytest
from buildingspy.io.outputfile import Reader
from trano.data_models.conversion import convert_network
from trano.elements.library.library import Library

from llmtrano.data import tutorial_house_yaml
from llmtrano.kpi.compute import _hourly_mean_peak_w, house_kpis

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
