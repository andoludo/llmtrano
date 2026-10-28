"""Ventilation rules of the compiler: how much outdoor air enters a zone, and how trano models it.

trano's ``infiltration`` zone brings in ``n50 / 20`` air changes per hour of infiltration and, on top of it,
the outdoor air of ``ventilation_schedule`` (rows of time since midnight [s] and mass flow [kg/s], repeated
every day; Buildings' ``MixedAirInf`` and IDEAS' ``ZoneScheduledVentilation``). A balanced system with heat
recovery is a system D air handling unit with supply and extract ducts in every zone, as trano's
after-renovation reference case (``examples/residential_cases.py``).

| Ventilation | Scheduled outdoor air | Design air change (EN 12831, simplified) |
| --- | --- | --- |
| natural | airing up to 0.5 1/h in total (EN 12831 minimum, trano's old house) | max(n50 / 20, 0.5) |
| mechanical extract | 0.5 1/h on top of the infiltration | n50 / 20 + 0.5 |
| balanced with heat recovery | none: ducts and an AHU carry 0.5 1/h through it | n50 / 20 + 0.5 x 0.2 |
"""

from llmtrano.brief.model import Ventilation

HYGIENE_ACH = (
    0.5  # [1/h] residential_cases.py: 0.5 1/h of natural ventilation (EN 12831), 150 m3/h of system D
)
HEAT_RECOVERY = 0.8  # residential_cases.HEAT_RECOVERY: effectiveness of the system D heat exchanger
N50_TO_ACH = 20.0  # trano's default ratio between n50 and the infiltration rate
AIR_DENSITY = 1.2  # [kg/m3] as residential_cases.py (flow = m3/h x 1.2 / 3600)
DAY_S = 86_400
# System D pressure drops of residential_cases.py: 50 Pa per duct, 100 Pa in the heat exchanger, fans at 150 Pa
DUCT_DP_PA = 50
AHU_DP_PA = 100
FAN_DP_PA = 150


def infiltration_ach(n50: float) -> float:
    return n50 / N50_TO_ACH


def scheduled_ach(n50: float, ventilation: Ventilation) -> float:
    """Outdoor air brought in by ``ventilation_schedule`` [1/h], on top of the infiltration."""
    match ventilation:
        case Ventilation.natural:
            return max(0.0, HYGIENE_ACH - infiltration_ach(n50))
        case Ventilation.mechanical_extract:
            return HYGIENE_ACH
        case Ventilation.balanced_heat_recovery:
            return 0.0  # the ducts and the air handling unit carry it
        case Ventilation.unknown:
            raise ValueError("ventilation 'unknown' must be replaced by a default before compiling")


def design_ach(n50: float, ventilation: Ventilation) -> float:
    """Air change rate that loses heat at the design condition [1/h] (heat recovery already taken off)."""
    match ventilation:
        case Ventilation.natural:
            return max(infiltration_ach(n50), HYGIENE_ACH)
        case Ventilation.mechanical_extract:
            return infiltration_ach(n50) + HYGIENE_ACH
        case Ventilation.balanced_heat_recovery:
            return infiltration_ach(n50) + HYGIENE_ACH * (1 - HEAT_RECOVERY)
        case Ventilation.unknown:
            raise ValueError("ventilation 'unknown' must be replaced by a default before compiling")


def mass_flow_kg_s(ach: float, volume_m3: float) -> float:
    return round(ach * volume_m3 * AIR_DENSITY / 3600, 5)


def ventilation_schedule(mass_flow: float) -> str:
    """trano's day table of the scheduled outdoor air: a constant flow, written on the whole day."""
    return f"[0, {mass_flow:g}; {DAY_S}, {mass_flow:g}]"


def describe(ventilation: Ventilation, n50: float) -> str:
    """The assumption line of the card and the interpretation."""
    infiltration = infiltration_ach(n50)
    match ventilation:
        case Ventilation.natural:
            return (
                f"ventilation: natural, modelled as {infiltration:g} 1/h of infiltration plus airing up to "
                f"{HYGIENE_ACH:g} 1/h in total"
            )
        case Ventilation.mechanical_extract:
            return (
                f"ventilation: mechanical extract of {HYGIENE_ACH:g} 1/h on top of {infiltration:g} 1/h of "
                "infiltration"
            )
        case Ventilation.balanced_heat_recovery:
            return (
                f"ventilation: balanced {HYGIENE_ACH:g} 1/h with {HEAT_RECOVERY:.0%} heat recovery (system D) on "
                f"top of {infiltration:g} 1/h of infiltration"
            )
        case Ventilation.unknown:
            raise ValueError("ventilation 'unknown' has no description")
