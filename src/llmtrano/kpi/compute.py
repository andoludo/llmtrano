"""House KPIs from a result file, for ideal heaters (phase 1) and hydronic systems (phase 2).

Ideal heaters: ``trano.kpi.building_kpis`` with the energy read from the heater's own meter. trano's KPI
integrates the heating power with a trapezoid on the output grid; the ideal heater also integrates its power
in the solver (``EHea``, an integrator of ``QHea_flow`` in trano's ``IdealHeatingCooling``), which is exact
whatever the grid. Measured in phase 1 (docs/measurements.md): the trapezoid reads 1.5% high for the tutorial
house on an hourly grid, and several times too high when the heater's power has short spikes.

Radiators (phase 2): the heat a radiator gives to its zone is ``<radiator>.Q_flow`` (Buildings' EN 442-2
radiator, the convective plus the radiative part), integrated with a trapezoid on the hourly grid; radiators
have no spikes. The generator's fuel, electricity and heat come from the meters trano 0.56 publishes on its
data bus (``EFue``, ``EEle``, ``EHea`` in kWh) when the result holds them, else from the powers of the
Buildings components (``boi.QFue_flow``, ``boi.QWat_flow``, ``PEle``) integrated the same way; the pumps'
``P`` give the auxiliary electricity. Every variable read is listed in ``source_variables``; a missing one
is listed in ``missing_variables``, never guessed. The names were checked against trano's stored
``tests/tutorials/results/three_zones_hydronic_heating.building_res.mat`` (reduced into
``tests/fixtures/kpi``) and trano's templates (``boiler.yaml``, ``pump.yaml``, ``radiator.yaml``).
"""

import re
from pathlib import Path
from typing import Any

import numpy as np
from buildingspy.io.outputfile import Reader  # type: ignore[import-untyped]
from trano.elements.space import Space
from trano.elements.system import Boiler, Pump, Radiator
from trano.kpi import JOULES_PER_KWH, SECONDS_PER_HOUR, building_kpis
from trano.topology import Network

POWER_SUFFIX = ".QHea_flow"
METER_SUFFIX = ".EHea"
# Generator readings: the meter (kWh) first, else the power (W) to integrate; trano 0.56 boiler templates.
GENERATOR_METERS: dict[str, tuple[str, str | None]] = {
    "gas": ("EFue.y", "boi.QFue_flow"),  # fuel on the lower heating value
    "electricity": ("EEle.y", "PEle.y"),  # heat pump compressor and source fan (its own pump included)
    "heat": ("EHea.y", "boi.QWat_flow"),  # heat the generator gives to the water
}
GENERATOR_PUMP = "pumBoi.P"
LOOP_PUMP = "pumRad.P"
HEAT_PUMP_VARIANTS = frozenset({"air_water_heat_pump", "water_water_heat_pump"})


def _meter(power_variable: str) -> str:
    if not power_variable.endswith(POWER_SUFFIX):
        raise ValueError(f"kpi: unexpected heating power variable {power_variable!r}")
    return power_variable.removesuffix(POWER_SUFFIX) + METER_SUFFIX


def _hourly_mean_peak_w(time: np.ndarray, energy_j: np.ndarray) -> float:
    """Highest mean power over the simulated hours [W]; the meter is continuous, so it is interpolated."""
    hours = np.arange(time[0], time[-1] + 1e-6, SECONDS_PER_HOUR)
    if len(hours) < 2:
        return float((energy_j[-1] - energy_j[0]) / max(time[-1] - time[0], 1.0))
    # the result file repeats a time at events: keep the last value of each time for interpolation
    unique_time, last = np.unique(time[::-1], return_index=True)
    values = energy_j[::-1][last]
    sampled = np.interp(hours, unique_time, values)
    return float(np.max(np.diff(sampled)) / SECONDS_PER_HOUR)


def _cumulative_j(time: np.ndarray, power_w: np.ndarray) -> np.ndarray:
    """Running trapezoid integral of a power [J], on the result's own grid."""
    steps = np.diff(time) * (power_w[1:] + power_w[:-1]) / 2
    return np.concatenate([[0.0], np.cumsum(steps)])


