"""Construction library: U-values against trano's residential reference cases."""

import pytest
import yaml
from trano.validation.schema import validate_schema

from llmtrano.brief.model import Glazing, InsulationLevel
from llmtrano.compiler.constructions import (
    GASES,
    GLASSES,
    GLAZINGS,
    MATERIALS,
    Element,
    construction,
    glazing,
    u_value,
    window_values,
)

LEVELS = [InsulationLevel.none, InsulationLevel.thin, InsulationLevel.medium, InsulationLevel.good]

# U-values written in the comments of trano's examples/residential_cases.py (BEFORE = none, AFTER = good)
REFERENCE_U = {
    (Element.wall, InsulationLevel.none): 1.47,
    (Element.wall, InsulationLevel.good): 0.23,
    (Element.roof, InsulationLevel.none): 1.70,
    (Element.roof, InsulationLevel.good): 0.15,
}
REFERENCE_UW = {Glazing.double_old: 2.7, Glazing.triple: 0.8}  # "Uw about ..." in the same comments


@pytest.mark.parametrize(("key", "expected"), REFERENCE_U.items(), ids=lambda v: str(v))
def test_reference_u_values(key: tuple[Element, InsulationLevel], expected: float) -> None:
    assert u_value(*key) == pytest.approx(expected, abs=0.02)


@pytest.mark.parametrize(("kind", "expected"), REFERENCE_UW.items())
def test_reference_window_u_values(kind: Glazing, expected: float) -> None:
    assert window_values(kind)[0] == pytest.approx(expected, abs=0.05)


def test_floor_layers_match_the_reference_slabs() -> None:
    before = ["CONCRETE:001", "SCREED:001", "TILE:001"]
    after = ["CONCRETE:001", "PUR:001", "SCREED:001", "TILE:001"]
    assert [layer.material for layer in construction(Element.floor, InsulationLevel.none).layers] == before
    good = construction(Element.floor, InsulationLevel.good)
    assert [layer.material for layer in good.layers] == after
    assert good.layers[1].thickness == 0.08


@pytest.mark.parametrize("element", list(Element))
def test_more_insulation_means_lower_u(element: Element) -> None:
    values = [u_value(element, level) for level in LEVELS]
    assert values == sorted(values, reverse=True)


def test_glazing_order() -> None:
    values = [window_values(kind)[0] for kind in (Glazing.single, Glazing.double_old, Glazing.double_low_e)]
    assert values == sorted(values, reverse=True)
    assert window_values(Glazing.triple)[0] < values[-1]


def test_ids_are_generated_per_level() -> None:
    assert construction(Element.wall, InsulationLevel.good).id == "EXTERNAL_WALL_GOOD:001"
    assert construction(Element.roof, InsulationLevel.thin).id == "ROOF_THIN:001"
    assert construction(Element.floor, InsulationLevel.none).id == "GROUND_FLOOR_NONE:001"
    assert glazing(Glazing.double_low_e).id == "WINDOW_DOUBLE_LOW_E:001"


def test_unknown_levels_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        construction(Element.wall, InsulationLevel.unknown)
    with pytest.raises(ValueError, match="unknown"):
        glazing(Glazing.unknown)


def test_every_layer_refers_to_a_known_material() -> None:
    for element in Element:
        for level in LEVELS:
            assert all(layer.material in MATERIALS for layer in construction(element, level).layers)
    for spec in GLAZINGS.values():
        for layer in spec.layers:
            assert getattr(layer, "glass", None) in GLASSES or getattr(layer, "gas", None) in GASES


def test_library_passes_trano_schema() -> None:
    """All materials, constructions and glazings, as YAML sections, satisfy trano's schema."""
    model = {
        "material": [m.model_dump() for m in MATERIALS.values()],
        "glass_material": [g.model_dump() for g in GLASSES.values()],
        "gas": [g.model_dump() for g in GASES.values()],
        "constructions": [construction(e, level).model_dump() for e in Element for level in LEVELS],
        "glazings": [g.model_dump() for g in GLAZINGS.values()],
        "spaces": [],
    }
    validate_schema(yaml.safe_load(yaml.safe_dump(model)))


def test_print_table_for_review(capsys: pytest.CaptureFixture[str]) -> None:
    """The full table for review: ``uv run pytest tests/test_constructions.py -k table -s``."""
    with capsys.disabled():
        print("\nelement  " + "".join(f"{level.value:>8s}" for level in LEVELS) + "   (U, W/m2K)")
        for element in Element:
            print(f"{element.value:8s} " + "".join(f"{u_value(element, level):8.2f}" for level in LEVELS))
        print(f"{'glazing':14s} {'Uw':>6s} {'Ug':>6s} {'g':>6s}  frame")
        for kind, spec in GLAZINGS.items():
            uw, ug, g = window_values(kind)
            print(f"{kind.value:14s} {uw:6.2f} {ug:6.2f} {g:6.2f}  {spec.u_value_frame}")
