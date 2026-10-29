"""The eval gate: thresholds on the summary metrics, read from ``evals/gate.yaml`` (#58 eval gate).

A threshold is ``metric: {min: x}`` or ``metric: {max: x}`` on a field of ``Summary``. A metric the run could
not measure (None: no case of that kind, or nothing simulated) fails its threshold: a gate that cannot be
checked is not passed. ``python -m llmtrano.evals.run --gate`` exits non-zero when a threshold fails;
``python -m llmtrano.evals.compare`` reports the gate for each run.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from llmtrano.evals.metrics import Summary

DEFAULT_GATE = Path("evals/gate.yaml")


class Threshold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _one_bound(self) -> "Threshold":
        if (self.min is None) == (self.max is None):
            raise ValueError("a threshold has exactly one of min and max")
        return self

    def check(self, value: float | None) -> bool:
        if value is None:
            return False
        return value >= self.min if self.min is not None else value <= float(self.max or 0)

    def text(self) -> str:
        return f">= {self.min:g}" if self.min is not None else f"<= {self.max:g}"


class GateRow(BaseModel):
    metric: str
    value: float | None
    threshold: str
    ok: bool


class GateResult(BaseModel):
    passed: bool
    rows: list[GateRow]


def load_gate(path: Path = DEFAULT_GATE) -> dict[str, Threshold]:
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    thresholds = {name: Threshold.model_validate(spec) for name, spec in data.items()}
    unknown = set(thresholds) - set(Summary.model_fields)
    if unknown:
        raise ValueError(f"gate metrics not in the summary: {sorted(unknown)}")
    return thresholds


def check_gate(summary: Summary, gate: dict[str, Threshold]) -> GateResult:
    rows = []
    for metric, threshold in gate.items():
        value = getattr(summary, metric)
        rows.append(
            GateRow(metric=metric, value=value, threshold=threshold.text(), ok=threshold.check(value))
        )
    return GateResult(passed=all(row.ok for row in rows), rows=rows)


def gate_markdown(result: GateResult) -> str:
    lines = [
        f"## Gate: {'passed' if result.passed else 'FAILED'}",
        "",
        "| Metric | Value | Threshold | OK |",
        "| --- | --- | --- | --- |",
    ]
    for row in result.rows:
        value = "not measured" if row.value is None else f"{row.value:g}"
        lines.append(f"| {row.metric} | {value} | {row.threshold} | {'yes' if row.ok else 'no'} |")
    return "\n".join(lines) + "\n"
