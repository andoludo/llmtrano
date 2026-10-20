"""Inputs of the ``respond`` and ``interpret`` calls: what the program decided, written for the LLM.

The reply wording is the LLM's; its content is not. ``respond`` gets the facts noted this turn, the renovation
facts, the planner's questions and the phase; ``interpret`` gets the KPI JSON, the Brief with its sources and
the assumptions. The answer buttons come from the planner's options, never from the LLM text.
"""

import json
import re
from collections import Counter
from collections.abc import Sequence
from typing import Literal

from llmtrano.brief.model import BRIEF_FIELDS, Fact, HouseBrief, Provenance, Source
from llmtrano.brief.plan import Question, label

Language = Literal["English", "Dutch", "French"]
# fmt: off
STOPWORDS: dict[Language, frozenset[str]] = {
    "English": frozenset({"the", "and", "is", "a", "an", "we", "our", "house", "with", "not", "it", "of", "i",
                          "my", "in", "has", "have", "built", "from", "about"}),
    "Dutch": frozenset({"de", "het", "een", "en", "van", "is", "met", "niet", "wij", "we", "ons", "huis",
                        "geen", "ik", "dat", "zijn", "uit", "heeft", "gebouwd", "ja"}),
    "French": frozenset({"le", "la", "les", "et", "est", "une", "un", "nous", "maison", "pas", "avec", "je",
                         "des", "du", "notre", "en", "a", "construite", "oui"}),
}
# fmt: on


def detect_language(text: str, current: Language = "English") -> Language:
    """The language with the most stop words; ``current`` when there is no clear winner (short answers)."""
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    counts = Counter({language: sum(word in stop for word in words) for language, stop in STOPWORDS.items()})
    (best, top), (_, second) = counts.most_common(2)
    return best if top >= 2 and top > second else current


def respond_input(
    *,
    noted: Sequence[Fact],
    renovation: Sequence[Fact],
    questions: Sequence[Question],
    ready_to_confirm: bool,
    language: Language,
    gave_up: bool = False,
) -> str:
    lines = [f"Language: {language}"]
    if noted:
        lines.append("Noted this turn: " + "; ".join(f"{_name(f.field)} = {label(f.value)}" for f in noted))
    if renovation:
        lines.append(
            "Renovation plans noted: " + "; ".join(f"{_name(f.field)} = {label(f.value)}" for f in renovation)
        )
    if gave_up:
        lines.append("The last message could not be read; ask the questions again simply.")
    unsupported = [q for q in questions if q.kind == "unsupported"]
    if unsupported:
        lines.append(f"Not supported: {unsupported[0].prompt_seed}")
    if ready_to_confirm:
        lines.append("Ready to confirm: yes")
    asked = [q for q in questions if q.kind != "unsupported"]
    if asked:
        lines.append(
            "Questions to ask:\n" + "\n".join(f"{i}. {q.prompt_seed}" for i, q in enumerate(asked, 1))
        )
    elif not ready_to_confirm and not unsupported:
        lines.append("Questions to ask: none; invite them to add anything else they know about the house.")
    return "\n".join(lines)


FIELD_NAMES: dict[str, str] = {
    "building_type": "building type",
    "construction_period": "construction period",
    "heated_floor_area_m2": "heated floor area (m2)",
    "storeys": "storeys",
    "room_height_m": "room height (m)",
    "street_facade_orientation": "street facade orientation",
    "wall_insulation": "wall insulation",
    "roof_insulation": "roof insulation",
    "floor_insulation": "floor insulation",
    "glazing": "glazing",
    "window_to_wall_ratio": "window-to-wall ratio",
    "airtightness": "airtightness",
    "heating_setpoint_day_c": "day temperature (degC)",
    "heating_setpoint_night_c": "night temperature (degC)",
    "day_start_h": "day starts at (hour)",
    "day_end_h": "day ends at (hour)",
    "occupants": "occupants",
    "municipality": "municipality",
}


def _name(field: str) -> str:
    return FIELD_NAMES[field]


def brief_lines(brief: HouseBrief, provenance: Provenance) -> list[str]:
    """``field: value (you said "...")`` or ``(assumed: basis)`` for every filled field."""
    lines = []
    for name in BRIEF_FIELDS:
        value = getattr(brief, name)
        if value is None:
            continue
        said = provenance.get(name)
        if said is not None and said.source is Source.user:
            source = f'you said "{said.evidence}"' if said.evidence else "you said"
        else:
            source = f"assumed: {said.basis}" if said is not None and said.basis else "assumed"
        lines.append(f"- {_name(name)}: {label(value)} ({source})")
    return lines


def interpret_input(
    *,
    kpis: dict[str, object],
    brief: HouseBrief,
    provenance: Provenance,
    assumptions: Sequence[str],
    language: Language,
) -> str:
    results = {
        key: kpis[key] for key in ("heating_energy_kwh", "heating_energy_kwh_per_m2", "peak_heating_w")
    }
    results |= {"heated_floor_area_m2": kpis.get("heated_floor_area_m2"), "period": "one year"}
    rounded = {key: round(value, 1) if isinstance(value, float) else value for key, value in results.items()}
    return "\n".join(
        [
            f"Language: {language}",
            "Results (from the simulation, rounded):",
            json.dumps(rounded, ensure_ascii=False),
            "House:",
            *brief_lines(brief, provenance),
            "Assumptions:",
            *(f"- {line}" for line in assumptions),
        ]
    )