def house_kpis(result_path: Path, network: Network) -> dict[str, Any]:
    """The KPI JSON stored on the run and given to the interpretation (all numbers in it are allowed)."""
    spaces = sorted((node for node in network.graph.nodes if isinstance(node, Space)), key=lambda s: s.name)
    if any(isinstance(emission, Radiator) for space in spaces for emission in space.emissions):
        return _hydronic_kpis(result_path, network, spaces)
    return _ideal_kpis(result_path, network)


def _ideal_kpis(result_path: Path, network: Network) -> dict[str, Any]:
    trano_kpi = building_kpis(result_path, network)
    reader = Reader(str(result_path), "openmodelica")
    sources = dict(trano_kpi.source_variables)
    zones: list[dict[str, Any]] = []
    total_energy_j = 0.0
    building_meter: tuple[np.ndarray, np.ndarray] | None = None
    for zone in trano_kpi.zones:
        powers = sources[f"{zone.zone_id}.heating_power"].split(" + ")
        meters = [_meter(power) for power in powers]
        series = [reader.values(meter) for meter in meters]
        time = np.asarray(series[0][0], dtype=float)
        energy = np.sum([np.asarray(values, dtype=float) for _, values in series], axis=0)
        zone_energy_j = float(energy[-1] - energy[0])
        total_energy_j += zone_energy_j
        sources[f"{zone.zone_id}.heating_energy"] = " + ".join(f"{m} (end - start)" for m in meters)
        building_meter = (time, energy) if building_meter is None else (time, building_meter[1] + energy)
        zones.append(
            {
                "zone_id": zone.zone_id,
                "heating_energy_kwh": zone_energy_j / JOULES_PER_KWH,
                "peak_heating_w": _hourly_mean_peak_w(time, energy),
                "hours_below_setpoint": zone.hours_below_setpoint,
                "heating_energy_kwh_trapezoid": zone.heating_energy_kwh,
                "peak_heating_instant_w": zone.peak_heating_w,
            }
        )
    if building_meter is None:
        raise ValueError("kpi: no heated zone")
    energy_kwh = total_energy_j / JOULES_PER_KWH
    area = trano_kpi.heated_floor_area_m2
    return {
        "period_start_s": trano_kpi.period_start_s,
        "period_end_s": trano_kpi.period_end_s,
        "heated_floor_area_m2": area,
        "heating_energy_kwh": energy_kwh,
        "heating_energy_kwh_per_m2": energy_kwh / area if area > 0 else None,
        "peak_heating_w": _hourly_mean_peak_w(*building_meter),
        "heating_energy_kwh_trapezoid": trano_kpi.heating_energy_kwh,
        "peak_heating_instant_w": trano_kpi.peak_heating_w,
        "zones": zones,
        "system": {"kind": "ideal", "fuel": None},
        "source_variables": sources,
        "missing_variables": [],
        "method": "energy from the heater's EHea meter; peak = highest hourly mean power",
    }


def _find(names: list[str], component: str, variable: str) -> str | None:
    """The full name of ``component.variable`` in the result file, wherever its container puts it."""
    pattern = re.compile(rf"(^|\.){re.escape(component)}\.{re.escape(variable)}$")
    found = [name for name in names if pattern.search(name)]
    return found[0] if len(found) == 1 else None


