"""The planner: which questions to ask next and when the Brief is ready to confirm (pure Python, #39, #42).

Python, not the LLM, decides the next question. The order is the expected impact on the heating demand:
conflicts first, then the required fields, then the envelope, then the set points. The LLM only words the
questions; the answer buttons in the ui come from ``Question.options``.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from llmtrano.brief.defaults import is_given
from llmtrano.brief.merge import Conflict
from llmtrano.brief.model import (
    REQUIRED_FIELDS,
    Airtightness,
    BriefField,
    BuildingType,
    Glazing,
    HeatingSystem,
    HouseBrief,
    InsulationLevel,
    Period,
    Provenance,
    Source,
    Ventilation,
    Zoning,
)

MAX_QUESTIONS = 2
MAX_ASKS = 2  # a field the user left unanswered twice is not asked again (its default applies)
PRIORITY: tuple[BriefField, ...] = (
    "building_type",
    "heated_floor_area_m2",
    "storeys",
    "construction_period",
    "wall_insulation",
    "glazing",
    "roof_insulation",
    "airtightness",
    "heating_system",
    "ventilation",
    "floor_insulation",
    "heating_setpoint_day_c",
    "heating_setpoint_night_c",
)
ENVELOPE_FIELDS: tuple[BriefField, ...] = ("wall_insulation", "roof_insulation", "glazing")
DONT_KNOW = "I don't know"

LABELS: dict[str, str] = {
    BuildingType.detached: "Detached",
    BuildingType.semi_detached: "Semi-detached",
    BuildingType.terraced: "Terraced",
    BuildingType.apartment: "Apartment",
    Period.before_1946: "Before 1946",
    Period.y1946_1970: "1946 to 1970",
    Period.y1971_1990: "1971 to 1990",
    Period.y1991_2005: "1991 to 2005",
    Period.y2006_2020: "2006 to 2020",
    Period.after_2020: "After 2020",
    InsulationLevel.none: "None",
    InsulationLevel.thin: "A little (a few cm)",
    InsulationLevel.medium: "Medium (about 8 to 12 cm)",
    InsulationLevel.good: "Good (renovated or recent)",
    Glazing.single: "Single glazing",
    Glazing.double_old: "Old double glazing",
    Glazing.double_low_e: "Double low-e (HR) glazing",
    Glazing.triple: "Triple glazing",
    Airtightness.leaky: "Draughty",
    Airtightness.average: "Average",
    Airtightness.tight: "Airtight",
    Ventilation.natural: "Natural (windows, grilles)",
    Ventilation.mechanical_extract: "Mechanical extract (system C)",
    Ventilation.balanced_heat_recovery: "Balanced with heat recovery (system D)",
    HeatingSystem.gas_boiler_old: "Old gas boiler",
    HeatingSystem.gas_boiler_condensing: "Condensing gas boiler",
    HeatingSystem.heat_pump_air_water: "Air-water heat pump",
    HeatingSystem.unknown: "Not known (heating need only)",
    Zoning.one: "One zone",
    Zoning.day_night: "Day and night zones",
}
SEEDS: dict[str, str] = {
    "building_type": "What kind of house is it: detached, semi-detached (one shared wall) or terraced?",
    "heated_floor_area_m2": "What is the heated floor area of all floors together, in square metres?",
    "storeys": "How many heated floors does the house have?",
    "construction_period": "When was the house built (roughly)?",
    "wall_insulation": "Are the outside walls insulated, and how much?",
    "glazing": "What kind of glazing do the windows have?",
    "roof_insulation": "Is the roof, or the attic floor, insulated?",
    "airtightness": "Is the house draughty, average or airtight?",
    "heating_system": "How is the house heated: an old gas boiler, a condensing gas boiler or a heat pump?",
    "ventilation": "How is the house ventilated: by opening windows, a mechanical extract system (C) or "
    "balanced ventilation with heat recovery (D)?",
    "floor_insulation": "Is the ground floor insulated?",
    "heating_setpoint_day_c": "What temperature do you keep the house at during the day?",
    "heating_setpoint_night_c": "And at night?",
}


class Option(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str  # the Brief value the button sets; "unknown" for "I don't know"
    label: str


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: BriefField
    kind: Literal["conflict", "confirm", "missing", "unsupported"]
    prompt_seed: str  # plain-language seed the LLM words
    options: list[Option]


def label(value: object) -> str:
    """The human label of a Brief value: enum labels, numbers without a trailing ".0"."""
    if isinstance(value, StrEnum):
        return LABELS.get(value, value.value)
    if isinstance(value, float):
        return f"{value:g}"
    return LABELS.get(value, str(value)) if isinstance(value, str) else str(value)


def options_for(field: BriefField) -> list[Option]:
    """Enum values with human labels plus "I don't know" where a default exists; numbers get a few presets."""
    enums: dict[str, type[StrEnum]] = {
        "building_type": BuildingType,
        "construction_period": Period,
        "wall_insulation": InsulationLevel,
        "roof_insulation": InsulationLevel,
        "floor_insulation": InsulationLevel,
        "glazing": Glazing,
        "airtightness": Airtightness,
        "ventilation": Ventilation,
        "heating_system": HeatingSystem,
        "zoning": Zoning,
    }
    if field in enums:
        members = [m for m in enums[field] if m.value != "unknown"]
        if field == "building_type":  # apartments are not supported in phase 1: no button for them
            members = [m for m in members if m is not BuildingType.apartment]
        options = [Option(value=m.value, label=LABELS[m]) for m in members]
    elif field == "storeys":
        options = [Option(value=str(n), label=str(n)) for n in (1, 2, 3)]
    elif field in ("heating_setpoint_day_c", "heating_setpoint_night_c"):
        presets = (19, 20, 21, 22) if field == "heating_setpoint_day_c" else (15, 16, 17, 18)
        options = [Option(value=str(t), label=f"{t} degC") for t in presets]
    else:
        options = []
    if field not in REQUIRED_FIELDS:
        options.append(Option(value="unknown", label=DONT_KNOW))
    return options


def _needs_answer(
    name: BriefField, brief: HouseBrief, provenance: Provenance
) -> Literal["missing", "confirm"] | None:
    said = provenance.get(name)
    value = getattr(brief, name)
    if value is None or said is None or said.source is not Source.user:
        return "missing"
    if said.confidence == "low" and is_given(value):
        return "confirm"
    return None


def next_questions(
    brief: HouseBrief,
    provenance: Provenance,
    conflicts: list[Conflict],
    asked: Mapping[str, int] | None = None,
) -> list[Question]:
    """At most two questions, highest impact first. ``asked`` counts earlier asks per field (loop guard)."""
    asked = asked or {}
    questions: list[Question] = []
    for conflict in conflicts:
        seed = (
            f"Earlier you said {label(conflict.current)!r}"
            f"{f' ({conflict.current_evidence!r})' if conflict.current_evidence else ''}, now "
            f"{label(conflict.proposed)!r} ({conflict.proposed_evidence!r}). Which is right?"
        )
        options = [
            Option(value=str(_plain(conflict.current)), label=label(conflict.current)),
            Option(value=str(_plain(conflict.proposed)), label=label(conflict.proposed)),
        ]
        questions.append(Question(field=conflict.field, kind="conflict", prompt_seed=seed, options=options))
    if brief.building_type is BuildingType.apartment:
        questions.append(
            Question(
                field="building_type",
                kind="unsupported",
                prompt_seed="Apartments are not supported yet. Is there a house you want to describe?",
                options=options_for("building_type"),
            )
        )
    conflicted = {conflict.field for conflict in conflicts}
    for name in PRIORITY:
        if len(questions) >= MAX_QUESTIONS:
            break
        if name in conflicted or (name == "building_type" and brief.building_type is BuildingType.apartment):
            continue
        need = _needs_answer(name, brief, provenance)
        if need is None or (asked.get(name, 0) >= MAX_ASKS and name not in REQUIRED_FIELDS):
            continue
        if need == "confirm":
            said = provenance[name]
            seed = f"You said {said.evidence!r}: is {label(getattr(brief, name))!r} right?"
        else:
            seed = SEEDS[name]
        questions.append(Question(field=name, kind=need, prompt_seed=seed, options=options_for(name)))
    return questions[:MAX_QUESTIONS]


def ready_to_confirm(
    brief: HouseBrief, provenance: Provenance, conflicts: list[Conflict], *, user_says_complete: bool
) -> bool:
    """All required fields stated, no conflict, a supported building, and either the user said the description
    is complete or the walls, roof and glazing are user-given ("I don't know" counts as an answer)."""
    if conflicts or brief.building_type is BuildingType.apartment:
        return False
    required = all(
        getattr(brief, name) is not None
        and (said := provenance.get(name)) is not None
        and said.source is Source.user
        for name in REQUIRED_FIELDS
    )
    envelope = all(
        (said := provenance.get(name)) is not None and said.source is Source.user for name in ENVELOPE_FIELDS
    )
    return required and (user_says_complete or envelope)


def _plain(value: object) -> object:
    return value.value if isinstance(value, StrEnum) else value
