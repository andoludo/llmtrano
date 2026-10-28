"""Final energy, cost and CO2 of a simulated year, and the heat-loss split: Python arithmetic on the KPI JSON.

Both are added to the KPI JSON by the workflow once the simulation is done (``enrich``), so every number the
explanation may use is in one place:

- ``energy``: the fuel or electricity the generator used (from the KPIs), times the price and the emission
  factor from ``Settings``; the prices and factors used are written next to the result. An ideal heater has
  no fuel: the heating need is reported and the cost is left empty, with a note.
- ``heat_loss_split``: the share of each element (walls, windows, roof, floor, air) in the design heat loss
  the compiler computed (``CompiledModel.sizing``), and the heating need split by those shares. A
  steady-state estimate, labelled as such: the per-element split read from the result file is a trano
  follow-up (docs/trano-changes.md, T11).
"""

from typing import Any

from llmtrano.settings import Settings

SPLIT_METHOD = (
    "steady-state estimate: each element's share of the design heat loss (U-values, areas and air flows "
    "at -8 degC), applied to the simulated heating need"
)
NO_SYSTEM_NOTE = "no heating system stated: the heating need is given, not the fuel or electricity"


def energy_kpis(kpis: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Final energy [kWh], cost [EUR] and CO2 [kg] of the simulated period."""
    system = kpis.get("system") or {}
    prices = {
        "gas_eur_per_kwh": settings.energy_price_gas_eur_per_kwh,
        "electricity_eur_per_kwh": settings.energy_price_electricity_eur_per_kwh,
    }
    factors = {
        "gas_kg_per_kwh": settings.co2_gas_kg_per_kwh,
        "electricity_kg_per_kwh": settings.co2_electricity_kg_per_kwh,
    }
    fuel = system.get("fuel")
    final = system.get("final_energy_kwh")
    if fuel is None or final is None:
        return {
            "fuel": None,
            "final_energy_kwh": None,
            "auxiliary_electricity_kwh": None,
            "cost_eur": None,
            "co2_kg": None,
            "note": NO_SYSTEM_NOTE if fuel is None else "the generator's energy was not found in the result",
            "prices": prices,
            "emission_factors": factors,
        }
    auxiliary = float(system.get("auxiliary_electricity_kwh") or 0.0)
    price = prices[f"{fuel}_eur_per_kwh"]
    factor = factors[f"{fuel}_kg_per_kwh"]
    cost = final * price + auxiliary * prices["electricity_eur_per_kwh"]
    co2 = final * factor + auxiliary * factors["electricity_kg_per_kwh"]
    return {
        "fuel": fuel,
        "final_energy_kwh": round(final, 1),
        "auxiliary_electricity_kwh": round(auxiliary, 1),
        "cost_eur": round(cost),
        "co2_kg": round(co2),
        "note": None,
        "prices": prices,
        "emission_factors": factors,
    }


def heat_loss_split(kpis: dict[str, Any], sizing: dict[str, Any]) -> dict[str, Any]:
    """Shares per element and the heating need split by them [kWh]."""
    shares: dict[str, float] = sizing.get("heat_loss_share") or {}
    need = float(kpis.get("heating_energy_kwh") or 0.0)
    return {
        "method": SPLIT_METHOD,
        "share_percent": {name: round(100 * share) for name, share in shares.items()},
        "estimated_kwh": {name: round(need * share) for name, share in shares.items()},
        "design_heat_loss_w": sizing.get("design_heat_loss_w"),
        "design_heat_loss_by_element_w": sizing.get("design_heat_loss_by_element_w"),
    }


def enrich(kpis: dict[str, Any], sizing: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The KPI JSON with ``energy``, ``heat_loss_split`` and ``sizing`` added (the inputs are not changed)."""
    return {
        **kpis,
        "energy": energy_kpis(kpis, settings),
        "heat_loss_split": heat_loss_split(kpis, sizing),
        "sizing": sizing,
    }
