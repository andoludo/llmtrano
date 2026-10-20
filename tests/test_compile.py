"""Compiler: snapshots of the YAML for three fixture Briefs, trano validation, ids and failures."""

import re
from pathlib import Path

import pytest
import yaml
from syrupy.assertion import SnapshotAssertion
from trano.data_models.conversion import convert_network
from trano.elements.library.library import Library
from trano.validation.schema import validate_schema

from llmtrano.brief.model import (
    Airtightness,
    BuildingType,
    FieldProvenance,
    Glazing,
    HouseBrief,
    InsulationLevel,
    Orientation,
    Period,
    Source,
)
from llmtrano.compiler.compile import BriefNotReadyError, compile_brief, heating_schedule

FIXTURES = {
    "detached_old": HouseBrief(
        building_type=BuildingType.detached,
        construction_period=Period.before_1946,
        heated_floor_area_m2=140,
        storeys=2,
        street_facade_orientation=Orientation.south,
        municipality="Leuven",
    ),
    "semi_detached_1980s": HouseBrief(
        building_type=BuildingType.semi_detached,
        construction_period=Period.y1971_1990,
        heated_floor_area_m2=120,
        storeys=2,
        heating_setpoint_day_c=20,
        heating_setpoint_night_c=17,
    ),
    "terraced_renovated": HouseBrief(
        building_type=BuildingType.terraced,
        construction_period=Period.y1946_1970,
        heated_floor_area_m2=110,
        storeys=2,
        room_height_m=2.7,
        wall_insulation=InsulationLevel.good,
        roof_insulation=InsulationLevel.good,
        floor_insulation=InsulationLevel.medium,
        glazing=Glazing.triple,
        airtightness=Airtightness.tight,
        street_facade_orientation=Orientation.east,
        occupants=4,
    ),
}


@pytest.mark.parametrize("name", FIXTURES)
def test_yaml_snapshot(name: str, snapshot: SnapshotAssertion) -> None:
    assert compile_brief(FIXTURES[name], {}).yaml_text == snapshot


@pytest.mark.parametrize("name", FIXTURES)
def test_model_passes_trano_schema_and_generates_modelica(name: str, tmp_path: Path) -> None:
    """No OpenModelica needed: trano's schema check and its YAML to Modelica translation."""
    compiled = compile_brief(FIXTURES[name], {})
    validate_schema(yaml.safe_load(compiled.yaml_text))
    path = tmp_path / "model.yaml"
    path.write_text(compiled.yaml_text)
    network = convert_network("model_check", path, library=Library.from_configuration("Buildings"))
    assert "model building" in network.model()


def test_yaml_text_and_dict_agree() -> None:
    compiled = compile_brief(FIXTURES["detached_old"], {})
    assert yaml.safe_load(compiled.yaml_text) == compiled.model_dict


def test_only_the_used_levels_are_included() -> None:
    model = compile_brief(FIXTURES["terraced_renovated"], {}).model_dict
    assert [c["id"] for c in model["constructions"]] == [
        "EXTERNAL_WALL_GOOD:001",
        "ROOF_GOOD:001",
        "GROUND_FLOOR_MEDIUM:001",
        "PARTY_WALL:001",
    ]
    assert [g["id"] for g in model["glazings"]] == ["WINDOW_TRIPLE:001"]
    assert {m["id"] for m in model["glass_material"]} == {
        "GLASS:001",
        "GLASS_LOWE_ROOM_SIDE:001",
        "GLASS_LOWE_OUTSIDE_SIDE:001",
    }
    assert [g["id"] for g in model["gas"]] == ["ARGON:001"]
    used = {layer["material"] for c in model["constructions"] for layer in c["layers"]}
    assert {m["id"] for m in model["material"]} == used


def test_detached_has_no_party_wall() -> None:
    model = compile_brief(FIXTURES["detached_old"], {}).model_dict
    assert "adiabatic_walls" not in model["spaces"][0]["external_boundaries"]
    assert "PARTY_WALL:001" not in {c["id"] for c in model["constructions"]}


