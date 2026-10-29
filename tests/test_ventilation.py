"""Ventilation rules: scheduled outdoor air, the design air change and trano's schedule string."""

import pytest

from llmtrano.brief.model import Ventilation
from llmtrano.compiler.ventilation import (
    describe,
    design_ach,
    mass_flow_kg_s,
    scheduled_ach,
    ventilation_schedule,
)


@pytest.mark.parametrize(
    ("ventilation", "n50", "scheduled", "design"),
    [
        (Ventilation.natural, 6.0, 0.2, 0.5),  # airing tops the infiltration up to 0.5 1/h
        (Ventilation.natural, 12.0, 0.0, 0.6),  # a leaky house already exceeds it
        (Ventilation.mechanical_extract, 6.0, 0.5, 0.8),
        (Ventilation.balanced_heat_recovery, 3.0, 0.0, 0.15 + 0.5 * 0.2),
    ],
)
def test_air_change_rates(ventilation: Ventilation, n50: float, scheduled: float, design: float) -> None:
    assert scheduled_ach(n50, ventilation) == pytest.approx(scheduled)
    assert design_ach(n50, ventilation) == pytest.approx(design)


def test_unknown_must_be_defaulted_first() -> None:
    with pytest.raises(ValueError, match="unknown"):
        scheduled_ach(6.0, Ventilation.unknown)
    with pytest.raises(ValueError, match="unknown"):
        design_ach(6.0, Ventilation.unknown)


def test_mass_flow_and_schedule() -> None:
    """150 m3/h at 1.2 kg/m3 is 0.05 kg/s: the system D flow of trano's reference house (300 m3, 0.5 1/h)."""
    flow = mass_flow_kg_s(0.5, 300.0)
    assert flow == pytest.approx(0.05)
    assert ventilation_schedule(flow) == "[0, 0.05; 86400, 0.05]"


def test_descriptions_name_the_numbers() -> None:
    assert describe(Ventilation.natural, 6.0) == (
        "ventilation: natural, modelled as 0.3 1/h of infiltration plus airing up to 0.5 1/h in total"
    )
    assert "80% heat recovery" in describe(Ventilation.balanced_heat_recovery, 3.0)
    assert "extract of 0.5 1/h" in describe(Ventilation.mechanical_extract, 12.0)
