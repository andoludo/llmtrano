"""The LLM judge: schema, validation, retry, the eval metrics and the runner flag (fake LLM)."""

import json
from pathlib import Path
from typing import Any, cast

from fakes import FakeLLM
from openai import AsyncOpenAI
from test_evals import _facts, _graph
from test_workflow import KPIS, FakeJobs

from llmtrano.brief.model import FieldProvenance, HouseBrief, Source
from llmtrano.evals.cases import load_cases
from llmtrano.evals.judge import JudgeScore, judge_input, judge_schema, run_judge, validate_score
from llmtrano.evals.metrics import CaseResult, markdown, summarize
from llmtrano.evals.run import run_case
from llmtrano.llm import calls
from llmtrano.workflow.deps import NullRecorder

CASES = Path(__file__).parents[1] / "evals" / "cases"
GOOD = json.dumps({"faithful": True, "clear": 4, "actionable": 3, "issues": []})
BAD = json.dumps(
    {"faithful": False, "clear": 2, "actionable": 1, "issues": ["It compares with an EPC label."]}
)


def test_schema_is_flat_and_complete() -> None:
    schema = judge_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "anyOf" not in text
    assert schema["required"] == ["faithful", "clear", "actionable", "issues"]


def test_validate_score() -> None:
    score, problems = validate_score(GOOD)
    assert score == JudgeScore(faithful=True, clear=4, actionable=3) and problems == []
    score, problems = validate_score(
        json.dumps({"faithful": True, "clear": 9, "actionable": 3, "issues": []})
    )
    assert score is None and problems == ["clear: Input should be less than or equal to 5"]


def test_judge_input_holds_the_explanation_the_numbers_and_the_house() -> None:
    brief = HouseBrief(storeys=2)
    text = judge_input(
        explanation="It needs 6469 kWh.",
        kpis=KPIS,
        brief=brief,
        provenance={"storeys": FieldProvenance(source=Source.user, evidence="two")},
        comparison={"heating_energy_kwh": {"saving": 1.0}},
        language="Dutch",
    )
    assert text.startswith("Language of the explanation: Dutch\nExplanation to grade:\nIt needs 6469 kWh.")
    assert '"heating_energy_kwh": 6469.2' in text and "Comparison:" in text
    assert text.endswith('House:\n- storeys: 2 (you said "two")')


async def test_run_judge_retries_once_then_gives_up() -> None:
    llm = FakeLLM().queue("judge", "not json", GOOD)
    score, stats = await run_judge(cast(AsyncOpenAI, llm), "dynamic", model="m")
    assert score is not None and score.faithful and len(stats) == 2
    assert "Your previous answer had these problems" in llm.calls("judge")[1]["messages"][-1]["content"]
    request = llm.calls("judge")[0]
    assert request["response_format"]["json_schema"]["name"] == "judge"
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}
    assert request["temperature"] == calls.JUDGE_SAMPLING.temperature
    llm = FakeLLM().queue("judge", "{}", "{}")
    score, _ = await run_judge(cast(AsyncOpenAI, llm), "dynamic", model="m")
    assert score is None


def test_judge_metrics_in_summary_and_markdown() -> None:
    results = [
        CaseResult(id="a", expected_outcome="simulate", judge=json.loads(GOOD)),
        CaseResult(id="b", expected_outcome="simulate", judge=json.loads(BAD)),
        CaseResult(id="c", expected_outcome="simulate"),
    ]
    summary = summarize(results)
    assert (summary.judge_faithful, summary.judge_clear_mean, summary.judge_actionable_mean) == (
        0.5,
        3.0,
        2.0,
    )
    text = markdown(summary, results, {"date": "d"})
    assert (
        "| Judge: faithful | 50% |" in text
        and "| Judge: clarity, actionability (1 to 5) | 3.0, 2.0 |" in text
    )
    assert "- b (NOT faithful, clear 2, actionable 1): It compares with an EPC label." in text
    assert "- a (" not in text  # faithful without issues: not listed


async def test_run_case_with_the_judge() -> None:
    case = next(c for c in load_cases(CASES) if c.id == "precise_terraced")
    llm = FakeLLM()
    llm.queue("extract", _facts({name: str(value) for name, value in case.expected.items()}, complete=True))
    llm.queue("respond", "All noted, please confirm.")
    llm.queue("interpret", "The house needs 6469 kWh per year.")
    llm.queue("judge", BAD)
    result = await run_case(
        case,
        _graph(llm, FakeJobs(), NullRecorder()),
        "s9",
        model="m",
        simulate=True,
        judge=(llm, "judge-model"),
    )
    assert result.outcome == "simulated" and result.judge == json.loads(BAD)
    assert llm.calls("judge")[0]["model"] == "judge-model"
    judged: dict[str, Any] = result.judge or {}
    assert judged["issues"] == ["It compares with an EPC label."]