def _hydronic_kpis(result_path: Path, network: Network, spaces: list[Space]) -> dict[str, Any]:
    reader = Reader(str(result_path), "openmodelica")
    names = reader.varNames(".*")
    sources: dict[str, str] = {}
    missing: list[str] = []
    zones: list[dict[str, Any]] = []
    building: tuple[np.ndarray, np.ndarray] | None = None
    area = 0.0
    for space in spaces:
        radiators = sorted(
            (str(e.name) for e in space.emissions if isinstance(e, Radiator)), key=lambda name: name
        )
        if not radiators:
            continue
        variables = [_find(names, radiator, "Q_flow") for radiator in radiators]
        found = [name for name in variables if name is not None]
        missing += [
            f"{radiator}.Q_flow" for radiator, name in zip(radiators, variables, strict=True) if not name
        ]
        if not found:
            continue
        time = np.asarray(reader.values(found[0])[0], dtype=float)
        power = np.sum([np.asarray(reader.values(name)[1], dtype=float) for name in found], axis=0)
        energy = _cumulative_j(time, power)
        sources[f"{space.name}.heating_power"] = " + ".join(found)
        area += float(space.parameters.floor_area)  # type: ignore[union-attr]
        building = (time, energy) if building is None else (time, building[1] + energy)
        zones.append(
            {
                "zone_id": space.name,
                "heating_energy_kwh": float(energy[-1]) / JOULES_PER_KWH,
                "peak_heating_w": _hourly_mean_peak_w(time, energy),
                "hours_below_setpoint": None,
                "peak_heating_instant_w": float(np.max(power)),
            }
        )
    if building is None:
        raise ValueError("kpi: no radiator heat flow found in the result file")
    energy_kwh = float(building[1][-1]) / JOULES_PER_KWH
    system = _generator_kpis(reader, names, network, sources, missing, energy_kwh)
    return {
        "period_start_s": float(building[0][0]),
        "period_end_s": float(building[0][-1]),
        "heated_floor_area_m2": area,
        "heating_energy_kwh": energy_kwh,
        "heating_energy_kwh_per_m2": energy_kwh / area if area > 0 else None,
        "peak_heating_w": _hourly_mean_peak_w(*building),
        "zones": zones,
        "system": system,
        "source_variables": sources,
        "missing_variables": missing,
        "method": (
            "heat delivered by the radiators (Q_flow, trapezoid on the output grid); generator from its "
            "meters or powers; peak = highest hourly mean power"
        ),
    }


def _integrated_kwh(
    reader: Reader, names: list[str], component: str, meter: str, power: str | None
) -> tuple[float | None, str | None]:
    """A component's energy over the run [kWh]: its meter (end minus start) or its power integrated."""
    name = _find(names, component, meter)
    if name is not None:
        _, values = reader.values(name)
        return float(values[-1] - values[0]), f"{name} (end - start)"
    if power is None:
        return None, None
    name = _find(names, component, power)
    if name is None:
        return None, None
    time, values = reader.values(name)
    energy = _cumulative_j(np.asarray(time, dtype=float), np.asarray(values, dtype=float))
    return float(energy[-1]) / JOULES_PER_KWH, f"{name} (trapezoid)"


def _generator_kpis(
    reader: Reader,
    names: list[str],
    network: Network,
    sources: dict[str, str],
    missing: list[str],
    heat_delivered_kwh: float,
) -> dict[str, Any]:
    generators = sorted(
        (node for node in network.graph.nodes if isinstance(node, Boiler)), key=lambda n: str(n.name)
    )
    pumps = sorted(
        (node for node in network.graph.nodes if isinstance(node, Pump)), key=lambda n: str(n.name)
    )
    if not generators:
        return {"kind": "none", "fuel": None}
    generator = generators[0]
    variant = str(getattr(generator, "variant", "default"))
    fuel = "electricity" if variant in HEAT_PUMP_VARIANTS else "gas"
    readings: dict[str, float | None] = {}
    for key, (meter, power) in GENERATOR_METERS.items():
        if (key == "gas" and fuel != "gas") or (key == "electricity" and fuel != "electricity"):
            continue
        value, source = _integrated_kwh(reader, names, str(generator.name), meter, power)
        readings[key] = value
        if source:
            sources[f"generator.{key}"] = source
        else:
            missing.append(f"{generator.name}.{meter}")
    auxiliary = 0.0
    auxiliary_sources = []
    for component, variable in [
        (str(generator.name), GENERATOR_PUMP),
        *((str(p.name), LOOP_PUMP) for p in pumps),
    ]:
        value, source = _integrated_kwh(reader, names, component, "__none__", variable)
        if value is not None and source:
            auxiliary += value
            auxiliary_sources.append(source)
    if auxiliary_sources:
        sources["auxiliary_electricity"] = " + ".join(auxiliary_sources)
    final = readings.get(fuel)
    return {
        "kind": variant,
        "fuel": fuel,
        "generator_heat_kwh": readings.get("heat"),
        "gas_kwh": readings.get("gas"),
        "electricity_kwh": readings.get("electricity"),
        "auxiliary_electricity_kwh": auxiliary,
        "final_energy_kwh": final,
        # heat delivered to the zones per unit of fuel or electricity: a boiler's seasonal efficiency, a heat
        # pump's seasonal performance factor
        "seasonal_efficiency": heat_delivered_kwh / final if final else None,
    }
