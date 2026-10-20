"""trano materials, constructions and glazings per insulation level and glazing type.

Every property is copied from trano's residential reference cases (trano 0.56.0):
``examples/residential_before_renovation.yaml`` and ``examples/residential_after_renovation.yaml``, written by
``examples/residential_cases.py``.
Levels between "none" (before renovation) and "good" (after) add the after-renovation insulation material in a
thinner layer at the same place. Nothing here comes from user text: ids are fixed by this module.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from trano.elements.construction import Gas as TranoGas
from trano.elements.construction import GasLayer as TranoGasLayer
from trano.elements.construction import Glass as TranoGlass
from trano.elements.construction import GlassLayer as TranoGlassLayer
from trano.elements.construction import GlassMaterial as TranoGlassMaterial

from llmtrano.brief.model import Glazing, InsulationLevel


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Material(_Frozen):
    id: str
    thermal_conductivity: float  # [W/(m.K)]
    density: float  # [kg/m3]
    specific_heat_capacity: float  # [J/(kg.K)]


class GlassMaterial(Material):
    solar_transmittance: tuple[float]
    solar_reflectance_outside_facing: tuple[float]
    solar_reflectance_room_facing: tuple[float]
    infrared_transmissivity: float
    infrared_absorptivity_outside_facing: float
    infrared_absorptivity_room_facing: float


class GasMaterial(Material):
    longwave_emissivity: float
    shortwave_emissivity: float


class Layer(_Frozen):
    material: str
    thickness: float  # [m]


class Construction(_Frozen):
    id: str
    layers: tuple[Layer, ...]


class GlassLayer(_Frozen):
    glass: str
    thickness: float


class GasLayer(_Frozen):
    gas: str
    thickness: float


class GlazingSpec(_Frozen):
    id: str
    u_value_frame: float  # [W/(m2.K)]
    layers: tuple[GlassLayer | GasLayer, ...]


class Element(StrEnum):
    wall = "wall"
    roof = "roof"
    floor = "floor"


# ---------- materials (residential_*_renovation.yaml)
MATERIALS: dict[str, Material] = {
    m.id: m
    for m in (
        Material(id="BRICK_FACING:001", thermal_conductivity=0.9, density=1700, specific_heat_capacity=840),
        Material(id="BRICK_INNER:001", thermal_conductivity=0.7, density=1600, specific_heat_capacity=840),
        # unventilated air layers as equivalent conductivities (EN ISO 6946): 5 cm cavity R = 0.18 m2K/W,
        # 10 cm between the ceiling joists R = 0.16 m2K/W
        Material(
            id="CAVITY_AIR_5CM:001", thermal_conductivity=0.278, density=1.2, specific_heat_capacity=1006
        ),
        Material(
            id="JOIST_AIR_10CM:001", thermal_conductivity=0.625, density=1.2, specific_heat_capacity=1006
        ),
        Material(id="PLASTER:001", thermal_conductivity=0.5, density=1300, specific_heat_capacity=1000),
        Material(id="GYPSUM_BOARD:001", thermal_conductivity=0.25, density=900, specific_heat_capacity=1000),
        Material(id="TIMBER:001", thermal_conductivity=0.13, density=500, specific_heat_capacity=1600),
        Material(id="TILE:001", thermal_conductivity=1.3, density=2300, specific_heat_capacity=840),
        Material(id="SCREED:001", thermal_conductivity=1.4, density=2000, specific_heat_capacity=1000),
        Material(id="CONCRETE:001", thermal_conductivity=1.7, density=2300, specific_heat_capacity=1000),
        # after renovation only
        Material(id="EPS:001", thermal_conductivity=0.032, density=20, specific_heat_capacity=1450),
        Material(id="MINERAL_WOOL:001", thermal_conductivity=0.035, density=30, specific_heat_capacity=1030),
        Material(id="PUR:001", thermal_conductivity=0.023, density=35, specific_heat_capacity=1400),
    )
}
GASES: dict[str, GasMaterial] = {
    g.id: g
    for g in (
        GasMaterial(
            id="AIR:001",
            thermal_conductivity=0.0256,
            density=1.23,
            specific_heat_capacity=1006,
            longwave_emissivity=0.0,
            shortwave_emissivity=0.0,
        ),
        GasMaterial(
            id="ARGON:001",
            thermal_conductivity=0.0168,
            density=1.70,
            specific_heat_capacity=519,
            longwave_emissivity=0.0,
            shortwave_emissivity=0.0,
        ),
    )
}


def _glass(id_: str, transmittance: float, reflectance: float, outside: float, room: float) -> GlassMaterial:
    """4 mm float glass; a low-e coating (infrared absorptivity 0.04) sits on the face given."""
    return GlassMaterial(
        id=id_,
        thermal_conductivity=1.0,
        density=2500,
        specific_heat_capacity=840,
        solar_transmittance=(transmittance,),
        solar_reflectance_outside_facing=(reflectance,),
        solar_reflectance_room_facing=(reflectance,),
        infrared_transmissivity=0.0,
        infrared_absorptivity_outside_facing=outside,
        infrared_absorptivity_room_facing=room,
    )


GLASSES: dict[str, GlassMaterial] = {
    g.id: g
    for g in (
        _glass("GLASS:001", 0.83, 0.075, 0.84, 0.84),
        _glass("GLASS_LOWE_ROOM_SIDE:001", 0.62, 0.2, 0.84, 0.04),
        _glass("GLASS_LOWE_OUTSIDE_SIDE:001", 0.62, 0.2, 0.04, 0.84),
    )
}

# ---------- opaque constructions, outside layer first (as in trano's examples)
WALL_BASE = (  # EXTERNAL_WALL:001 before renovation: cavity wall without insulation, U = 1.47 W/m2K
    Layer(material="BRICK_FACING:001", thickness=0.09),
    Layer(material="CAVITY_AIR_5CM:001", thickness=0.05),
    Layer(material="BRICK_INNER:001", thickness=0.14),
    Layer(material="PLASTER:001", thickness=0.015),
)
ROOF_BASE = (  # ROOF:001 before: ceiling under the cold attic without insulation, U = 1.70 W/m2K
    Layer(material="TIMBER:001", thickness=0.022),
    Layer(material="JOIST_AIR_10CM:001", thickness=0.1),
    Layer(material="GYPSUM_BOARD:001", thickness=0.015),
)
FLOOR_SLAB = Layer(material="CONCRETE:001", thickness=0.12)
FLOOR_FINISH = (Layer(material="SCREED:001", thickness=0.06), Layer(material="TILE:001", thickness=0.01))
PARTY_WALL = Construction(  # PARTY_WALL:001 of both cases: half of the 19 cm brick wall, adiabatic mid-plane
    id="PARTY_WALL:001",
    layers=(
        Layer(material="BRICK_INNER:001", thickness=0.095),
        Layer(material="PLASTER:001", thickness=0.015),
    ),
)

# Insulation material and thickness [m] per level; "good" is the after-renovation case.
INSULATION: dict[Element, tuple[str, dict[InsulationLevel, float]]] = {
    Element.wall: (
        "EPS:001",
        {InsulationLevel.thin: 0.04, InsulationLevel.medium: 0.08, InsulationLevel.good: 0.12},
    ),
    Element.roof: (
        "MINERAL_WOOL:001",
        {InsulationLevel.thin: 0.06, InsulationLevel.medium: 0.12, InsulationLevel.good: 0.22},
    ),
    Element.floor: (
        "PUR:001",
        {InsulationLevel.thin: 0.03, InsulationLevel.medium: 0.05, InsulationLevel.good: 0.08},
    ),
}
CONSTRUCTION_NAMES = {Element.wall: "EXTERNAL_WALL", Element.roof: "ROOF", Element.floor: "GROUND_FLOOR"}
# Surface resistances [m2.K/W] of residential_cases.py: wall Rsi 0.13 + Rse 0.04, ceiling under a cold attic
# 0.10 + 0.10. The slab on ground has Rsi 0.17 and no outside film (the ground model adds the rest).
SURFACE_RESISTANCE = {Element.wall: 0.17, Element.roof: 0.20, Element.floor: 0.17}


def _concrete(level: InsulationLevel) -> InsulationLevel:
    if level is InsulationLevel.unknown:
        raise ValueError("insulation level 'unknown' must be replaced by a default before compiling")
    return level


def construction(element: Element, level: InsulationLevel) -> Construction:
    """The construction of ``element`` at ``level``; its id is ``<ELEMENT>_<LEVEL>:001``."""
    level = _concrete(level)
    material, thicknesses = INSULATION[element]
    insulation = (Layer(material=material, thickness=thicknesses[level]),) if level in thicknesses else ()
    match element:
        case Element.wall:  # external insulation (ETICS), outside the facing brick
            layers = insulation + WALL_BASE
        case Element.roof:  # insulation laid on the attic floor
            layers = insulation + ROOF_BASE
        case Element.floor:  # insulation on the slab, under a new screed
            layers = (FLOOR_SLAB, *insulation, *FLOOR_FINISH)
    return Construction(id=f"{CONSTRUCTION_NAMES[element]}_{level.value.upper()}:001", layers=layers)


GLAZINGS: dict[Glazing, GlazingSpec] = {
    # assumption (no trano source): one 4 mm pane in the old wooden frame of double_old
    Glazing.single: GlazingSpec(
        id="WINDOW_SINGLE:001", u_value_frame=2.2, layers=(GlassLayer(glass="GLASS:001", thickness=0.004),)
    ),
    # WINDOW:001 before renovation: old double glazing 4-12-4 air in wooden frames (Uw about 2.7)
    Glazing.double_old: GlazingSpec(
        id="WINDOW_DOUBLE_OLD:001",
        u_value_frame=2.2,
        layers=(
            GlassLayer(glass="GLASS:001", thickness=0.004),
            GasLayer(gas="AIR:001", thickness=0.012),
            GlassLayer(glass="GLASS:001", thickness=0.004),
        ),
    ),
    # 4-16-4 argon, low-e coating on face 3 (inner pane, cavity side), from the after-renovation glass;
    # the 16 mm gap and the 1.4 frame are those of trano's tutorial double glazing (first_model.yaml)
    Glazing.double_low_e: GlazingSpec(
        id="WINDOW_DOUBLE_LOW_E:001",
        u_value_frame=1.4,
        layers=(
            GlassLayer(glass="GLASS:001", thickness=0.004),
            GasLayer(gas="ARGON:001", thickness=0.016),
            GlassLayer(glass="GLASS_LOWE_OUTSIDE_SIDE:001", thickness=0.004),
        ),
    ),
    # WINDOW:001 after renovation: triple 4-12-4-12-4 argon, two low-e coatings, insulated frames (Uw 0.8)
    Glazing.triple: GlazingSpec(
        id="WINDOW_TRIPLE:001",
        u_value_frame=1.1,
        layers=(
            GlassLayer(glass="GLASS_LOWE_ROOM_SIDE:001", thickness=0.004),
            GasLayer(gas="ARGON:001", thickness=0.012),
            GlassLayer(glass="GLASS:001", thickness=0.004),
            GasLayer(gas="ARGON:001", thickness=0.012),
            GlassLayer(glass="GLASS_LOWE_OUTSIDE_SIDE:001", thickness=0.004),
        ),
    ),
}
FRAME_FRACTION = 0.25  # windows of residential_*_renovation.yaml
ASSUMED_GLAZINGS = {Glazing.single: "single glazing is an assumption (no trano reference)"}


def glazing(kind: Glazing) -> GlazingSpec:
    if kind is Glazing.unknown:
        raise ValueError("glazing 'unknown' must be replaced by a default before compiling")
    return GLAZINGS[kind]


def resistance(construction_: Construction) -> float:
    """Conduction resistance of the layers [m2.K/W], as ``Case.resistance`` of residential_cases.py."""
    return sum(
        layer.thickness / MATERIALS[layer.material].thermal_conductivity for layer in construction_.layers
    )


def u_value(element: Element, level: InsulationLevel) -> float:
    """U-value [W/(m2.K)] with the surface resistances of residential_cases.py."""
    return 1 / (SURFACE_RESISTANCE[element] + resistance(construction(element, level)))


def window_values(kind: Glazing) -> tuple[float, float, float]:
    """Uw, Ug [W/(m2.K)] and g of a window, computed by trano (EN 673) as in residential_cases.py."""
    spec = glazing(kind)
    layers: list[TranoGlassLayer | TranoGasLayer] = []
    for layer in spec.layers:
        if isinstance(layer, GlassLayer):
            glass = GLASSES[layer.glass].model_dump(exclude={"id"})
            material = TranoGlassMaterial(name=layer.glass, **glass)
            layers.append(TranoGlassLayer(material=material, thickness=layer.thickness))
        else:
            gas = TranoGas(name=layer.gas, **GASES[layer.gas].model_dump(exclude={"id"}))
            layers.append(TranoGasLayer(material=gas, thickness=layer.thickness))
    properties = TranoGlass(name=spec.id, layers=layers, u_value_frame=spec.u_value_frame).properties
    u_window = (1 - FRAME_FRACTION) * properties.u_value + FRAME_FRACTION * spec.u_value_frame
    return float(u_window), float(properties.u_value), float(properties.g_value)
