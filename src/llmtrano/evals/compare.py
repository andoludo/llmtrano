"""Compare two eval runs (A/B of a prompt or model change) and report regressions (#58, #61).

    uv run python -m llmtrano.evals.compare evals/results/a.json evals/results/b.json
    uv run python -m llmtrano.evals.compare a.json b.json --gate evals/gate.yaml --fail-on-regression

Reads the JSON payloads ``llmtrano.evals.run`` writes, and prints a Markdown report: the header of each
run, the metric deltas (B minus A), the cases whose outcome changed, the field checks that regressed or
improved, and the gate of each run. A regression is a case that was simulated in A and is not in B, a field
right in A and wrong in B, or a guardrail that passed in A and not in B.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from llmtrano.evals.gate import GateResult, check_gate, gate_markdown, load_gate
from llmtrano.evals.metrics import CaseResult, Summary

DELTA_METRICS = (
    "field_accuracy",
    "scenario_accuracy",
    "turns_to_confirmation_median",
    "valid_model_rate",
    "simulation_success",
    "number_faithfulness",
    "unsupported_handled",
    "renovation_success",
    "reference_checks_ok",
    "ttft_ms_median",
    "turn_s_median",
)
HIGHER_IS_BETTER = frozenset(DELTA_METRICS) - {
    "turns_to_confirmation_median",
    "ttft_ms_median",
    "turn_s_median",
}


class Run(BaseModel):
    header: dict[str, str]
    summary: Summary
    cases: list[CaseResult]

    @classmethod
    def load(cls, path: Path) -> "Run":
        payload = json.loads(path.read_text())
        return cls(
            header=payload["header"],
            summary=Summary.model_validate(payload["summary"]),
            cases=payload["cases"],
        )


class Change(BaseModel):
    case: str
    what: str
    before: str
    after: str


class Report(BaseModel):
    deltas: list[tuple[str, float | None, float | None, float | None]]
    regressions: list[Change] = Field(default_factory=list)
    improvements: list[Change] = Field(default_factory=list)
    outcome_changes: list[Change] = Field(default_factory=list)
    gate_a: GateResult | None = None
    gate_b: GateResult | None = None

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def compare_runs(a: Run, b: Run, gate_path: Path | None = None) -> Report:
    deltas = [
        (
            metric,
            getattr(a.summary, metric),
            getattr(b.summary, metric),
            _delta(getattr(a.summary, metric), getattr(b.summary, metric)),
        )
        for metric in DELTA_METRICS
    ]
    report = Report(deltas=deltas)
    before = {case.id: case for case in a.cases}
    for case in b.cases:
        old = before.get(case.id)
        if old is None:
            continue
        if old.outcome != case.outcome:
            report.outcome_changes.append(
                Change(case=case.id, what="outcome", before=old.outcome, after=case.outcome)
            )
        _collect(report, case.id, "simulated", old.outcome == "simulated", case.outcome == "simulated")
        _collect(report, case.id, "guardrail first time", old.guardrail_first_time, case.guardrail_first_time)
        for name, check in case.fields.items():
            old_check = old.fields.get(name)
            if old_check is not None:
                _collect(report, case.id, f"field {name}", old_check.ok, check.ok)
        for name, check in case.renovation.items():
            old_check = old.renovation.get(name)
            if old_check is not None:
                _collect(report, case.id, f"renovation {name}", old_check.ok, check.ok)
    if gate_path is not None:
        gate = load_gate(gate_path)
        report.gate_a, report.gate_b = check_gate(a.summary, gate), check_gate(b.summary, gate)
    return report


def _collect(report: Report, case_id: str, what: str, before: bool | None, after: bool | None) -> None:
    if before is None or after is None or before == after:
        return
    change = Change(case=case_id, what=what, before=_yes(before), after=_yes(after))
    (report.improvements if after else report.regressions).append(change)


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3g}"


def markdown(a: Run, b: Run, report: Report) -> str:
    lines = ["# Eval comparison", ""]
    for label, run in (("A", a), ("B", b)):
        lines.append(f"- {label}: " + ", ".join(f"{key} {value}" for key, value in run.header.items()))
    lines += ["", "| Metric | A | B | B minus A |", "| --- | --- | --- | --- |"]
    for metric, before, after, delta in report.deltas:
        mark = ""
        if delta:
            better = delta > 0 if metric in HIGHER_IS_BETTER else delta < 0
            mark = " (better)" if better else " (worse)"
        lines.append(f"| {metric} | {_num(before)} | {_num(after)} | {_num(delta)}{mark} |")
    for title, changes in (
        ("Regressions", report.regressions),
        ("Improvements", report.improvements),
        ("Outcome changes", report.outcome_changes),
    ):
        lines += ["", f"## {title}", ""]
        lines += [f"- {c.case}: {c.what} {c.before} -> {c.after}" for c in changes] or ["- none"]
    for label, gate in (("A", report.gate_a), ("B", report.gate_b)):
        if gate is not None:
            lines += ["", gate_markdown(gate).replace("## Gate", f"## Gate of {label}").rstrip()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two eval runs.")
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--gate", type=Path, help="gate thresholds (evals/gate.yaml)")
    parser.add_argument("--output", type=Path, help="write the Markdown report here too")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)
    a, b = Run.load(args.a), Run.load(args.b)
    report = compare_runs(a, b, args.gate)
    text = markdown(a, b, report)
    if args.output:
        args.output.write_text(text)
    sys.stdout.write(text)
    return 1 if args.fail_on_regression and report.has_regressions else 0


if __name__ == "__main__":
    sys.exit(main())
