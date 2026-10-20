"""Merge the facts of a turn into the Brief (pure Python, #31 slot filling).

Rules:
- a user value replaces a default;
- a new user value that differs from an earlier user value is not applied but becomes a ``Conflict``
  (the planner asks); the next user value for that field resolves it;
- low-confidence and ``unclear`` facts are applied and asked back for confirmation (the planner reads the
  provenance's confidence);
- ``unknown`` never replaces a stated value;
- renovation facts are kept apart, never applied to the baseline;
- an edit on the Brief card (``overrides``) is the user correcting a value: it replaces it, no conflict.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationError

from llmtrano.brief.defaults import UNKNOWN
from llmtrano.brief.model import BriefField, BriefValue, Fact, FieldProvenance, HouseBrief, Provenance, Source


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: BriefField
    current: BriefValue
    proposed: BriefValue
    current_evidence: str | None
    proposed_evidence: str
    turn: int
    reason: str = "differs from an earlier answer"


@dataclass(slots=True)
class MergeResult:
    brief: HouseBrief
    provenance: Provenance
    conflicts: list[Conflict]
    applied: list[Fact] = field(default_factory=list)
    renovation: list[Fact] = field(default_factory=list)
    ignored: list[Fact] = field(default_factory=list)  # repeats, or "unknown" after a stated value


def _same(a: object, b: object) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    return _plain(a) == _plain(b)


def _plain(value: object) -> object:
    return value.value if isinstance(value, StrEnum) else value


def _brief_value(value: object) -> BriefValue:
    plain = _plain(value)
    if not isinstance(plain, str | int | float):
        raise TypeError(f"not a Brief value: {value!r}")
    return plain


def merge_facts(
    brief: HouseBrief,
    provenance: Provenance,
    conflicts: list[Conflict],
    facts: list[Fact],
    overrides: Collection[str] = (),
) -> MergeResult:
    """The Brief with this turn's facts applied. Inputs are not changed."""
    values = brief.model_dump()
    provenance = dict(provenance)
    open_conflicts = {conflict.field: conflict for conflict in conflicts}
    result = MergeResult(brief=brief, provenance=provenance, conflicts=[])
    for fact in facts:
        if fact.scenario == "renovation":
            result.renovation.append(fact)
            continue
        name, current, said = fact.field, values[fact.field], provenance.get(fact.field)
        # a value the user stated: not a default, not "I don't know"
        stated = (
            said is not None and said.source is Source.user and current is not None and current not in UNKNOWN
        )
        resolving = name in open_conflicts or name in overrides
        if stated and fact.value in UNKNOWN:
            result.ignored.append(fact)
            continue
        if stated and not resolving and not _same(current, fact.value):
            open_conflicts[name] = Conflict(
                field=name,
                current=_brief_value(current),
                proposed=fact.value,
                current_evidence=said.evidence if said else None,
                proposed_evidence=fact.evidence,
                turn=fact.turn,
            )
            continue
        if stated and _same(current, fact.value) and not _more_certain(fact, said):
            result.ignored.append(fact)
            continue
        candidate = {**values, name: fact.value}
        try:
            HouseBrief.model_validate(candidate)
        except ValidationError as error:  # e.g. a day that would end before it starts
            open_conflicts[name] = Conflict(
                field=name,
                current=_brief_value(current) if current is not None else "",
                proposed=fact.value,
                current_evidence=said.evidence if said else None,
                proposed_evidence=fact.evidence,
                turn=fact.turn,
                reason=error.errors()[0]["msg"],
            )
            continue
        values = candidate
        open_conflicts.pop(name, None)
        provenance[name] = FieldProvenance(
            source=Source.user,
            evidence=fact.evidence,
            confidence="low" if fact.scenario == "unclear" else fact.confidence,
            turn=fact.turn,
        )
        result.applied.append(fact)
    result.brief = HouseBrief.model_validate(values)
    result.provenance = provenance
    result.conflicts = list(open_conflicts.values())
    return result


CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2, None: -1}


def _more_certain(fact: Fact, said: FieldProvenance | None) -> bool:
    """A repeat that raises the confidence (the user confirming a low-confidence value)."""
    return said is None or CONFIDENCE_RANK[fact.confidence] > CONFIDENCE_RANK[said.confidence]