def test_space_parameters() -> None:
    space = compile_brief(FIXTURES["terraced_renovated"], {}).model_dict["spaces"][0]
    assert space["id"] == "HOUSE:001"
    assert space["variant"] == "infiltration"
    assert space["parameters"] == {"floor_area": 110.0, "average_room_height": 2.7, "n50": 3.0}
    assert space["occupancy"]["parameters"] == {"occupancy": "3600*{7, 23}", "heat_gain_if_occupied": "4/110"}
    heating = space["emissions"][0]["ideal_heating_cooling"]
    assert heating["id"] == "HEATING:001"
    assert heating["parameters"]["maximum_cooling_power"] == 0.0


def test_ids_never_come_from_user_text() -> None:
    brief = FIXTURES["detached_old"].model_copy(update={"municipality": "EVIL:001'; drop"})
    provenance = {"municipality": FieldProvenance(source=Source.user, evidence="HOUSE:666 in EVIL:001")}
    compiled = compile_brief(brief, provenance)
    assert "EVIL" not in compiled.yaml_text and "666" not in compiled.yaml_text
    ids = re.findall(r"\b[A-Z_]+:\d{3}\b", compiled.yaml_text)
    assert all(re.fullmatch(r"[A-Z_]+:001", i) for i in ids)


def test_assumptions_list_defaults_and_fixed_rules() -> None:
    compiled = compile_brief(FIXTURES["detached_old"], {})
    text = "\n".join(compiled.assumptions)
    assert "window to wall ratio = 0.2" in text
    assert "aspect ratio 1.5" in text
    assert "Uccle" in text
    assert "municipality Leuven is stored" in text


def test_single_glazing_is_flagged() -> None:
    brief = FIXTURES["detached_old"].model_copy(update={"glazing": Glazing.single})
    assert any("single glazing is an assumption" in a for a in compile_brief(brief, {}).assumptions)


def test_schema_hash_is_a_sha256() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", compile_brief(FIXTURES["detached_old"], {}).schema_hash)


def test_missing_required_fields_are_refused() -> None:
    with pytest.raises(BriefNotReadyError, match="heated_floor_area_m2, storeys"):
        compile_brief(
            HouseBrief(building_type=BuildingType.detached, construction_period=Period.after_2020), {}
        )


def test_apartments_are_refused() -> None:
    brief = FIXTURES["detached_old"].model_copy(update={"building_type": BuildingType.apartment})
    with pytest.raises(BriefNotReadyError, match="apartments"):
        compile_brief(brief, {})


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            (21, 18, 7, 23),
            "[0, 291.15; 25200, 291.15; 25200, 294.15; 82800, 294.15; 82800, 291.15; 86400, 291.15]",
        ),
        ((20, 16, 0, 22), "[0, 293.15; 79200, 293.15; 79200, 289.15; 86400, 289.15]"),
        ((20, 16, 6, 24), "[0, 289.15; 21600, 289.15; 21600, 293.15; 86400, 293.15]"),
    ],
)
def test_heating_schedule(args: tuple[float, float, int, int], expected: str) -> None:
    assert heating_schedule(*args) == expected


def test_tutorial_schedule_matches_first_model() -> None:
    """The tutorial house heats to 21 degC from 7 to 23 h and 18 degC otherwise, written the same way."""
    tutorial = "[0, 291.15; 25200, 291.15; 25200, 294.15; 82800, 294.15; 82800, 291.15; 86400, 291.15]"
    assert heating_schedule(21, 18, 7, 23) == tutorial


@pytest.mark.simulation
@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_models_pass_check_yaml(name: str, tmp_path: Path) -> None:
    from trano.api import check_yaml

    compiled = compile_brief(FIXTURES[name], {})
    result = check_yaml(compiled.model_dict, runtime="local", project_path=tmp_path, name=f"check_{name}")
    assert result.success, f"{result.error_kind}: {result.error_message}"


def test_heater_is_sized_on_the_design_loss() -> None:
    """1.4 times the design heat loss, in 500 W steps: more insulation, smaller heater."""
    capacity = {
        name: compile_brief(brief, {}).model_dict["spaces"][0]["emissions"][0]["ideal_heating_cooling"][
            "parameters"
        ]["maximum_heating_power"]
        for name, brief in FIXTURES.items()
    }
    assert capacity == {"detached_old": 22500.0, "semi_detached_1980s": 8000.0, "terraced_renovated": 3000.0}
