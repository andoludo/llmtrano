"""The House Brief card: what the ui shows next to the chat and what the confirmation interrupt carries.

Phase 2: a renovation column, filled from the renovation facts, for the fields the renovation changes.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from llmtrano.brief.defaults import apply_defaults, assumptions
from llmtrano.brief.merge import Conflict
from llmtrano.brief.model import BRIEF_FIELDS, Fact, HouseBrief, Provenance, Source, missing_required
from llmtrano.brief.overlay import renovation_brief
from llmtrano.brief.plan import label


class CardRow(BaseModel):
    field: str
    value: str | None  # human label; None while unknown
    source: Source | None  # "you said" (user) or "assumed" (default)
    evidence: str | None  # the user's words, shown on hover
    basis: str | None  # where a default comes from
    conflict: bool = False
    renovation: str | None = None  # the value after the renovation, when the renovation changes it
    renovation_evidence: str | None = None


class BriefCard(BaseModel):
    rows: list[CardRow]
    assumptions: list[str]
    missing_required: list[str]
    conflicts: list[Conflict]
    ready: bool
    renovation_fields: list[str] = []  # the fields the renovation column changes


def build_card(
    brief: HouseBrief,
    provenance: Provenance,
    conflicts: list[Conflict],
    *,
    ready: bool,
    renovation_facts: Sequence[Fact] = (),
) -> BriefCard:
    """The card with defaults shown as assumptions (the state itself keeps only what the user said)."""
    filled, full = apply_defaults(brief, provenance)
    overlay = renovation_brief(brief, provenance, renovation_facts)
    renovated, renovated_provenance = apply_defaults(*overlay) if overlay else (filled, full)
    conflicted = {conflict.field for conflict in conflicts}
    rows = []
    for name in BRIEF_FIELDS:
        value = getattr(filled, name)
        said = full.get(name)
        after = getattr(renovated, name)
        after_said = renovated_provenance.get(name)
        changed = after_said is not None and after_said.source is Source.renovation and after != value
        rows.append(
            CardRow(
                field=name,
                value=label(value) if value is not None else None,
                source=said.source if said else None,
                evidence=said.evidence if said else None,
                basis=said.basis if said else None,
                conflict=name in conflicted,
                renovation=label(after) if changed else None,
                renovation_evidence=after_said.evidence if changed and after_said else None,
            )
        )
    return BriefCard(
        rows=rows,
        assumptions=assumptions(filled, full),
        missing_required=missing_required(brief),
        conflicts=conflicts,
        ready=ready,
        renovation_fields=[row.field for row in rows if row.renovation is not None],
    )
