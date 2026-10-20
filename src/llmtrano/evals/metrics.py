"""Eval metrics (#54 offline evaluation, #58 eval gate), computed from per-case results.

| Metric | Definition |
| --- | --- |
| Field accuracy | stated fields extracted with the right value / stated fields |
| Scenario accuracy | renovation facts tagged renovation / renovation facts |
| Turns to confirmation | user turns until the confirm phase |
| Valid model rate | compiled models passing ``check_yaml`` / cases reaching compile |
| Simulation success | successful runs / runs |
| Number faithfulness | explanations passing the guardrail first time / explanations |
| Latency | median and p95 TTFT and turn time |
"""

import math
import statistics
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

NUMBER_TOLERANCE = 0.01  # relative: "130 m2" for 130, "2.44 m" for 8 ft


class FieldCheck(BaseModel):
    expected: Any
    got: Any
    ok: bool


class CaseResult(BaseModel):
    id: str
    gate: bool = False
    expected_outcome: Literal["simulate", "unsupported"]
    outcome: Literal["simulated", "failed", "confirmed", "unsupported", "stalled", "error"] = "stalled"
    turns_to_confirmation: int | None = None
    fields: dict[str, FieldCheck] = Field(default_factory=dict)
    renovation: dict[str, FieldCheck] = Field(default_factory=dict)
    compiled: bool = False
    check_ok: bool | None = None
    simulated: bool = False
    simulation_ok: bool | None = None
    guardrail_first_time: bool | None = None
    kpis: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    ttft_ms: list[float] = Field(default_factory=list)  # the interactive replies
    turn_s: list[float] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)
    explanation: str | None = None


def plain(value: Any) -> Any:
    return value.value if isinstance(value, StrEnum) else value


def matches(expected: Any, got: Any) -> bool:
    """Numbers within 1% (relative), everything else equal as text."""
    got = plain(got)
    if got is None:
        return False
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        try:
            return math.isclose(float(got), float(expected), rel_tol=NUMBER_TOLERANCE)
        except (TypeError, ValueError):
            return False
    return str(got).strip().lower() == str(expected).strip().lower()


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None without values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


class Summary(BaseModel):
    cases: int
    field_accuracy: float | None
    fields_ok: int
    fields_stated: int
    scenario_accuracy: float | None
    renovation_ok: int
    renovation_stated: int
    turns_to_confirmation_median: float | None
    turns_to_confirmation_max: int | None
    confirmed: int
    valid_model_rate: float | None
    simulation_success: float | None
    number_faithfulness: float | None
    unsupported_handled: float | None
    ttft_ms_median: float | None
    ttft_ms_p95: float | None
    turn_s_median: float | None
    turn_s_p95: float | None


def summarize(results: Sequence[CaseResult]) -> Summary:
    checks = [check for result in results for check in result.fields.values()]
    renovations = [check for result in results for check in result.renovation.values()]
    turns = [r.turns_to_confirmation for r in results if r.turns_to_confirmation is not None]
    compiled = [r for r in results if r.compiled]
    simulated = [r for r in results if r.simulated]
    explained = [r for r in results if r.guardrail_first_time is not None]
    unsupported = [r for r in results if r.expected_outcome == "unsupported"]
    ttft = [value for r in results for value in r.ttft_ms]
    turn_s = [value for r in results for value in r.turn_s]
    return Summary(
        cases=len(results),
        field_accuracy=_ratio(sum(c.ok for c in checks), len(checks)),
        fields_ok=sum(c.ok for c in checks),
        fields_stated=len(checks),
        scenario_accuracy=_ratio(sum(c.ok for c in renovations), len(renovations)),
        renovation_ok=sum(c.ok for c in renovations),
        renovation_stated=len(renovations),
        turns_to_confirmation_median=statistics.median(turns) if turns else None,
        turns_to_confirmation_max=max(turns) if turns else None,
        confirmed=len(turns),
        valid_model_rate=_ratio(sum(bool(r.check_ok) for r in compiled), len(compiled)),
        simulation_success=_ratio(sum(bool(r.simulation_ok) for r in simulated), len(simulated)),
        number_faithfulness=_ratio(sum(bool(r.guardrail_first_time) for r in explained), len(explained)),
        unsupported_handled=_ratio(sum(r.outcome == "unsupported" for r in unsupported), len(unsupported)),
        ttft_ms_median=statistics.median(ttft) if ttft else None,
        ttft_ms_p95=percentile(ttft, 95),
        turn_s_median=statistics.median(turn_s) if turn_s else None,
        turn_s_p95=percentile(turn_s, 95),
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def markdown(summary: Summary, results: Sequence[CaseResult], header: dict[str, str]) -> str:
    lines = [f"# Eval run {header.get('date', '')}", ""]
    lines += [f"- {key}: {value}" for key, value in header.items()]
    lines += [
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Field accuracy | {_pct(summary.field_accuracy)} ({summary.fields_ok}/{summary.fields_stated}) |",
        f"| Scenario accuracy | {_pct(summary.scenario_accuracy)} "
        f"({summary.renovation_ok}/{summary.renovation_stated}) |",
        f"| Turns to confirmation | median {_num(summary.turns_to_confirmation_median)}, "
        f"max {summary.turns_to_confirmation_max or 'n/a'} ({summary.confirmed} cases confirmed) |",
        f"| Valid model rate | {_pct(summary.valid_model_rate)} |",
        f"| Simulation success | {_pct(summary.simulation_success)} |",
        f"| Number faithfulness | {_pct(summary.number_faithfulness)} |",
        f"| Unsupported handled (apartments) | {_pct(summary.unsupported_handled)} |",
        f"| TTFT (reply) | median {_num(summary.ttft_ms_median, 0)} ms, "
        f"p95 {_num(summary.ttft_ms_p95, 0)} ms |",
        f"| Turn time | median {_num(summary.turn_s_median)} s, p95 {_num(summary.turn_s_p95)} s |",
        "",
        "| Case | Outcome | Turns | Fields | Renovation | Heating (kWh) | Guardrail first time |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        fields = f"{sum(c.ok for c in r.fields.values())}/{len(r.fields)}"
        renovation = (
            f"{sum(c.ok for c in r.renovation.values())}/{len(r.renovation)}" if r.renovation else "-"
        )
        heating = f"{r.kpis['heating_energy_kwh']:,.0f}" if r.kpis else "-"
        guardrail = "-" if r.guardrail_first_time is None else ("yes" if r.guardrail_first_time else "no")
        lines.append(
            f"| {r.id} | {r.outcome} | {r.turns_to_confirmation or '-'} | {fields} | {renovation} "
            f"| {heating} | {guardrail} |"
        )
    wrong = [
        f"- {r.id}.{name}: expected {check.expected!r}, got {check.got!r}"
        for r in results
        for name, check in {**r.fields, **{f"renovation.{k}": v for k, v in r.renovation.items()}}.items()
        if not check.ok
    ]
    if wrong:
        lines += ["", "## Wrong or missing fields", "", *wrong]
    failures = [f"- {r.id}: {r.outcome}; {'; '.join(r.errors)}" for r in results if r.errors]
    if failures:
        lines += ["", "## Errors", "", *failures]
    return "\n".join(lines) + "\n"
