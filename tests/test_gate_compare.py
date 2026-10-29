"""The eval gate thresholds and the A/B comparison of two runs (phase 3)."""

import json
from pathlib import Path

import pytest
from test_workflow import KPIS

from llmtrano.evals.compare import Run, compare_runs, main, markdown
from llmtrano.evals.gate import DEFAULT_GATE, Threshold, check_gate, gate_markdown, load_gate
from llmtrano.evals.metrics import CaseResult, FieldCheck, summarize

GATE = Path(__file__).parents[1] / "evals" / "gate.yaml"


def _result(
    case_id: str, *, simulated: bool = True, fields_ok: bool = True, guardrail: bool = True
) -> CaseResult:
    check = FieldCheck(expected=2, got=2 if fields_ok else 3, ok=fields_ok)
    return CaseResult(
        id=case_id,
        expected_outcome="simulate",
        outcome="simulated" if simulated else "failed",
        turns_to_confirmation=2,
        fields={"storeys": check},
        compiled=True,
        check_ok=True,
        simulated=True,
        simulation_ok=simulated,
        guardrail_first_time=guardrail,
        kpis=KPIS if simulated else None,
        turn_s=[3.0],
        ttft_ms=[200.0],
    )


def test_thresholds_check_min_max_and_missing() -> None:
    assert Threshold(min=0.9).check(0.95) and not Threshold(min=0.9).check(0.8)
    assert Threshold(max=6).check(6) and not Threshold(max=6).check(7)
    assert not Threshold(min=0.9).check(None)  # not measured: not passed
    with pytest.raises(ValueError, match="exactly one"):
        Threshold(min=1, max=2)


def test_the_committed_gate_loads_and_names_summary_metrics() -> None:
    gate = load_gate(GATE)
    assert {"field_accuracy", "number_faithfulness", "turns_to_confirmation_max"} <= set(gate)
    assert Path("evals/gate.yaml") == DEFAULT_GATE


def test_gate_result_and_markdown(tmp_path: Path) -> None:
    summary = summarize([_result("a"), _result("b", fields_ok=False)])
    gate = {"field_accuracy": Threshold(min=0.9), "simulation_success": Threshold(min=0.95)}
    result = check_gate(summary, gate)
    assert [row.ok for row in result.rows] == [False, True] and not result.passed
    text = gate_markdown(result)
    assert "## Gate: FAILED" in text and "| field_accuracy | 0.5 | >= 0.9 | no |" in text
    (tmp_path / "gate.yaml").write_text("field_accuracy: {min: 0.4}\n")
    assert check_gate(summary, load_gate(tmp_path / "gate.yaml")).passed
    (tmp_path / "bad.yaml").write_text("pool_temperature: {min: 1}\n")
    with pytest.raises(ValueError, match="not in the summary"):
        load_gate(tmp_path / "bad.yaml")


def _payload(path: Path, model: str, results: list[CaseResult]) -> Path:
    payload = {
        "header": {"date": "2026-10-29", "interactive model": model},
        "summary": summarize(results).model_dump(),
        "gate": None,
        "cases": [r.model_dump() for r in results],
    }
    path.write_text(json.dumps(payload))
    return path


def test_compare_runs_reports_deltas_regressions_and_the_gate(tmp_path: Path) -> None:
    a = _payload(tmp_path / "a.json", "m1", [_result("x"), _result("y", guardrail=False), _result("z")])
    b = _payload(
        tmp_path / "b.json",
        "m2",
        [_result("x", fields_ok=False), _result("y"), _result("z", simulated=False)],
    )
    run_a, run_b = Run.load(a), Run.load(b)
    report = compare_runs(run_a, run_b, GATE)
    assert [c.what for c in report.regressions] == ["field storeys", "simulated"]
    assert [(c.case, c.what) for c in report.improvements] == [("y", "guardrail first time")]
    assert [(c.case, c.before, c.after) for c in report.outcome_changes] == [("z", "simulated", "failed")]
    deltas = {metric: delta for metric, _, _, delta in report.deltas}
    assert deltas["field_accuracy"] == pytest.approx(-1 / 3)
    assert report.gate_a is not None and report.gate_b is not None and not report.gate_b.passed
    text = markdown(run_a, run_b, report)
    assert "| field_accuracy | 1 | 0.667 | -0.333 (worse) |" in text
    assert "- x: field storeys yes -> no" in text and "## Gate of B: FAILED" in text
    arguments = [
        str(a),
        str(b),
        "--gate",
        str(GATE),
        "--fail-on-regression",
        "--output",
        str(tmp_path / "r.md"),
    ]
    assert main(arguments) == 1
    assert (tmp_path / "r.md").read_text() == text
    assert main([str(a), str(a)]) == 0
