"""LLM judge of the explanation quality (#59 LLM as judge, #21 structured output): a signal, not the gate.

The judge gets the explanation, the same numbers the interpretation got and the house, and answers with a
schema-constrained JSON: ``faithful`` (every claim follows from the inputs), ``clear`` and ``actionable``
(1 to 5) and ``issues``. The number guardrail stays the hard check; the judge adds what a regex cannot see
(a driver the house does not support, a benchmark that was never given) and a readability score to compare
prompts and models with. It runs with the interpretation model, thinking on, near-greedy sampling. An
output that fails validation is retried once with the problems, then counted as "not judged".
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llmtrano.brief.model import HouseBrief, Provenance
from llmtrano.llm import calls
from llmtrano.llm.calls import CallStats
from llmtrano.llm.reply import brief_lines, results_summary

log = logging.getLogger(__name__)


class JudgeScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    faithful: bool
    clear: int = Field(ge=1, le=5)
    actionable: int = Field(ge=1, le=5)
    issues: list[str] = Field(default_factory=list, max_length=5)


def judge_schema() -> dict[str, Any]:
    """Flat JSON Schema for ``response_format`` (no ``$ref``, every property required)."""
    return {
        "type": "object",
        "properties": {
            "faithful": {"type": "boolean"},
            "clear": {"type": "integer", "minimum": 1, "maximum": 5},
            "actionable": {"type": "integer", "minimum": 1, "maximum": 5},
            "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["faithful", "clear", "actionable", "issues"],
        "additionalProperties": False,
    }


def judge_input(
    *,
    explanation: str,
    kpis: Mapping[str, Any],
    brief: HouseBrief,
    provenance: Provenance,
    renovation_kpis: Mapping[str, Any] | None = None,
    comparison: Mapping[str, Any] | None = None,
    language: str = "English",
) -> str:
    import json

    lines = [
        f"Language of the explanation: {language}",
        "Explanation to grade:",
        explanation.strip(),
        "Results (what the assistant was given, rounded):",
        json.dumps(results_summary(kpis), ensure_ascii=False),
    ]
    if renovation_kpis is not None:
        lines += ["Renovation results:", json.dumps(results_summary(renovation_kpis), ensure_ascii=False)]
    if comparison:
        lines += ["Comparison:", json.dumps(comparison, ensure_ascii=False)]
    lines += ["House:", *brief_lines(brief, provenance)]
    return "\n".join(lines)


def validate_score(text: str) -> tuple[JudgeScore | None, list[str]]:
    try:
        return JudgeScore.model_validate_json(text), []
    except ValidationError as error:
        return None, [f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}" for e in error.errors()[:5]]


async def run_judge(
    llm: AsyncOpenAI, dynamic: str, *, model: str
) -> tuple[JudgeScore | None, Sequence[CallStats]]:
    text, stats = await calls.judge(llm, dynamic, model=model, response_schema=judge_schema())
    score, problems = validate_score(text)
    stats_list = [stats]
    if score is None:
        retry = f"{dynamic}\n\nYour previous answer had these problems:\n" + "\n".join(
            f"- {p}" for p in problems
        )
        text, stats = await calls.judge(llm, retry, model=model, response_schema=judge_schema())
        stats_list.append(stats)
        score, problems = validate_score(text)
        if score is None:
            log.warning("judge gave up problems=%r", problems)
    return score, stats_list
