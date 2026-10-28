"""Compile a confirmed House Brief into a trano model (dict and YAML text).

The structure mirrors trano's tutorial ``first_model.yaml``: one space ``HOUSE:001`` with infiltration, an
occupancy schedule, an ideal heater (cooling off) and the envelope from ``geometry``; materials and
constructions from ``constructions``, only the levels the house uses. Every id is generated here.
"""

import hashlib
from functools import cache
from importlib.resources import files
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from llmtrano.brief.defaults import NO_DEFAULT, apply_defaults, assumptions
from llmtrano.brief.model import (
    Airtightness,
    BuildingType,
    HouseBrief,
    Provenance,
    Ventilation,
    missing_required,
)
from llmtrano.compiler.constructions import (
    ASSUMED_GLAZINGS,
    FRAME_FRACTION,
    GASES,
    GLASSES,
    MATERIALS,
    PARTY_WALL,
    Construction,
    Element,
    GasLayer,
    GlassLayer,
    construction,
    glazing,
)
from llmtrano.compiler.geometry import ASSUMPTIONS as GEOMETRY_ASSUMPTIONS
from llmtrano.compiler.geometry import Surface, SurfaceKind, build_geometry
from llmtrano.compiler.sizing import design_heat_loss, heating_capacity_w
from llmtrano.compiler.ventilation import (
    AHU_DP_PA,
    DUCT_DP_PA,
    FAN_DP_PA,
    HEAT_RECOVERY,
    HYGIENE_ACH,
    describe,
    mass_flow_kg_s,
    scheduled_ach,
    ventilation_schedule,
)

SPACE_ID = "HOUSE:001"
HEATING_ID = "HEATING:001"
AHU_ID = "AHU:001"
# residential_before_renovation.yaml: Uccle typical year (TMYx 2007-2021) shipped with IDEAS
WEATHER_PATH = (
    'Modelica.Utilities.Files.loadResource("modelica://IDEAS/Resources/weatherdata/'
    'BEL_VLG_Uccle.064470_TMYx.2007-2021.mos")'
)
# n50 [1/h]: 12 and 3 are trano's before and after renovation cases; 6 is a rule of thumb.
N50: dict[Airtightness, float] = {
    Airtightness.leaky: 12.0,
    Airtightness.average: 6.0,
    Airtightness.tight: 3.0,
}
KELVIN = 273.15
DAY_S = 86_400
FIXED_ASSUMPTIONS = (
    "weather: Uccle typical year (TMYx 2007-2021) for every address",
    "heating by an ideal heater sized at 1.4 times the design heat loss at -8 degC, as trano's residential "
    "cases; cooling off",
    "ground under the slab at trano's default temperature (10 degC, Buildings)",
    "occupant heat gains of trano (105 W sensible and 30 W latent per person) during the day hours",
    "airtightness: n50 12 (leaky), 6 (average, rule of thumb) or 3 (tight) 1/h",
)


class BriefNotReadyError(ValueError):
    """The Brief misses a required field or describes something phase 1 does not support."""


class CompiledModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    yaml_text: str
    model_dict: dict[str, Any]
    assumptions: list[str]
    schema_hash: str  # sha256 of trano's trano_final.yaml in the installed trano
    sizing: dict[str, Any] = {}  # design heat loss per element [W] and the capacities written (phase 2)


@cache
def trano_schema_hash() -> str:
    schema = files("trano.data_models").joinpath("trano_final.yaml").read_bytes()
    return hashlib.sha256(schema).hexdigest()


def compile_brief(brief: HouseBrief, provenance: Provenance) -> CompiledModel:
    """The trano model of ``brief``; defaults are applied first (they never replace a user value).

    Raises:
        BriefNotReadyError: a required field is missing, or the building is an apartment.
    """
    missing = missing_required(brief)
    if missing:
        raise BriefNotReadyError(f"missing required fields: {', '.join(missing)}")
    if brief.building_type is BuildingType.apartment:
        raise BriefNotReadyError("apartments are not supported yet")
    brief, provenance = apply_defaults(brief, provenance)
    model, sizing = build_model(brief)
    values = _filled(brief)
    notes = [
        *assumptions(brief, provenance),
        *GEOMETRY_ASSUMPTIONS,
        *FIXED_ASSUMPTIONS,
        describe(values["ventilation"], N50[values["airtightness"]]),
        *([ASSUMED_GLAZINGS[brief.glazing]] if brief.glazing in ASSUMED_GLAZINGS else []),
    ]
    if brief.municipality:
        notes.append(f"municipality {brief.municipality} is stored; the weather is Uccle's")
    return CompiledModel(
        yaml_text=yaml.safe_dump(model, sort_keys=False, allow_unicode=True, width=120),
        model_dict=model,
        assumptions=notes,
        schema_hash=trano_schema_hash(),
        sizing=sizing,
    )


