"""Extraction: the user's message to a list of typed facts about the house.

The response schema is flat on purpose (no ``$ref``, no ``anyOf`` mixed with properties) so llama.cpp can
turn it into a grammar; ``value`` is always a string and ``llmtrano.brief.parse`` types it. The output is
validated with Pydantic; on failure one retry shows the model what was wrong, then the turn's extraction is
given up and the planner asks again.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llmtrano.brief.model import BRIEF_FIELDS, BriefField, Confidence, HouseBrief, Provenance, Source
from llmtrano.brief.parse import ParseError, parse_value
from llmtrano.llm import calls
from llmtrano.llm.calls import CallStats

log = logging.getLogger(__name__)

Scenario = Literal["baseline", "renovation", "unclear"]
SCENARIOS: tuple[Scenario, ...] = ("baseline", "renovation", "unclear")
CONFIDENCES: tuple[Confidence, ...] = ("low", "medium", "high")
BriefValue = str | int | float


def extraction_schema() -> dict[str, Any]:
    """The JSON Schema of ``response_format``: flat, every property required."""
    fact = {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": list(BRIEF_FIELDS)},
            "value": {"type": "string"},
            "scenario": {"type": "string", "enum": list(SCENARIOS)},
            "evidence": {"type": "string"},
            "confidence": {"type": "string", "enum": list(CONFIDENCES)},
        },
        "required": ["field", "value", "scenario", "evidence", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"facts": {"type": "array", "items": fact}, "user_says_complete": {"type": "boolean"}},
        "required": ["facts", "user_says_complete"],
        "additionalProperties": False,
    }


class RawFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: BriefField
    value: str = Field(max_length=200)
    scenario: Scenario
    evidence: str = Field(max_length=300)
    confidence: Confidence


class RawExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[RawFact] = Field(max_length=30)
    user_says_complete: bool


class Fact(BaseModel):
    """A fact as stored (``facts`` table) and merged: the typed value plus what the model wrote."""

    model_config = ConfigDict(frozen=True)

    field: BriefField
    value: BriefValue
    raw_value: str
    scenario: Scenario
    evidence: str
    confidence: Confidence
    turn: int


@dataclass(slots=True)
class ExtractionResult:
    facts: list[Fact]
    user_says_complete: bool = False
    errors: list[str] = field(default_factory=list)  # validation messages of the last attempt
    gave_up: bool = False  # no valid output after the retry
    calls: list[CallStats] = field(default_factory=list)


def extraction_input(
    *, brief: HouseBrief, provenance: Provenance, last_question: str | None, message: str
) -> str:
    """The dynamic part of the extraction prompt. Only user-given values: defaults are not facts to repeat."""
    known = [
        f"{name}={_text(getattr(brief, name))}"
        for name in BRIEF_FIELDS
        if getattr(brief, name) is not None and (item := provenance.get(name)) and item.source is Source.user
    ]
    return (
        f"Known so far from the person: {'; '.join(known) or 'nothing yet'}\n"
        f"Last question asked: {last_question or 'none'}\n"
        f"Message: {message!r}"
    )


def _text(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def validate_extraction(text: str, turn: int) -> tuple[RawExtraction | None, list[Fact], list[str]]:
    """The parsed output, its typed facts and one message per problem (invalid JSON or values)."""
    try:
        raw = RawExtraction.model_validate_json(text)
    except ValidationError as error:
        problems = [f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}" for e in error.errors()[:10]]
        return None, [], problems
    facts: list[Fact] = []
    problems = []
    for item in raw.facts:
        try:
            value = parse_value(item.field, item.value)
        except ParseError as error:
            problems.append(str(error))
            continue
        if value is None:  # "I don't know" for a number: nothing to store
            continue
        facts.append(
            Fact(
                field=item.field,
                value=value,
                raw_value=item.value,
                scenario=item.scenario,
                evidence=item.evidence,
                confidence=item.confidence,
                turn=turn,
            )
        )
    return raw, facts, problems


def retry_input(dynamic: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        f"{dynamic}\n\nYour previous answer had these problems:\n{listed}\n"
        "Answer again with the corrected JSON. Leave out a fact you cannot state correctly."
    )


async def run_extraction(llm: AsyncOpenAI, dynamic: str, *, model: str, turn: int) -> ExtractionResult:
    """Extract, validate, retry once with the problems, then give up (no facts) if still invalid."""
    schema = extraction_schema()
    text, stats = await calls.extract(llm, dynamic, model=model, response_schema=schema)
    raw, facts, problems = validate_extraction(text, turn)
    stats_list = [stats]
    if problems:
        log.info("extraction retry turn=%d problems=%d first=%r", turn, len(problems), problems[0])
        text, stats = await calls.extract(
            llm, retry_input(dynamic, problems), model=model, response_schema=schema
        )
        stats_list.append(stats)
        retry_raw, retry_facts, problems = validate_extraction(text, turn)
        if retry_raw is not None:
            raw, facts = retry_raw, retry_facts
    if raw is None:
        log.warning("extraction gave up turn=%d problems=%r", turn, problems)
        return ExtractionResult(facts=[], errors=problems, gave_up=True, calls=stats_list)
    if problems:
        log.info("extraction dropped facts turn=%d problems=%r", turn, problems)
    return ExtractionResult(
        facts=facts, user_says_complete=raw.user_says_complete, errors=problems, calls=stats_list
    )
