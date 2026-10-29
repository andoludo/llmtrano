"""The renovation overlay: the renovation facts laid over the confirmed baseline Brief (phase 2).

Facts tagged ``renovation`` during the intake never touch the baseline (``merge``). Once the baseline is
confirmed they become a second Brief: the baseline with those fields replaced, the last fact per field
winning. Its provenance says ``Source.renovation`` with the user's words, so the card can show a renovation
column and the explanation can say what changes. A fact whose value does not fit the Brief (bounds, the day
schedule) is skipped and logged; the planner never asked it back, so it cannot block the run.
"""

import logging
from collections.abc import Sequence

from pydantic import ValidationError

from llmtrano.brief.defaults import UNKNOWN
from llmtrano.brief.model import BRIEF_FIELDS, Fact, FieldProvenance, HouseBrief, Provenance, Source
from llmtrano.brief.plan import label

log = logging.getLogger(__name__)


def renovation_facts(facts: Sequence[Fact]) -> dict[str, Fact]:
    """The renovation fact that applies per field: the last one stated (turn order, then list order)."""
    chosen: dict[str, Fact] = {}
    for fact in sorted((f for f in facts if f.scenario == "renovation"), key=lambda f: f.turn):
        if fact.value in UNKNOWN:
            continue
        chosen[fact.field] = fact
    return chosen


def renovation_brief(
    brief: HouseBrief, provenance: Provenance, facts: Sequence[Fact]
) -> tuple[HouseBrief, Provenance] | None:
    """The renovation Brief and its provenance, or None when no renovation fact applies.

    ``brief`` is the baseline as the user stated it (defaults are applied by the compiler, as for the
    baseline, so a renovation of a defaulted field replaces the default).
    """
    values = brief.model_dump()
    overlay = dict(provenance)
    applied = 0
    for name, fact in renovation_facts(facts).items():
        candidate = {**values, name: fact.value}
        try:
            HouseBrief.model_validate(candidate)
        except ValidationError as error:
            log.warning("renovation fact skipped field=%s value=%r error=%s", name, fact.value, error)
            continue
        values = candidate
        overlay[name] = FieldProvenance(
            source=Source.renovation, evidence=fact.evidence, confidence=fact.confidence, turn=fact.turn
        )
        applied += 1
    if not applied:
        return None
    return HouseBrief.model_validate(values), overlay


def renovation_changes(baseline: HouseBrief, renovation: HouseBrief) -> list[tuple[str, str | None, str]]:
    """``(field, before, after)`` as human labels, for the fields the renovation changes."""
    changes = []
    for name in BRIEF_FIELDS:
        before, after = getattr(baseline, name), getattr(renovation, name)
        if after is not None and after != before:
            changes.append((name, label(before) if before is not None else None, label(after)))
    return changes
