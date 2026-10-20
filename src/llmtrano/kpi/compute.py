"""House KPIs from a result file: ``trano.kpi.building_kpis`` with the energy read from the heater's meter.

trano's KPI integrates the heating power with a trapezoid on the output grid. The ideal heater also integrates
its own power in the solver (``EHea``, an integrator of ``QHea_flow`` in trano's ``IdealHeatingCooling``),
which is exact whatever the grid. Measured in step 1.0 and 1.4 (docs/measurements.md): the trapezoid reads
1.5% high for the tutorial house on an hourly grid, and several times too high when the heater's power has
short spikes. So llmtrano takes, per zone:

- ``heating_energy_kwh`` = ``EHea(end) - EHea(start)``;
- ``peak_heating_w`` = the highest hourly mean power, from the ``EHea`` increase over each simulated hour.

trano's own values are kept as ``heating_energy_kwh_trapezoid`` and ``peak_heating_instant_w``. The ``EHea``
variable is found next to the ``QHea_flow`` variable trano reports in ``source_variables``, not guessed.
"""

from pathlib import Path
from typing import Any

import numpy as np
from buildingspy.io.outputfile import Reader  # type: ignore[import-untyped]
from trano.kpi import JOULES_PER_KWH, SECONDS_PER_HOUR, building_kpis
from trano.topology import Network

POWER_SUFFIX = ".QHea_flow"
METER_SUFFIX = ".EHea"


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


def house_kpis(result_path: Path, network: Network) -> dict[str, Any]:
    """The KPI JSON stored on the run and given to the interpretation (all numbers in it are allowed)."""
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
        "source_variables": sources,
        "method": "energy from the heater's EHea meter; peak = highest hourly mean power",
    }
