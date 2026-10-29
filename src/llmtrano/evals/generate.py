"""Generate eval cases from house archetypes and phrasing templates (phase 3, #57 synthetic data).

    uv run python -m llmtrano.evals.generate                 # writes evals/cases/gen_*.yaml

Ten archetypes (a typical house per construction period and system) times three languages give thirty
cases, each with an opening that states some fields, scripted answers for the others, the expected Brief
values and the renovation plans. The generator is deterministic (no randomness) and the phrases are the
words the parsers understand, which ``tests/test_generate.py`` checks field by field: a perfect extraction
scores 100% on them, so a lower score measures the model, not the cases. The files are written once,
reviewed and committed: they are the golden set, the generator is where they come from.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Language = Literal["English", "Dutch", "French"]
LANGUAGES: tuple[Language, ...] = ("English", "Dutch", "French")
Phrase = Callable[[Any], str]


@dataclass(frozen=True)
class Archetype:
    key: str
    note: str
    values: dict[str, Any]  # the Brief values the scripted user knows
    opening: tuple[str, ...]  # fields stated in the opening; the others are answered when asked
    renovation: dict[str, Any] = field(default_factory=dict)
    outcome: Literal["simulate", "unsupported", "compare"] = "simulate"


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        "terraced_1930",
        "a pre-war terraced house with single glazing and no insulation; roof and glazing planned",
        {
            "building_type": "terraced",
            "construction_period": 1930,
            "heated_floor_area_m2": 95,
            "storeys": 2,
            "wall_insulation": "none",
            "roof_insulation": "none",
            "floor_insulation": "none",
            "glazing": "single",
            "airtightness": "leaky",
            "heating_system": "gas_boiler_old",
            "ventilation": "natural",
            "occupants": 2,
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "glazing",
            "wall_insulation",
        ),
        renovation={"roof_insulation": "good", "glazing": "double_low_e"},
        outcome="compare",
    ),
    Archetype(
        "semi_1965",
        "a 1960s semi-detached house with an old boiler; a deep renovation with a heat pump planned",
        {
            "building_type": "semi_detached",
            "construction_period": 1965,
            "heated_floor_area_m2": 120,
            "storeys": 2,
            "wall_insulation": "none",
            "roof_insulation": "thin",
            "floor_insulation": "none",
            "glazing": "double_old",
            "airtightness": "leaky",
            "heating_system": "gas_boiler_old",
            "ventilation": "natural",
            "occupants": 4,
            "street_facade_orientation": "north",
        },
        ("building_type", "construction_period", "heated_floor_area_m2", "storeys", "heating_system"),
        renovation={"wall_insulation": "good", "glazing": "triple", "heating_system": "heat_pump_air_water"},
        outcome="compare",
    ),
    Archetype(
        "detached_1978",
        "a 1970s detached house with a little insulation",
        {
            "building_type": "detached",
            "construction_period": 1978,
            "heated_floor_area_m2": 160,
            "storeys": 2,
            "wall_insulation": "thin",
            "roof_insulation": "thin",
            "floor_insulation": "none",
            "glazing": "double_old",
            "airtightness": "average",
            "heating_system": "gas_boiler_old",
            "ventilation": "natural",
            "municipality": "Hasselt",
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "municipality",
            "glazing",
        ),
    ),
    Archetype(
        "bungalow_1985",
        "a one-storey house from the eighties with a condensing boiler",
        {
            "building_type": "detached",
            "construction_period": 1985,
            "heated_floor_area_m2": 110,
            "storeys": 1,
            "wall_insulation": "medium",
            "roof_insulation": "medium",
            "floor_insulation": "thin",
            "glazing": "double_old",
            "airtightness": "average",
            "heating_system": "gas_boiler_condensing",
            "ventilation": "natural",
            "heating_setpoint_day_c": 20,
            "heating_setpoint_night_c": 16,
        },
        ("building_type", "storeys", "construction_period", "heated_floor_area_m2", "heating_system"),
    ),
    Archetype(
        "terraced_1995",
        "a 1990s terraced house on three floors with mechanical extract ventilation",
        {
            "building_type": "terraced",
            "construction_period": 1995,
            "heated_floor_area_m2": 130,
            "storeys": 3,
            "wall_insulation": "medium",
            "roof_insulation": "good",
            "floor_insulation": "medium",
            "glazing": "double_low_e",
            "airtightness": "tight",
            "heating_system": "gas_boiler_condensing",
            "ventilation": "mechanical_extract",
            "occupants": 3,
        },
        ("building_type", "construction_period", "heated_floor_area_m2", "storeys", "ventilation", "glazing"),
    ),
    Archetype(
        "semi_2008",
        "a 2000s semi-detached house with a night zone",
        {
            "building_type": "semi_detached",
            "construction_period": 2008,
            "heated_floor_area_m2": 170,
            "storeys": 2,
            "wall_insulation": "good",
            "roof_insulation": "good",
            "floor_insulation": "good",
            "glazing": "double_low_e",
            "airtightness": "tight",
            "heating_system": "gas_boiler_condensing",
            "ventilation": "mechanical_extract",
            "zoning": "day_night",
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "zoning",
            "heating_system",
        ),
    ),
    Archetype(
        "detached_2016_heat_pump",
        "a recent detached house with a heat pump, system D and the big windows south",
        {
            "building_type": "detached",
            "construction_period": 2016,
            "heated_floor_area_m2": 210,
            "storeys": 2,
            "wall_insulation": "good",
            "roof_insulation": "good",
            "floor_insulation": "good",
            "glazing": "triple",
            "airtightness": "tight",
            "heating_system": "heat_pump_air_water",
            "ventilation": "balanced_heat_recovery",
            "zoning": "day_night",
            "main_window_orientation": "south",
            "occupants": 5,
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "heating_system",
            "ventilation",
            "main_window_orientation",
            "glazing",
        ),
    ),
    Archetype(
        "new_terraced_2023",
        "a new terraced house on three floors",
        {
            "building_type": "terraced",
            "construction_period": 2023,
            "heated_floor_area_m2": 140,
            "storeys": 3,
            "wall_insulation": "good",
            "roof_insulation": "good",
            "floor_insulation": "good",
            "glazing": "triple",
            "airtightness": "tight",
            "heating_system": "heat_pump_air_water",
            "ventilation": "balanced_heat_recovery",
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "glazing",
            "airtightness",
        ),
    ),
    Archetype(
        "villa_1990",
        "a large 1990 house with high set points and four occupants",
        {
            "building_type": "detached",
            "construction_period": 1990,
            "heated_floor_area_m2": 320,
            "storeys": 2,
            "wall_insulation": "thin",
            "roof_insulation": "medium",
            "floor_insulation": "none",
            "glazing": "double_old",
            "airtightness": "leaky",
            "heating_system": "gas_boiler_old",
            "ventilation": "natural",
            "occupants": 4,
            "heating_setpoint_day_c": 22,
            "heating_setpoint_night_c": 17,
            "street_facade_orientation": "north",
        },
        (
            "building_type",
            "construction_period",
            "heated_floor_area_m2",
            "storeys",
            "occupants",
            "heating_setpoint_day_c",
            "heating_setpoint_night_c",
        ),
    ),
    Archetype(
        "apartment_2000",
        "an apartment: not supported, the reply must say so",
        {"building_type": "apartment", "construction_period": 2000, "heated_floor_area_m2": 90},
        ("building_type", "construction_period", "heated_floor_area_m2"),
        outcome="unsupported",
    ),
)

# ---------- phrases per language: the words the parsers understand (tests/test_generate.py checks them)

BUILDING: dict[Language, dict[str, str]] = {
    "English": {
        "detached": "a detached house",
        "semi_detached": "a semi-detached house",
        "terraced": "a terraced house",
        "apartment": "an apartment",
    },
    "Dutch": {
        "detached": "een vrijstaande woning",
        "semi_detached": "een halfopen bebouwing",
        "terraced": "een rijwoning",
        "apartment": "een appartement",
    },
    "French": {
        "detached": "une maison quatre façades",
        "semi_detached": "une maison mitoyenne d'un côté",
        "terraced": "une maison mitoyenne",
        "apartment": "un appartement",
    },
}
STOREYS: dict[Language, dict[int, str]] = {
    "English": {1: "a bungalow on one level", 2: "two floors", 3: "three floors"},
    "Dutch": {1: "gelijkvloers", 2: "twee verdiepingen", 3: "drie verdiepingen"},
    "French": {1: "de plain-pied", 2: "deux niveaux", 3: "trois niveaux"},
}
INSULATION_CM: dict[str, dict[str, int]] = {  # the library's thicknesses per level (constructions.py)
    "wall_insulation": {"thin": 4, "medium": 8, "good": 12},
    "roof_insulation": {"thin": 6, "medium": 12, "good": 22},
    "floor_insulation": {"thin": 3, "medium": 5, "good": 8},
}
ELEMENT: dict[Language, dict[str, str]] = {
    "English": {
        "wall_insulation": "the walls",
        "roof_insulation": "the roof",
        "floor_insulation": "the floor",
    },
    "Dutch": {"wall_insulation": "de muren", "roof_insulation": "het dak", "floor_insulation": "de vloer"},
    "French": {"wall_insulation": "les murs", "roof_insulation": "le toit", "floor_insulation": "le sol"},
}
NONE_INSULATED: dict[Language, str] = {
    "English": "{element} not insulated",
    "Dutch": "{element} niet geïsoleerd",
    "French": "aucune isolation pour {element}",
}
WITH_CM: dict[Language, str] = {
    "English": "{cm} cm of insulation in {element}",
    "Dutch": "{cm} cm isolatie in {element}",
    "French": "{cm} cm d'isolant dans {element}",
}
GLAZING: dict[Language, dict[str, str]] = {
    "English": {
        "single": "single glazing",
        "double_old": "old double glazing",
        "double_low_e": "HR++ glazing",
        "triple": "triple glazing",
    },
    "Dutch": {
        "single": "enkel glas",
        "double_old": "dubbel glas",
        "double_low_e": "HR++ glas",
        "triple": "driedubbel glas",
    },
    "French": {
        "single": "simple vitrage",
        "double_old": "double vitrage ancien",
        "double_low_e": "double vitrage HR++",
        "triple": "triple vitrage",
    },
}
AIRTIGHTNESS: dict[Language, dict[str, str]] = {
    "English": {
        "leaky": "it is draughty",
        "average": "the airtightness is average",
        "tight": "it is airtight",
    },
    "Dutch": {
        "leaky": "het is tochtig",
        "average": "de luchtdichtheid is gemiddeld",
        "tight": "het is luchtdicht",
    },
    "French": {
        "leaky": "il y a des courants d'air",
        "average": "l'étanchéité est de niveau moyen",
        "tight": "la maison est étanche",
    },
}
HEATING: dict[Language, dict[str, str]] = {
    "English": {
        "gas_boiler_old": "an old gas boiler with radiators",
        "gas_boiler_condensing": "a condensing gas boiler with radiators",
        "heat_pump_air_water": "an air-water heat pump on radiators",
    },
    "Dutch": {
        "gas_boiler_old": "een oude gasketel met radiatoren",
        "gas_boiler_condensing": "een condensatieketel met radiatoren",
        "heat_pump_air_water": "een warmtepomp op radiatoren",
    },
    "French": {
        "gas_boiler_old": "une vieille chaudière au gaz avec des radiateurs",
        "gas_boiler_condensing": "une chaudière à condensation avec des radiateurs",
        "heat_pump_air_water": "une pompe à chaleur sur radiateurs",
    },
}
VENTILATION: dict[Language, dict[str, str]] = {
    "English": {
        "natural": "we only open the windows",
        "mechanical_extract": "a mechanical extract system (system C)",
        "balanced_heat_recovery": "balanced ventilation with heat recovery (system D)",
    },
    "Dutch": {
        "natural": "we zetten alleen de ramen open",
        "mechanical_extract": "mechanische afzuiging (systeem C)",
        "balanced_heat_recovery": "systeem D met warmteterugwinning",
    },
    "French": {
        "natural": "on ouvre les fenêtres",
        "mechanical_extract": "une VMC simple flux",
        "balanced_heat_recovery": "une VMC double flux",
    },
}
ZONING: dict[Language, str] = {
    "English": "the bedrooms upstairs are a separate night zone",
    "Dutch": "de slaapkamers boven zijn een aparte nachtzone",
    "French": "les chambres forment une zone nuit à l'étage",
}
ORIENTATION: dict[Language, dict[str, str]] = {
    "English": {"north": "north", "east": "east", "south": "south", "west": "west"},
    "Dutch": {"north": "het noorden", "east": "het oosten", "south": "het zuiden", "west": "het westen"},
    "French": {"north": "le nord", "east": "l'est", "south": "le sud", "west": "l'ouest"},
}
MAIN_WINDOW: dict[Language, str] = {
    "English": "the big windows face {orientation}",
    "Dutch": "de grote ramen liggen op {orientation}",
    "French": "les grandes fenêtres donnent vers {orientation}",
}
STREET: dict[Language, str] = {
    "English": "the street side faces {orientation}",
    "Dutch": "de straatkant ligt op {orientation}",
    "French": "la façade côté rue donne vers {orientation}",
}
NUMBER_WORD: dict[Language, dict[int, str]] = {
    "English": {2: "two", 3: "three", 4: "four", 5: "five"},
    "Dutch": {2: "twee", 3: "drie", 4: "vier", 5: "vijf"},
    "French": {2: "deux", 3: "trois", 4: "quatre", 5: "cinq"},
}
OCCUPANTS: dict[Language, str] = {
    "English": "{n} of us live here",
    "Dutch": "we wonen hier met {n}",
    "French": "nous sommes {n} à vivre ici",
}
SETPOINT_DAY: dict[Language, str] = {
    "English": "{t} degrees during the day",
    "Dutch": "{t} graden overdag",
    "French": "{t} degrés en journée",
}
SETPOINT_NIGHT: dict[Language, str] = {
    "English": "{t} at night",
    "Dutch": "{t} 's nachts",
    "French": "{t} la nuit",
}
BUILT: dict[Language, str] = {
    "English": "built in {year}",
    "Dutch": "gebouwd in {year}",
    "French": "construite en {year}",
}
AREA: dict[Language, str] = {"English": "{area} m2", "Dutch": "{area} m2", "French": "{area} m2"}
MUNICIPALITY: dict[Language, str] = {"English": "in {name}", "Dutch": "in {name}", "French": "à {name}"}
OPENING_START: dict[Language, str] = {
    "English": "We live in {building}, {built}, {area} on {storeys}",
    "Dutch": "Wij wonen in {building}, {built}, met {area} op {storeys}",
    "French": "Nous habitons {building}, {built}, {area} sur {storeys}",
}
APARTMENT_START: dict[Language, str] = {
    "English": "We live in {building} of {area}, {built}",
    "Dutch": "Wij wonen in {building} van {area}, {built}",
    "French": "Nous habitons {building} de {area}, {built}",
}
PLAN: dict[Language, str] = {
    "English": "Next year we plan {plans}.",
    "Dutch": "Volgend jaar plannen we {plans}.",
    "French": "L'année prochaine nous prévoyons {plans}.",
}
AND: dict[Language, str] = {"English": " and ", "Dutch": " en ", "French": " et "}
DONT_KNOW: dict[Language, str] = {
    "English": "I don't know.",
    "Dutch": "Weet ik niet.",
    "French": "Je ne sais pas.",
}


def phrase(language: Language, name: str, value: Any) -> str:
    """The phrase for one field value, as the opening or as an answer."""
    match name:
        case "building_type":
            return BUILDING[language][value]
        case "construction_period":
            return BUILT[language].format(year=value)
        case "heated_floor_area_m2":
            return AREA[language].format(area=value)
        case "storeys":
            return STOREYS[language][value]
        case "wall_insulation" | "roof_insulation" | "floor_insulation":
            element = ELEMENT[language][name]
            if value == "none":
                return NONE_INSULATED[language].format(element=element)
            return WITH_CM[language].format(cm=INSULATION_CM[name][value], element=element)
        case "glazing":
            return GLAZING[language][value]
        case "airtightness":
            return AIRTIGHTNESS[language][value]
        case "heating_system":
            return HEATING[language][value]
        case "ventilation":
            return VENTILATION[language][value]
        case "zoning":
            return ZONING[language]
        case "main_window_orientation":
            return MAIN_WINDOW[language].format(orientation=ORIENTATION[language][value])
        case "street_facade_orientation":
            return STREET[language].format(orientation=ORIENTATION[language][value])
        case "occupants":
            return OCCUPANTS[language].format(n=NUMBER_WORD[language][value])
        case "heating_setpoint_day_c":
            return SETPOINT_DAY[language].format(t=value)
        case "heating_setpoint_night_c":
            return SETPOINT_NIGHT[language].format(t=value)
        case "municipality":
            return str(value)  # the opening adds "in"; an answer is the name alone
    raise KeyError(name)


def opening_text(archetype: Archetype, language: Language) -> str:
    values = archetype.values
    core = {"building_type", "construction_period", "heated_floor_area_m2", "storeys"}
    if archetype.outcome == "unsupported":
        start = APARTMENT_START[language].format(
            building=phrase(language, "building_type", values["building_type"]),
            area=phrase(language, "heated_floor_area_m2", values["heated_floor_area_m2"]),
            built=phrase(language, "construction_period", values["construction_period"]),
        )
    else:
        start = OPENING_START[language].format(
            building=phrase(language, "building_type", values["building_type"]),
            built=phrase(language, "construction_period", values["construction_period"]),
            area=phrase(language, "heated_floor_area_m2", values["heated_floor_area_m2"]),
            storeys=phrase(language, "storeys", values["storeys"]),
        )
    rest = [
        MUNICIPALITY[language].format(name=values[name])
        if name == "municipality"
        else phrase(language, name, values[name])
        for name in archetype.opening
        if name not in core
    ]
    sentences = [start + (", " + ", ".join(rest) if rest else "") + "."]
    if archetype.renovation:
        plans = AND[language].join(
            phrase(language, name, value) for name, value in archetype.renovation.items()
        )
        sentences.append(PLAN[language].format(plans=plans))
    return " ".join(sentences)


def answers(archetype: Archetype, language: Language) -> dict[str, str]:
    """A short answer per field the opening does not state (the runner asks by field)."""
    return {
        name: phrase(language, name, value)[0].upper() + phrase(language, name, value)[1:] + "."
        for name, value in archetype.values.items()
        if name not in archetype.opening
    }


def expected_values(archetype: Archetype) -> dict[str, Any]:
    from llmtrano.brief.parse import period_of_year

    expected = dict(archetype.values)
    expected["construction_period"] = period_of_year(archetype.values["construction_period"]).value
    return expected


def case_id(archetype: Archetype, language: Language) -> str:
    return f"gen_{archetype.key}_{language.lower()}"


def generate_case(archetype: Archetype, language: Language) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": case_id(archetype, language),
        "generated": True,
        "note": archetype.note,
        "language": language,
        "opening": opening_text(archetype, language),
        "answers": answers(archetype, language),
        "default_answer": DONT_KNOW[language],
        "expected": expected_values(archetype),
        "outcome": archetype.outcome,
    }
    if archetype.renovation:
        case["renovation"] = dict(archetype.renovation)
    return case


def generate_all() -> list[dict[str, Any]]:
    return [generate_case(archetype, language) for archetype in ARCHETYPES for language in LANGUAGES]


def write_cases(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for case in generate_all():
        path = directory / f"{case['id']}.yaml"
        header = (
            "# Generated by `python -m llmtrano.evals.generate`; edit the archetype there, not this file.\n"
        )
        path.write_text(header + yaml.safe_dump(case, sort_keys=False, allow_unicode=True, width=100))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the generated eval cases.")
    parser.add_argument("--output", type=Path, default=Path("evals/cases"))
    args = parser.parse_args(argv)
    for path in write_cases(args.output):
        sys.stdout.write(f"written {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
