"""Final energy, cost, CO2 and the heat-loss split: arithmetic on the KPI JSON."""

from typing import Any

from llmtrano.kpi.energy import NO_SYSTEM_NOTE, energy_kpis, enrich, heat_loss_split
from llmtrano.settings import Settings

SETTINGS = Settings(
    energy_price_gas_eur_per_kwh=0.1,
    energy_price_electricity_eur_per_kwh=0.3,
    co2_gas_kg_per_kwh=0.2,
    co2_electricity_kg_per_kwh=0.1,
)
SIZING: dict[str, Any] = {
    "design_heat_loss_w": 8000,
    "design_heat_loss_by_element_w": {
        "walls": 2000,
        "windows": 2400,
        "roof": 1600,
        "floor": 800,
        "air": 1200,
    },
    "heat_loss_share": {"walls": 0.25, "windows": 0.3, "roof": 0.2, "floor": 0.1, "air": 0.15},
}


def test_gas_boiler_cost_and_co2() -> None:
    kpis = {
        "heating_energy_kwh": 8500.0,
        "system": {"fuel": "gas", "final_energy_kwh": 10000.0, "auxiliary_electricity_kwh": 50.0},
    }
    energy = energy_kpis(kpis, SETTINGS)
    assert (energy["fuel"], energy["final_energy_kwh"], energy["auxiliary_electricity_kwh"]) == (
        "gas",
        10000.0,
        50.0,
    )
    assert energy["cost_eur"] == 1000 + 15  # gas at 0.1, the pumps at 0.3
    assert energy["co2_kg"] == 2000 + 5
    assert energy["note"] is None
    assert energy["prices"] == {"gas_eur_per_kwh": 0.1, "electricity_eur_per_kwh": 0.3}


def test_heat_pump_uses_the_electricity_price() -> None:
    kpis = {"system": {"fuel": "electricity", "final_energy_kwh": 2000.0, "auxiliary_electricity_kwh": 0.0}}
    energy = energy_kpis(kpis, SETTINGS)
    assert (energy["cost_eur"], energy["co2_kg"]) == (600, 200)


def test_ideal_heater_has_no_fuel() -> None:
    energy = energy_kpis({"heating_energy_kwh": 6000.0, "system": {"kind": "ideal", "fuel": None}}, SETTINGS)
    assert energy["cost_eur"] is None and energy["co2_kg"] is None
    assert energy["note"] == NO_SYSTEM_NOTE


def test_missing_generator_energy_is_noted() -> None:
    energy = energy_kpis({"system": {"fuel": "gas", "final_energy_kwh": None}}, SETTINGS)
    assert energy["cost_eur"] is None and "not found" in energy["note"]


def test_split_applies_the_design_shares_to_the_heating_need() -> None:
    split = heat_loss_split({"heating_energy_kwh": 10000.0}, SIZING)
    assert split["share_percent"] == {"walls": 25, "windows": 30, "roof": 20, "floor": 10, "air": 15}
    assert split["estimated_kwh"] == {
        "walls": 2500,
        "windows": 3000,
        "roof": 2000,
        "floor": 1000,
        "air": 1500,
    }
    assert sum(split["estimated_kwh"].values()) == 10000
    assert split["design_heat_loss_w"] == 8000 and split["method"].startswith("steady-state estimate")


def test_enrich_adds_energy_split_and_sizing_without_changing_the_input() -> None:
    kpis = {"heating_energy_kwh": 1000.0, "system": {"kind": "ideal", "fuel": None}}
    enriched = enrich(kpis, SIZING, SETTINGS)
    assert set(enriched) == {"heating_energy_kwh", "system", "energy", "heat_loss_split", "sizing"}
    assert enriched["sizing"] is SIZING and "energy" not in kpis
