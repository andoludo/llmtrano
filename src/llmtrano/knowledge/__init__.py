"""Grounded references (#46 grounding, #47 retrieval): published results the explanation may compare with.

``references.yaml`` is a small, hand-curated table with a source for every number (trano's validation page,
llmtrano's own measurements). ``retrieve`` picks the entries that apply to a house by building type,
construction period and whether it is the renovated scenario; the interpretation gets them as "References"
and their numbers join the guardrail's known values. Retrieval is a lookup, not a vector search: the table
is small and the match must be explainable. Ando adds TABULA or EPC references in the same file.
"""

from functools import cache
from importlib.resources import files
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from llmtrano.brief.model import BuildingType, HouseBrief, Period

MAX_REFERENCES = 4


class AppliesTo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    building_types: list[BuildingType] = Field(default_factory=list)  # empty: any
    periods: list[Period] = Field(default_factory=list)  # empty: any
    renovated: bool | None = None  # None: either scenario

    def matches(self, brief: HouseBrief, renovated: bool) -> bool:
        if self.building_types and brief.building_type not in self.building_types:
            return False
        if self.periods and brief.construction_period not in self.periods:
            return False
        return self.renovated is None or self.renovated == renovated


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    metric: str
    value: float
    unit: str
    applies_to: AppliesTo
    conditions: str
    source: str = Field(min_length=10)

    def line(self) -> str:
        return f"{self.title}: {self.value:g} {self.unit} [{self.conditions}] (source: {self.source})"


@cache
def load_references() -> tuple[Reference, ...]:
    data: list[dict[str, Any]] = yaml.safe_load(files(__package__).joinpath("references.yaml").read_text())
    references = tuple(Reference.model_validate(item) for item in data)
    ids = [reference.id for reference in references]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate reference ids")
    return references


def retrieve(brief: HouseBrief, *, renovated: bool = False, limit: int = MAX_REFERENCES) -> list[Reference]:
    """The references that apply to ``brief`` (the heating need first), at most ``limit``."""
    matching = [r for r in load_references() if r.applies_to.matches(brief, renovated)]
    order = {"heating_energy_kwh_per_m2": 0, "design_heat_loss_w_per_m2": 1, "final_energy_kwh_per_m2": 2}
    matching.sort(key=lambda r: (order.get(r.metric, 9), r.id))
    return matching[:limit]
