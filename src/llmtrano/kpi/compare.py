"""Comparison of two simulated scenarios (baseline and renovation): Python arithmetic, no LLM.

Every difference the explanation may quote is computed here and stored in the state next to both KPI JSONs,
so the number guardrail accepts it. A quantity missing in either scenario (an ideal heater has no cost) gives
no difference.
"""

from typing import Any

METRICS: dict[str, tuple[str, ...]] = {
    # name: path in the KPI JSON
    "heating_energy_kwh": ("heating_energy_kwh",),
    "heating_energy_kwh_per_m2": ("heating_energy_kwh_per_m2",),
    "peak_heating_w": ("peak_heating_w",),
    "final_energy_kwh": ("energy", "final_energy_kwh"),
    "cost_eur": ("energy", "cost_eur"),
    "co2_kg": ("energy", "co2_kg"),
}


def _get(kpis: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = kpis
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def compare(baseline: dict[str, Any], renovation: dict[str, Any]) -> dict[str, Any]:
    """Per metric: the two values, the saving (baseline minus renovation) and the saving in percent."""
    result: dict[str, Any] = {}
    for name, path in METRICS.items():
        before, after = _get(baseline, path), _get(renovation, path)
        if before is None or after is None:
            continue
        saving = before - after
        result[name] = {
            "baseline": round(before, 1),
            "renovation": round(after, 1),
            "saving": round(saving, 1),
            "saving_percent": round(100 * saving / before) if before else None,
        }
    fuel_before = (baseline.get("energy") or {}).get("fuel")
    fuel_after = (renovation.get("energy") or {}).get("fuel")
    result["fuel"] = {"baseline": fuel_before, "renovation": fuel_after}
    return result
