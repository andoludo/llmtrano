"""Compile a confirmed House Brief into a trano model (dict and YAML text).

The structure mirrors trano's tutorial ``first_model.yaml`` and its residential reference cases: one space per
zone (``HOUSE:001``, or ``DAY:001`` and ``NIGHT:001``) with infiltration and ventilation, an occupancy
schedule, a heater (the ideal heater of phase 1, or radiators on a hydronic loop) and the envelope from
``geometry``; materials and constructions from ``constructions``, only the levels the house uses. Every id is
generated here.
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
    HeatingSystem,
    HouseBrief,
    Provenance,
    Ventilation,
    Zoning,
    missing_required,
)
from llmtrano.compiler import systems as hydronic
from llmtrano.compiler.constructions import (
    ASSUMED_GLAZINGS,
    FRAME_FRACTION,
    GASES,
    GLASSES,
    INTERMEDIATE_FLOOR,
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
from llmtrano.compiler.geometry import (
    MAIN_WINDOW_ASSUMPTION,
    Geometry,
    HouseGeometry,
    Surface,
    SurfaceKind,
    build_house,
)
from llmtrano.compiler.sizing import DesignLoss, design_heat_loss, heating_capacity_w
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
# The night zone (bedrooms) of trano's reference house: 18 degC from 6 h to 23 h, the setback otherwise.
NIGHT_ZONE_SETPOINT_C = 18.0
NIGHT_ZONE_HOURS = (6, 23)
FIXED_ASSUMPTIONS = (
    "weather: Uccle typical year (TMYx 2007-2021) for every address",
    "ground under the slab at trano's default temperature (10 degC, Buildings)",
    "occupant heat gains of trano (105 W sensible and 30 W latent per person) during the day hours",
    "airtightness: n50 12 (leaky), 6 (average, rule of thumb) or 3 (tight) 1/h",
)
IDEAL_HEATER_ASSUMPTION = (
    "heating by an ideal heater sized at 1.4 times the design heat loss at -8 degC, as trano's residential "
    "cases; cooling off"
)
DAY_NIGHT_ASSUMPTION = (
    f"day zone on the ground floor, night zone above, kept at {NIGHT_ZONE_SETPOINT_C:g} degC from "
    f"{NIGHT_ZONE_HOURS[0]} to {NIGHT_ZONE_HOURS[1]} h (the bedrooms of trano's reference house)"
)
ONE_STOREY_ZONING_NOTE = "one storey: the house is one zone although day and night zones were chosen"


class BriefNotReadyError(ValueError):
    """The Brief misses a required field or describes something not supported yet."""


class CompiledModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    yaml_text: str
    model_dict: dict[str, Any]
    assumptions: list[str]
    schema_hash: str  # sha256 of trano's trano_final.yaml in the installed trano
    sizing: dict[str, Any] = {}  # design heat loss per element [W] and the capacities written (phase 2)


class ZoneModel(BaseModel):
    """What the compiler wrote for one zone, kept for the sizing record and the systems."""

    model_config = ConfigDict(frozen=True)

    geometry: Geometry
    loss: DesignLoss
    heater_capacity_w: float
    space: dict[str, Any]
    schedule: str  # "3600*{start, end}" of the zone's set point
    setpoint_c: float
    setback_c: float


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
    model, sizing, notes = build_model(brief)
    values = _filled(brief)
    notes = [
        *assumptions(brief, provenance),
        *GEOMETRY_ASSUMPTIONS,
        *([MAIN_WINDOW_ASSUMPTION] if values["main_window_orientation"] is not None else []),
        *notes,
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


def build_model(brief: HouseBrief) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """The trano model, the sizing record and the zoning notes of a Brief whose fields are all filled."""
    values = _filled(brief)
    house = build_house(
        building_type=values["building_type"],
        heated_floor_area_m2=values["heated_floor_area_m2"],
        storeys=values["storeys"],
        room_height_m=values["room_height_m"],
        street_facade_orientation=values["street_facade_orientation"],
        window_to_wall_ratio=values["window_to_wall_ratio"],
        zoning=values["zoning"],
        main_window_orientation=values["main_window_orientation"],
    )
    notes = []
    if values["zoning"] is Zoning.day_night:
        notes.append(DAY_NIGHT_ASSUMPTION if len(house.zones) == 2 else ONE_STOREY_ZONING_NOTE)
    wall = construction(Element.wall, values["wall_insulation"])
    roof = construction(Element.roof, values["roof_insulation"])
    floor = construction(Element.floor, values["floor_insulation"])
    window = glazing(values["glazing"])
    n50 = N50[values["airtightness"]]
    ventilation: Ventilation = values["ventilation"]
    floor_area = float(values["heated_floor_area_m2"])
    heating_system: HeatingSystem = values["heating_system"]

    zones = [
        _zone_model(zone, values, wall=wall, roof=roof, floor=floor, window=window, n50=n50, house=house)
        for zone in house.zones
    ]
    systems: list[dict[str, Any]] = []
    generator: dict[str, Any] = {}
    if heating_system is HeatingSystem.unknown:
        notes.append(IDEAL_HEATER_ASSUMPTION)
    else:
        spec = hydronic.SPECS[heating_system]
        radiators = [hydronic.radiator_power_w(zone.loss.total_w, spec) for zone in zones]
        flows = [hydronic.radiator_flow_kg_s(zone.loss.total_w, spec) for zone in zones]
        generator_w = hydronic.generator_power_w(sum(zone.loss.total_w for zone in zones), spec)
        names = [zone.geometry.name for zone in zones]
        for zone, radiator_w, flow in zip(zones, radiators, flows, strict=True):
            zone.space["emissions"] = hydronic.radiator_emissions(
                zone.geometry.name,
                radiator_w,
                flow_kg_s=flow,
                schedule=zone.schedule,
                setpoint_c=zone.setpoint_c,
                setback_c=zone.setback_c,
            )
        systems += hydronic.hydronic_loop(spec, generator_w, names, flows)
        notes.append(hydronic.describe(spec, generator_w, radiators))
        generator = {
            "kind": heating_system.value,
            "fuel": spec.fuel,
            "nominal_power_w": generator_w,
            "supply_temperature_c": spec.supply_c,
            "efficiency": spec.efficiency,
            "radiators_w": dict(zip(names, radiators, strict=True)),
            "radiator_oversizing": round(hydronic.radiator_oversizing(spec), 2),
        }
    constructions = [wall, roof, floor]
    if any(zone.geometry.of(SurfaceKind.party_wall) for zone in zones):
        constructions.append(PARTY_WALL)
    if house.internal_floors:
        constructions.append(INTERMEDIATE_FLOOR)
    material_ids = _unique(layer.material for c in constructions for layer in c.layers)
    glass_ids = _unique(layer.glass for layer in window.layers if isinstance(layer, GlassLayer))
    gas_ids = _unique(layer.gas for layer in window.layers if isinstance(layer, GasLayer))

    if ventilation is Ventilation.balanced_heat_recovery:
        systems.append(_system_d(zones))
    model: dict[str, Any] = {
        "material": [MATERIALS[i].model_dump() for i in material_ids],
        "constructions": [c.model_dump(mode="json") for c in constructions],
        "glass_material": [_glass_dict(i) for i in glass_ids],
        "gas": [GASES[i].model_dump() for i in gas_ids],
        "glazings": [window.model_dump(mode="json")],
        "weather": {"parameters": {"path": WEATHER_PATH}},
        "spaces": [zone.space for zone in zones],
    }
    if house.internal_floors:
        model["internal_walls"] = [
            {
                "space_1": _space_id(internal.upper),
                "space_2": _space_id(internal.lower),
                "construction": INTERMEDIATE_FLOOR.id,
                "surface": internal.area_m2,
                "space_1_tilt": "floor",
                "space_2_tilt": "ceiling",
            }
            for internal in house.internal_floors
        ]
    if systems:
        model["systems"] = systems
    total = sum((zone.loss for zone in zones[1:]), zones[0].loss)
    sizing = {
        "design_outdoor_temperature_c": -8.0,
        "design_heat_loss_w": round(total.total_w),
        "design_heat_loss_by_element_w": {k: round(v) for k, v in total.by_element().items()},
        "heat_loss_share": {k: round(v, 3) for k, v in total.shares().items()},
        "heater_capacity_w": sum(zone.heater_capacity_w for zone in zones),
        "generator": generator,
        "zones": {
            zone.geometry.name: {
                "floor_area_m2": zone.geometry.floor_area_m2,
                "design_heat_loss_w": round(zone.loss.total_w),
                "heater_capacity_w": zone.heater_capacity_w,
            }
            for zone in zones
        },
        "ventilation_ach": {
            "infiltration": round(n50 / 20, 3),
            "scheduled": round(scheduled_ach(n50, ventilation), 3),
            "heat_recovery": HEAT_RECOVERY if ventilation is Ventilation.balanced_heat_recovery else 0.0,
        },
        "heated_floor_area_m2": floor_area,
    }
    return model, sizing, notes


def _zone_model(
    zone: Geometry,
    values: dict[str, Any],
    *,
    wall: Construction,
    roof: Construction,
    floor: Construction,
    window: Any,
    n50: float,
    house: HouseGeometry,
) -> ZoneModel:
    ventilation: Ventilation = values["ventilation"]
    night_zone = zone.name == "NIGHT"
    day_c, night_c = float(values["heating_setpoint_day_c"]), float(values["heating_setpoint_night_c"])
    start_h, end_h = int(values["day_start_h"]), int(values["day_end_h"])
    if night_zone:
        day_c = min(NIGHT_ZONE_SETPOINT_C, day_c)
        night_c = min(night_c, day_c)
        start_h, end_h = NIGHT_ZONE_HOURS
        occupancy = f"3600*{{0, {int(values['day_start_h'])}, {int(values['day_end_h'])}, 24}}"
    else:
        occupancy = f"3600*{{{int(values['day_start_h'])}, {int(values['day_end_h'])}}}"
    loss = design_heat_loss(
        zone,
        wall=values["wall_insulation"],
        roof=values["roof_insulation"],
        floor=values["floor_insulation"],
        glazing=values["glazing"],
        n50=n50,
        setpoint_c=day_c,
        ventilation=ventilation,
    )
    capacity = heating_capacity_w(loss.total_w)
    parameters: dict[str, Any] = {
        "floor_area": zone.floor_area_m2,
        "average_room_height": float(values["room_height_m"]),
        "n50": n50,
    }
    airing = scheduled_ach(n50, ventilation)
    if airing > 0:
        parameters["ventilation_schedule"] = ventilation_schedule(mass_flow_kg_s(airing, zone.volume_m3))
    boundaries: dict[str, list[dict[str, Any]]] = {
        "external_walls": [
            *(_wall(surface, wall) for surface in zone.of(SurfaceKind.external_wall)),
            *(_wall(surface, roof) for surface in zone.of(SurfaceKind.roof)),
        ],
        "floor_on_grounds": [
            {"surface": s.area_m2, "construction": floor.id, "perimeter": zone.exposed_perimeter_m}
            for s in zone.of(SurfaceKind.floor_on_ground)
        ],
        "windows": [
            {
                "surface": s.area_m2,
                "azimuth": s.azimuth_rad,
                "tilt": s.tilt,
                "construction": window.id,
                "frame_fraction": FRAME_FRACTION,
            }
            for s in zone.of(SurfaceKind.window)
        ],
        "adiabatic_walls": [_wall(s, PARTY_WALL) for s in zone.of(SurfaceKind.party_wall)],
    }
    space: dict[str, Any] = {
        "id": _space_id(zone.name),
        "variant": "infiltration",
        "parameters": parameters,
        "occupancy": {
            "parameters": {
                "occupancy": occupancy,
                # occupants per m2: the same density in every zone
                "heat_gain_if_occupied": f"{values['occupants']}/{house.floor_area_m2:g}",
            }
        },
        "emissions": [
            {
                "ideal_heating_cooling": {
                    "id": _heating_id(zone.name),
                    "parameters": {
                        "heating_setpoint_schedule": heating_schedule(day_c, night_c, start_h, end_h),
                        "maximum_heating_power": capacity,
                        "maximum_cooling_power": 0.0,  # cooling off
                    },
                }
            }
        ],
        "external_boundaries": {key: items for key, items in boundaries.items() if items},
    }
    if ventilation is Ventilation.balanced_heat_recovery:
        flow = mass_flow_kg_s(HYGIENE_ACH, zone.volume_m3)
        space["ventilation_inlets"] = [_duct(f"DUCT_SUPPLY_{zone.name}:001", flow)]
        space["ventilation_outlets"] = [_duct(f"DUCT_EXTRACT_{zone.name}:001", flow)]
    return ZoneModel(
        geometry=zone,
        loss=loss,
        heater_capacity_w=capacity,
        space=space,
        schedule=f"3600*{{{start_h}, {end_h}}}",
        setpoint_c=day_c,
        setback_c=night_c,
    )


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


def _space_id(zone_name: str) -> str:
    return f"{zone_name}:001"


def _heating_id(zone_name: str) -> str:
    return HEATING_ID if zone_name == "HOUSE" else f"HEATING_{zone_name}:001"


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


def _duct(duct_id: str, mass_flow: float) -> dict[str, Any]:
    return {
        "duct": {"id": duct_id, "parameters": {"nominal_mass_flow_rate": mass_flow, "dp_nominal": DUCT_DP_PA}}
    }


def _system_d(zones: list[ZoneModel]) -> dict[str, Any]:
    """System D of trano's after-renovation case: balanced flow, heat recovery, fans at 150 Pa."""
    total_flow = round(sum(mass_flow_kg_s(HYGIENE_ACH, zone.geometry.volume_m3) for zone in zones), 5)
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
            "inlets": [f"DUCT_EXTRACT_{zone.geometry.name}:001" for zone in zones],
            "outlets": [f"DUCT_SUPPLY_{zone.geometry.name}:001" for zone in zones],
        }
    }


def _unique(ids: Any) -> list[str]:
    return list(dict.fromkeys(ids))