def model_dict(brief: HouseBrief) -> dict[str, Any]:
    """The trano model of a Brief whose fields are all filled (after ``apply_defaults``)."""
    return build_model(brief)[0]


def build_model(brief: HouseBrief) -> tuple[dict[str, Any], dict[str, Any]]:
    """The trano model and the sizing record of a Brief whose fields are all filled."""
    values = _filled(brief)
    geometry = build_geometry(
        building_type=values["building_type"],
        heated_floor_area_m2=values["heated_floor_area_m2"],
        storeys=values["storeys"],
        room_height_m=values["room_height_m"],
        street_facade_orientation=values["street_facade_orientation"],
        window_to_wall_ratio=values["window_to_wall_ratio"],
    )
    wall = construction(Element.wall, values["wall_insulation"])
    roof = construction(Element.roof, values["roof_insulation"])
    floor = construction(Element.floor, values["floor_insulation"])
    window = glazing(values["glazing"])
    n50 = N50[values["airtightness"]]
    ventilation: Ventilation = values["ventilation"]
    loss = design_heat_loss(
        geometry,
        wall=values["wall_insulation"],
        roof=values["roof_insulation"],
        floor=values["floor_insulation"],
        glazing=values["glazing"],
        n50=n50,
        setpoint_c=values["heating_setpoint_day_c"],
        ventilation=ventilation,
    )
    capacity = heating_capacity_w(loss.total_w)
    volume = geometry.footprint_m2 * geometry.facade_height_m
    constructions = [wall, roof, floor, *([PARTY_WALL] if geometry.of(SurfaceKind.party_wall) else [])]

    material_ids = _unique(layer.material for c in constructions for layer in c.layers)
    glass_ids = _unique(layer.glass for layer in window.layers if isinstance(layer, GlassLayer))
    gas_ids = _unique(layer.gas for layer in window.layers if isinstance(layer, GasLayer))

    floor_area = float(values["heated_floor_area_m2"])
    parameters: dict[str, Any] = {
        "floor_area": floor_area,
        "average_room_height": float(values["room_height_m"]),
        "n50": n50,
    }
    airing = scheduled_ach(n50, ventilation)
    if airing > 0:
        parameters["ventilation_schedule"] = ventilation_schedule(mass_flow_kg_s(airing, volume))
    boundaries: dict[str, list[dict[str, Any]]] = {
        "external_walls": [
            *(_wall(surface, wall) for surface in geometry.of(SurfaceKind.external_wall)),
            *(_wall(surface, roof) for surface in geometry.of(SurfaceKind.roof)),
        ],
        "floor_on_grounds": [
            {"surface": s.area_m2, "construction": floor.id, "perimeter": geometry.exposed_perimeter_m}
            for s in geometry.of(SurfaceKind.floor_on_ground)
        ],
        "windows": [
            {
                "surface": s.area_m2,
                "azimuth": s.azimuth_rad,
                "tilt": s.tilt,
                "construction": window.id,
                "frame_fraction": FRAME_FRACTION,
            }
            for s in geometry.of(SurfaceKind.window)
        ],
        "adiabatic_walls": [_wall(s, PARTY_WALL) for s in geometry.of(SurfaceKind.party_wall)],
    }
    space: dict[str, Any] = {
        "id": SPACE_ID,
        "variant": "infiltration",
        "parameters": parameters,
        "occupancy": {
            "parameters": {
                "occupancy": f"3600*{{{values['day_start_h']}, {values['day_end_h']}}}",
                "heat_gain_if_occupied": f"{values['occupants']}/{floor_area:g}",  # occupants per m2
            }
        },
        "emissions": [
            {
                "ideal_heating_cooling": {
                    "id": HEATING_ID,
                    "parameters": {
                        "heating_setpoint_schedule": heating_schedule(
                            values["heating_setpoint_day_c"],
                            values["heating_setpoint_night_c"],
                            values["day_start_h"],
                            values["day_end_h"],
                        ),
                        "maximum_heating_power": capacity,
                        "maximum_cooling_power": 0.0,  # cooling off
                    },
                }
            }
        ],
        "external_boundaries": {key: items for key, items in boundaries.items() if items},
    }
    systems: list[dict[str, Any]] = []
    if ventilation is Ventilation.balanced_heat_recovery:
        supply_flow = mass_flow_kg_s(HYGIENE_ACH, volume)
        space["ventilation_inlets"] = [_duct(f"DUCT_SUPPLY_{_zone_name(SPACE_ID)}:001", supply_flow)]
        space["ventilation_outlets"] = [_duct(f"DUCT_EXTRACT_{_zone_name(SPACE_ID)}:001", supply_flow)]
        systems.append(
            _air_handling_unit(
                supply_flow,
                extracts=[f"DUCT_EXTRACT_{_zone_name(SPACE_ID)}:001"],
                supplies=[f"DUCT_SUPPLY_{_zone_name(SPACE_ID)}:001"],
            )
        )
    model: dict[str, Any] = {
        "material": [MATERIALS[i].model_dump() for i in material_ids],
        "constructions": [c.model_dump(mode="json") for c in constructions],
        "glass_material": [_glass_dict(i) for i in glass_ids],
        "gas": [GASES[i].model_dump() for i in gas_ids],
        "glazings": [window.model_dump(mode="json")],
        "weather": {"parameters": {"path": WEATHER_PATH}},
        "spaces": [space],
    }
    if systems:
        model["systems"] = systems
    sizing = {
        "design_outdoor_temperature_c": -8.0,
        "design_heat_loss_w": round(loss.total_w),
        "design_heat_loss_by_element_w": {k: round(v) for k, v in loss.by_element().items()},
        "heat_loss_share": {k: round(v, 3) for k, v in loss.shares().items()},
        "heater_capacity_w": capacity,
        "ventilation_ach": {
            "infiltration": round(n50 / 20, 3),
            "scheduled": round(airing, 3),
            "heat_recovery": HEAT_RECOVERY if ventilation is Ventilation.balanced_heat_recovery else 0.0,
        },
    }
    return model, sizing


