"""Eval set and runner logic with a fake LLM and fake jobs (the live run needs the stack)."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fakes import FakeLLM
from langgraph.checkpoint.memory import InMemorySaver
from openai import AsyncOpenAI
from test_workflow import KPIS, FakeJobs

from llmtrano.brief.model import BuildingType, FieldProvenance, HouseBrief, Source
from llmtrano.brief.plan import Question
from llmtrano.evals.cases import EvalCase, load_cases
from llmtrano.evals.metrics import CaseResult, FieldCheck, markdown, matches, percentile, summarize
from llmtrano.evals.run import StatsRecorder, check_fields, gate_comparison, next_message, run_case
from llmtrano.settings import Settings
from llmtrano.workflow.deps import Deps, NullRecorder
from llmtrano.workflow.graph import build_graph, make_serializer
from llmtrano.workflow.state import WorkflowState

CASES = Path(__file__).parents[1] / "evals" / "cases"


def test_the_eval_set_has_the_planned_cases() -> None:
    cases = {case.id: case for case in load_cases(CASES)}
    assert set(cases) == {
        # phase 1
        "tutorial_house",
        "vague_1960s_semi",
        "precise_terraced",
        "mixed_renovation",
        "correction",
        "dutch_rijwoning",
        "dutch_vrijstaand",
        "apartment",
        "contradictory_area",
        "unusual_units",
        # phase 2
        "reference_house",
        "gas_boiler_house",
        "heat_pump_day_night",
        "system_d_ventilation",
        "french_maison",
    }
    assert sorted(c.id for c in cases.values() if c.gate) == ["reference_house", "tutorial_house"]
    assert cases["apartment"].outcome == "unsupported"
    assert cases["mixed_renovation"].renovation == {"wall_insulation": "good", "glazing": "triple"}
    assert {c.language for c in cases.values()} == {"English", "Dutch", "French"}
    gate = cases["reference_house"]
    assert gate.outcome == "compare" and gate.reference is not None
    assert (gate.reference.heating_kwh_per_m2, gate.reference.renovation_heating_kwh_per_m2) == (130, 17.6)
    assert gate.renovation["heating_system"] == "heat_pump_air_water"


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown Brief fields"):
        EvalCase(id="x", opening="y", expected={"pool": True})


@pytest.mark.parametrize(
    ("expected", "got", "ok"),
    [(130, 130.0, True), (130, 131.2, True), (130, 132, False), ("terraced", BuildingType.terraced, True),
     ("good", "medium", False), (2, None, False), (120.8, "120.77", True)],
)  # fmt: skip
def test_matches(expected: Any, got: Any, ok: bool) -> None:
    assert matches(expected, got) is ok


def test_next_message_follow_ups_then_answers_then_done() -> None:
    case = EvalCase(id="x", opening="o", follow_ups=["sorry"], answers={"storeys": "Two floors."})
    state = WorkflowState(
        session_id="s",
        pending_questions=[
            Question(field="storeys", kind="missing", prompt_seed="?", options=[]),
            Question(field="glazing", kind="missing", prompt_seed="?", options=[]),
        ],
    )
    follow_ups = list(case.follow_ups)
    assert next_message(case, state, follow_ups) == "sorry"
    assert next_message(case, state, follow_ups) == "Two floors. I don't know"
    assert next_message(case, WorkflowState(session_id="s"), follow_ups) == "That's all I know."


def test_check_fields_counts_only_user_values() -> None:
    case = EvalCase(id="x", opening="o", expected={"storeys": 2, "glazing": "triple", "occupants": 3})
    state = WorkflowState(
        session_id="s",
        brief=HouseBrief(storeys=2, glazing="triple", occupants=2),
        provenance={
            "storeys": FieldProvenance(source=Source.user),
            "glazing": FieldProvenance(source=Source.user),
            "occupants": FieldProvenance(source=Source.default),
        },
    )
    checks = check_fields(case, state)
    assert {name: check.ok for name, check in checks.items()} == {
        "storeys": True,
        "glazing": True,
        "occupants": False,
    }
    assert checks["occupants"].got is None


def _graph(llm: FakeLLM, jobs: FakeJobs, recorder: Any) -> Any:
    deps = Deps(llm=cast(AsyncOpenAI, llm), jobs=jobs, recorder=recorder, settings=Settings())
    return build_graph(deps, InMemorySaver(serde=make_serializer()))


def _facts(items: dict[str, str], complete: bool = False) -> str:
    return json.dumps(
        {
            "facts": [
                {"field": f, "value": v, "scenario": "baseline", "evidence": v, "confidence": "high"}
                for f, v in items.items()
            ],
            "user_says_complete": complete,
        }
    )


async def test_run_case_scores_a_simulated_case() -> None:
    case = next(c for c in load_cases(CASES) if c.id == "precise_terraced")
    llm = FakeLLM()
    llm.queue("extract", _facts({name: str(value) for name, value in case.expected.items()}, complete=True))
    llm.queue("respond", "All noted, please confirm.")
    llm.queue("interpret", "The house needs 6469 kWh per year.")
    recorder = StatsRecorder(NullRecorder())
    result = await run_case(
        case, _graph(llm, FakeJobs(), recorder), "s1", model="m", simulate=True, stats=recorder
    )
    assert result.outcome == "simulated"
    assert result.turns_to_confirmation == 1
    assert all(check.ok for check in result.fields.values()), result.fields
    assert result.compiled and result.check_ok and result.simulation_ok and result.guardrail_first_time
    assert result.kpis is not None and {key: result.kpis[key] for key in KPIS} == KPIS
    assert len(result.ttft_ms) == 1 and len(result.turn_s) == 1
    assert result.transcript[0] == {"role": "user", "content": case.opening}


async def test_run_case_apartment_is_unsupported() -> None:
    case = next(c for c in load_cases(CASES) if c.id == "apartment")
    llm = FakeLLM()
    llm.queue("extract", _facts({"building_type": "apartment", "heated_floor_area_m2": "85 m2"}))
    llm.queue("respond", "Apartments are not supported yet.")
    jobs = FakeJobs()
    result = await run_case(case, _graph(llm, jobs, NullRecorder()), "s2", model=None, simulate=True)
    assert result.outcome == "unsupported"
    assert result.fields["building_type"].ok
    assert jobs.checked == [] and not result.compiled


def test_summary_and_markdown() -> None:
    ok, wrong = FieldCheck(expected=1, got=1, ok=True), FieldCheck(expected=2, got=3, ok=False)
    results = [
        CaseResult(
            id="a",
            expected_outcome="simulate",
            outcome="simulated",
            turns_to_confirmation=3,
            fields={"storeys": ok, "occupants": wrong},
            renovation={"glazing": ok},
            compiled=True,
            check_ok=True,
            simulated=True,
            simulation_ok=True,
            guardrail_first_time=False,
            kpis=KPIS,
            ttft_ms=[100, 300],
            turn_s=[2.0, 4.0],
        ),
        CaseResult(
            id="b", expected_outcome="unsupported", outcome="unsupported", fields={"building_type": ok}
        ),
    ]
    summary = summarize(results)
    assert (summary.field_accuracy, summary.scenario_accuracy) == (2 / 3, 1.0)
    assert (summary.valid_model_rate, summary.simulation_success, summary.number_faithfulness) == (
        1.0,
        1.0,
        0.0,
    )
    assert summary.unsupported_handled == 1.0
    assert (summary.ttft_ms_median, summary.ttft_ms_p95, summary.turns_to_confirmation_median) == (
        200,
        300,
        3,
    )
    text = markdown(summary, results, {"date": "2026-10-20", "interactive model": "m"})
    assert "| Field accuracy | 67% (2/3) |" in text
    assert "| a | simulated | 3 | 1/2 | 1/1 | 6,469 | - | no |" in text
    assert summary.renovation_success is None and summary.reference_checks_ok is None
    assert "- a.occupants: expected 2, got 3" in text


def test_percentile_and_gate_comparison() -> None:
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([], 95) is None
    gate = CaseResult(
        id="t",
        expected_outcome="simulate",
        gate=True,
        kpis={"heating_energy_kwh": 5000.0, "peak_heating_w": 3000.0},
    )
    comparison = gate_comparison(gate, {"heating_energy_kwh": 6000.0, "peak_heating_w": 3500.0})
    assert comparison is not None and comparison["difference"] == "-16.7%"


async def test_run_case_compares_the_renovation_and_checks_the_reference() -> None:
    from test_workflow import ScenarioJobs

    case = next(c for c in load_cases(CASES) if c.id == "reference_house")
    llm = FakeLLM()
    baseline = {name: str(value) for name, value in case.expected.items()}
    plans = [
        {"field": f, "value": str(v), "scenario": "renovation", "evidence": v, "confidence": "high"}
        for f, v in case.renovation.items()
    ]
    llm.queue(
        "extract",
        json.dumps({"facts": [*json.loads(_facts(baseline))["facts"], *plans], "user_says_complete": True}),
    )
    llm.queue("respond", "All noted, please confirm.")
    llm.queue("interpret", "Before 6469 kWh, after 3100 kWh.")
    result = await run_case(
        case, _graph(llm, ScenarioJobs(), NullRecorder()), "s3", model=None, simulate=True
    )
    assert result.outcome == "simulated" and result.renovation_simulated is True
    assert result.renovation_kpis is not None and result.comparison is not None
    assert all(check.ok for check in result.renovation.values()), result.renovation
    metrics = {check.metric: check for check in result.reference_checks}
    assert set(metrics) == {"heating_kwh_per_m2", "renovation_heating_kwh_per_m2"}
    assert (
        metrics["heating_kwh_per_m2"].got == 53.91 and metrics["heating_kwh_per_m2"].ok is False
    )  # fake KPIs
    assert "trano docs/validation" in metrics["heating_kwh_per_m2"].source
    text = markdown(summarize([result]), [result], {"date": "d"})
    assert "## Gate: simulated values against their published references" in text
    assert "| reference_house | heating_kwh_per_m2 | 130 | 53.9 | 35% | no |" in text
    assert "| Renovation simulated (compare cases) | 100% |" in text
