"""The House Brief card: what the ui shows next to the chat and what the confirmation interrupt carries."""

from pydantic import BaseModel

from llmtrano.brief.defaults import apply_defaults, assumptions
from llmtrano.brief.merge import Conflict
from llmtrano.brief.model import BRIEF_FIELDS, HouseBrief, Provenance, Source, missing_required
from llmtrano.brief.plan import label


class CardRow(BaseModel):
    field: str
    value: str | None  # human label; None while unknown
    source: Source | None  # "you said" (user) or "assumed" (default)
    evidence: str | None  # the user's words, shown on hover
    basis: str | None  # where a default comes from
    conflict: bool = False


class BriefCard(BaseModel):
    rows: list[CardRow]
    assumptions: list[str]
    missing_required: list[str]
    conflicts: list[Conflict]
    ready: bool


def build_card(
    brief: HouseBrief, provenance: Provenance, conflicts: list[Conflict], *, ready: bool
) -> BriefCard:
    """The card with defaults shown as assumptions (the state itself keeps only what the user said)."""
    filled, full = apply_defaults(brief, provenance)
    conflicted = {conflict.field for conflict in conflicts}
    rows = []
    for name in BRIEF_FIELDS:
        value = getattr(filled, name)
        said = full.get(name)
        rows.append(
            CardRow(
                field=name,
                value=label(value) if value is not None else None,
                source=said.source if said else None,
                evidence=said.evidence if said else None,
                basis=said.basis if said else None,
                conflict=name in conflicted,
            )
        )
    return BriefCard(
        rows=rows,
        assumptions=assumptions(filled, full),
        missing_required=missing_required(brief),
        conflicts=conflicts,
        ready=ready,
    )