def _zone_name(space_id: str) -> str:
    return space_id.split(":")[0]


def _duct(duct_id: str, mass_flow: float) -> dict[str, Any]:
    return {
        "duct": {"id": duct_id, "parameters": {"nominal_mass_flow_rate": mass_flow, "dp_nominal": DUCT_DP_PA}}
    }


def _air_handling_unit(total_flow: float, *, extracts: list[str], supplies: list[str]) -> dict[str, Any]:
    """System D of trano's after-renovation case: balanced flow, heat recovery, fans at 150 Pa."""
    return {
        "air_handling_unit": {
            "id": AHU_ID,
            "variant": "system_d",
            "parameters": {
                "m_flow_nominal": total_flow,
                "dp_nominal": AHU_DP_PA,
                "supply_dp_nominal": FAN_DP_PA,
                "return_dp_nominal": FAN_DP_PA,
                "heat_exchanger_effectiveness": HEAT_RECOVERY,
            },
            "inlets": extracts,
            "outlets": supplies,
        }
    }


def heating_schedule(day_c: float, night_c: float, start_h: int, end_h: int) -> str:
    """trano's day table of the heating set point: rows of time since midnight [s] and set point [K]."""
    segments = [
        (0, start_h * 3600, night_c),
        (start_h * 3600, end_h * 3600, day_c),
        (end_h * 3600, DAY_S, night_c),
    ]
    rows = [
        f"{time}, {celsius + KELVIN:.2f}"
        for begin, end, celsius in segments
        if end > begin
        for time in (begin, end)
    ]
    return f"[{'; '.join(rows)}]"


def _filled(brief: HouseBrief) -> dict[str, Any]:
    values = brief.model_dump()
    empty = [name for name, value in values.items() if value is None and name not in NO_DEFAULT]
    if empty:
        raise BriefNotReadyError(f"fields without a value or a default: {', '.join(empty)}")
    values.update({name: getattr(brief, name) for name in values})  # keep the enum members
    return values


def _wall(surface: Surface, construction_: Construction) -> dict[str, Any]:
    return {
        "surface": surface.area_m2,
        "azimuth": surface.azimuth_rad,
        "tilt": surface.tilt,
        "construction": construction_.id,
    }


def _glass_dict(glass_id: str) -> dict[str, Any]:
    glass = GLASSES[glass_id].model_dump()
    # trano reads the optical properties as lists (one value per wavelength band)
    for key in ("solar_transmittance", "solar_reflectance_outside_facing", "solar_reflectance_room_facing"):
        glass[key] = list(glass[key])
    return glass


def _unique(ids: Any) -> list[str]:
    return list(dict.fromkeys(ids))
