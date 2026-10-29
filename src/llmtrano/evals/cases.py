"""Eval cases (#55 golden examples): an opening description, a scripted user and the expected Brief.

A case file (``evals/cases/<id>.yaml``)::

    id: precise_terraced
    language: English
    opening: "Terraced house built in 1998, ..."
    follow_ups: ["Sorry, it is 140 m2, not 120."]   # sent after the opening, before any answer (optional)
    answers:                                          # the scripted user, by field asked
      heated_floor_area_m2: "about 130 m2"
    default_answer: "I don't know"
    expected:                                         # Brief values for the fields the user states
      building_type: terraced
      heated_floor_area_m2: 115
    renovation: {glazing: triple}                     # facts the user states as plans (scenario accuracy)
    outcome: simulate                                 # unsupported (apartments) or compare (both scenarios)
    gate: false                                       # a gate case (tutorial house, reference house)
    reference:                                        # published numbers the result is checked against
      source: "trano docs/validation/residential_reference_cases.md"
      heating_kwh_per_m2: 130
      renovation_heating_kwh_per_m2: 17.6
      tolerance: 0.35                                 # relative
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from llmtrano.brief.model import BRIEF_FIELDS

MAX_TURNS = 10


class Reference(BaseModel):
    """Published numbers a gate case is checked against, with their source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    heating_kwh_per_m2: float
    renovation_heating_kwh_per_m2: float | None = None
    tolerance: float = Field(default=0.3, gt=0, le=1)  # relative


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    language: Literal["English", "Dutch", "French"] = "English"
    opening: str
    follow_ups: list[str] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    default_answer: str = "I don't know"
    expected: dict[str, Any] = Field(default_factory=dict)
    renovation: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["simulate", "unsupported", "compare"] = "simulate"
    gate: bool = False
    note: str | None = None
    reference: Reference | None = None
    generated: bool = False  # written by ``llmtrano.evals.generate`` (phase 3)

    @field_validator("answers", "expected", "renovation")
    @classmethod
    def _known_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(BRIEF_FIELDS)
        if unknown:
            raise ValueError(f"unknown Brief fields: {sorted(unknown)}")
        return values

    def answer(self, field: str) -> str:
        return self.answers.get(field, self.default_answer)


def load_cases(directory: Path) -> list[EvalCase]:
    cases = [
        EvalCase.model_validate(yaml.safe_load(path.read_text())) for path in sorted(directory.glob("*.yaml"))
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {directory}")
    return cases
