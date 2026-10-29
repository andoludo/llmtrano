"""Run the eval set on the real workflow: live LLM, Postgres checkpointer, the worker (#54, #56 agent tests).

    uv run python -m llmtrano.evals.run                       # default model, every case, with simulations
    uv run python -m llmtrano.evals.run --model qwen3.6-35b-a3b
    uv run python -m llmtrano.evals.run --only tutorial_house --no-simulate
    uv run python -m llmtrano.evals.run --gate                 # exit 1 when evals/gate.yaml fails
    uv run python -m llmtrano.evals.run --judge                # grade each explanation with the LLM judge

Needs the stack (``docker compose --profile gpu up -d``). Each case gets its own session: the opening (and its
follow-ups) is sent, then the scripted user answers the planner's questions by field until the Brief is ready
(at most ``MAX_TURNS`` user turns), then the Brief is approved and the house simulated. For the gate case the
tutorial ``first_model.yaml`` is also simulated directly, for the comparison of the annual heating need.

Writes ``evals/results/<date>-<model>.json`` (every case, its transcript and metrics) and the same name in
``.md`` (the summary).
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from langgraph.types import Command

from llmtrano.brief.defaults import apply_defaults, is_given
from llmtrano.brief.model import Fact, HouseBrief, Provenance, Scenario, Source
from llmtrano.compiler.compile import CompiledModel, trano_schema_hash
from llmtrano.data import tutorial_house_yaml
from llmtrano.evals.cases import MAX_TURNS, EvalCase, load_cases
from llmtrano.evals.gate import DEFAULT_GATE, check_gate, gate_markdown, load_gate
from llmtrano.evals.judge import judge_input, run_judge
from llmtrano.evals.metrics import CaseResult, FieldCheck, ReferenceCheck, markdown, matches, summarize
from llmtrano.llm.calls import CallStats
from llmtrano.logs import configure_logging
from llmtrano.settings import Settings
from llmtrano.workflow.deps import Jobs, PhaseCallback, Recorder
from llmtrano.workflow.state import Phase, WorkflowState

log = logging.getLogger("llmtrano.evals.run")

DONE_MESSAGE = {
    "English": "That's all I know.",
    "Dutch": "Dat is alles wat ik weet.",
    "French": "C'est tout.",
}


class StatsRecorder:
    """Wraps a recorder and keeps the LLM call stats of each session, for the latency metrics."""

    def __init__(self, inner: Recorder) -> None:
        self.inner = inner
        self.stats: defaultdict[str, list[CallStats]] = defaultdict(list)

    async def message(
        self, session_id: str, role: str, content: str, stats: CallStats | None = None
    ) -> uuid.UUID | None:
        if stats is not None:
            self.stats[session_id].append(stats)
        return await self.inner.message(session_id, role, content, stats)

    async def facts(self, session_id: str, facts: list[Fact], accepted: bool) -> None:
        await self.inner.facts(session_id, facts, accepted)

    async def brief(
        self,
        session_id: str,
        brief: HouseBrief,
        provenance: Provenance,
        schema_hash: str | None,
        confirmed: bool,
        scenario: Scenario = "baseline",
    ) -> None:
        await self.inner.brief(session_id, brief, provenance, schema_hash, confirmed, scenario)


def next_message(case: EvalCase, state: WorkflowState, follow_ups: list[str]) -> str:
    """What the scripted user says next: a pending follow-up, else the answers to the questions asked."""
    if follow_ups:
        return follow_ups.pop(0)
    answers = list(dict.fromkeys(case.answer(question.field) for question in state.pending_questions))
    return " ".join(answers) if answers else DONE_MESSAGE[case.language]


def check_fields(case: EvalCase, state: WorkflowState) -> dict[str, FieldCheck]:
    checks = {}
    for name, expected in case.expected.items():
        said = state.provenance.get(name)
        value = getattr(state.brief, name)
        got = value.value if hasattr(value, "value") else value
        stated = said is not None and said.source is Source.user and is_given(value)
        checks[name] = FieldCheck(
            expected=expected, got=got if stated else None, ok=stated and matches(expected, value)
        )
    return checks


def check_renovation(case: EvalCase, state: WorkflowState) -> dict[str, FieldCheck]:
    checks = {}
    for name, expected in case.renovation.items():
        values = [fact.value for fact in state.renovation_facts if fact.field == name]
        ok = any(matches(expected, value) for value in values)
        got = [v.value if hasattr(v, "value") else v for v in values]
        checks[name] = FieldCheck(expected=expected, got=got or None, ok=ok)
    return checks


def reference_checks(case: EvalCase, result: CaseResult) -> list[ReferenceCheck]:
    """The gate check: the simulated heating need per m2 against the case's published reference."""
    if case.reference is None:
        return []
    reference = case.reference
    pairs = [("heating_kwh_per_m2", reference.heating_kwh_per_m2, result.kpis)]
    if reference.renovation_heating_kwh_per_m2 is not None:
        pairs.append(
            ("renovation_heating_kwh_per_m2", reference.renovation_heating_kwh_per_m2, result.renovation_kpis)
        )
    checks = []
    for metric, expected, kpis in pairs:
        got = kpis.get("heating_energy_kwh_per_m2") if kpis else None
        ok = got is not None and abs(got - expected) <= reference.tolerance * expected
        checks.append(
            ReferenceCheck(
                metric=metric,
                reference=expected,
                got=got,
                tolerance=reference.tolerance,
                ok=ok,
                source=reference.source,
            )
        )
    return checks


async def _state(graph: Any, config: dict[str, Any]) -> tuple[WorkflowState, bool]:
    snapshot = await graph.aget_state(config)
    return WorkflowState.model_validate(snapshot.values), bool(snapshot.interrupts)


async def run_case(
    case: EvalCase,
    graph: Any,
    session_id: str,
    *,
    model: str | None,
    simulate: bool,
    stats: StatsRecorder | None = None,
    judge: tuple[Any, str] | None = None,  # (llm client, judge model) to grade the explanation
) -> CaseResult:
    """Drive one case through the graph and score it."""
    result = CaseResult(id=case.id, gate=case.gate, expected_outcome=case.outcome)
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
    follow_ups = list(case.follow_ups)
    message = case.opening
    state: WorkflowState | None = None
    waiting = False
    try:
        for turn in range(1, MAX_TURNS + 1):
            result.transcript.append({"role": "user", "content": message})
            graph_input: dict[str, Any] = {"session_id": session_id, "message": message}
            if model:
                graph_input["model"] = model
            started = time.perf_counter()
            await graph.ainvoke(graph_input, config)
            result.turn_s.append(time.perf_counter() - started)
            state, waiting = await _state(graph, config)
            result.transcript.append({"role": "assistant", "content": state.recent_messages[-1].content})
            if waiting:
                result.turns_to_confirmation = turn
                result.outcome = "confirmed"
                break
            if any(question.kind == "unsupported" for question in state.pending_questions):
                result.outcome = "unsupported"
                break
            message = next_message(case, state, follow_ups)
        if state is not None:
            result.fields = check_fields(case, state)
            result.renovation = check_renovation(case, state)
        if waiting and simulate:
            await graph.ainvoke(Command(resume={"approved": True}), config)
            state, _ = await _state(graph, config)
            result.compiled = state.compiled is not None
            result.check_ok = state.run_id is not None  # the simulation starts only after a passed check
            result.simulated = state.run_id is not None
            result.simulation_ok = state.phase is Phase.done if result.simulated else None
            result.kpis = state.kpis
            result.renovation_kpis = state.renovation_kpis
            result.comparison = state.comparison
            if case.outcome == "compare":
                result.renovation_simulated = (
                    state.renovation_kpis is not None and state.comparison is not None
                )
            result.explanation = state.explanation
            result.guardrail_first_time = state.guardrail.passed_first_time if state.guardrail else None
            result.errors = list(state.errors) if state.phase is Phase.failed else []
            result.outcome = "simulated" if state.phase is Phase.done else "failed"
            result.reference_checks = reference_checks(case, result)
            if state.explanation:
                result.transcript.append({"role": "assistant", "content": state.explanation})
            if judge is not None and state.explanation and state.kpis is not None:
                result.judge = await _judge(judge, state)
    except Exception as error:  # an eval run reports a broken case and goes on
        log.exception("case error case=%s", case.id)
        result.outcome = "error"
        result.errors.append(f"{type(error).__name__}: {error}")
    if stats is not None:
        result.ttft_ms = [s.ttft_ms for s in stats.stats[session_id] if s.call == "respond" and s.ttft_ms]
    return result


async def _judge(judge: tuple[Any, str], state: WorkflowState) -> dict[str, Any] | None:
    llm, model = judge
    filled, provenance = apply_defaults(state.brief, state.provenance)
    dynamic = judge_input(
        explanation=state.explanation or "",
        kpis=state.kpis or {},
        brief=filled,
        provenance=provenance,
        renovation_kpis=state.renovation_kpis,
        comparison=state.comparison,
        language=state.language,
    )
    score, _ = await run_judge(llm, dynamic, model=model)
    return score.model_dump() if score else None


async def simulate_tutorial(jobs: Jobs, session_id: str) -> dict[str, Any] | None:
    """trano's tutorial house simulated directly (the reference of the gate comparison)."""
    text = tutorial_house_yaml()
    model = CompiledModel(
        yaml_text=text, model_dict=yaml.safe_load(text), assumptions=[], schema_hash=trano_schema_hash()
    )

    async def ignore(_phase: str) -> None:
        return None

    on_phase: PhaseCallback = ignore
    outcome = await jobs.simulate(session_id, model, on_phase)
    return outcome.kpis if outcome.ok else None


async def main(argv: list[str] | None = None) -> int:
    from arq import create_pool
    from arq.connections import RedisSettings

    from llmtrano.db.migrate import upgrade_head
    from llmtrano.db.models import Session as SessionRow
    from llmtrano.db.recorder import DbRecorder
    from llmtrano.db.session import make_sessionmaker
    from llmtrano.jobs.queue import WorkerJobs
    from llmtrano.llm.client import make_llm_client
    from llmtrano.workflow.checkpoint import postgres_checkpointer
    from llmtrano.workflow.deps import Deps
    from llmtrano.workflow.graph import build_graph

    parser = argparse.ArgumentParser(description="Run the phase 1 eval set on the live stack.")
    parser.add_argument("--model", help="interactive model (default: LLM_DEFAULT_MODEL)")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases"))
    parser.add_argument("--output", type=Path, default=Path("evals/results"))
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--no-simulate", action="store_true", help="stop at the confirmation")
    parser.add_argument("--judge", action="store_true", help="grade each explanation with the LLM judge")
    parser.add_argument(
        "--gate",
        nargs="?",
        const=DEFAULT_GATE,
        type=Path,
        help="check the summary against the gate thresholds (default evals/gate.yaml); exit 1 when it fails",
    )
    args = parser.parse_args(argv)
    configure_logging()
    settings = Settings()
    model = args.model or settings.llm_default_model
    cases = [c for c in load_cases(args.cases) if not args.only or c.id in args.only]
    upgrade_head(settings.database_url)
    sessions = make_sessionmaker(settings.database_url)
    redis = await create_pool(RedisSettings.from_dsn(settings.valkey_url))
    llm = make_llm_client(settings)
    recorder = StatsRecorder(DbRecorder(sessions))
    jobs = WorkerJobs(redis, sessions, settings)
    results: list[CaseResult] = []
    reference: dict[str, Any] | None = None
    started = datetime.now(UTC)
    try:
        async with postgres_checkpointer(settings.database_url) as saver:
            graph = build_graph(Deps(llm=llm, jobs=jobs, recorder=recorder, settings=settings), saver)
            for case in cases:
                with sessions() as session:
                    row = SessionRow()
                    session.add(row)
                    session.commit()
                    session_id = str(row.id)
                log.info("case start case=%s session=%s", case.id, session_id)
                result = await run_case(
                    case,
                    graph,
                    session_id,
                    model=model,
                    simulate=not args.no_simulate,
                    stats=recorder,
                    judge=(llm, settings.llm_interpret_model) if args.judge else None,
                )
                log.info("case end case=%s outcome=%s", case.id, result.outcome)
                results.append(result)
                if case.id == "tutorial_house" and not args.no_simulate:
                    reference = await simulate_tutorial(jobs, session_id)
    finally:
        await redis.aclose()
        await llm.close()

    summary = summarize(results)
    stamp = started.strftime("%Y-%m-%d")
    header = {
        "date": started.isoformat(timespec="seconds"),
        "interactive model": model,
        "interpretation model": settings.llm_interpret_model,
        "cases": str(len(results)),
    }
    gate = next((r for r in results if r.id == "tutorial_house"), None)
    comparison = gate_comparison(gate, reference)
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"{stamp}-{model}"
    payload = {
        "header": header,
        "summary": summary.model_dump(),
        "gate": comparison,
        "cases": [r.model_dump() for r in results],
    }
    (args.output / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    text = markdown(summary, results, header)
    if comparison:
        text += "\n## Gate: tutorial house through the chat vs first_model.yaml\n\n" + "\n".join(
            f"- {key}: {value}" for key, value in comparison.items()
        )
    status = 0
    if args.gate is not None:
        gate_result = check_gate(summary, load_gate(args.gate))
        text += "\n" + gate_markdown(gate_result)
        status = 0 if gate_result.passed else 1
    (args.output / f"{name}.md").write_text(text)
    sys.stdout.write(text)
    return status


def gate_comparison(gate: CaseResult | None, reference: dict[str, Any] | None) -> dict[str, Any] | None:
    if gate is None or not gate.kpis or not reference:
        return None
    chat, direct = gate.kpis["heating_energy_kwh"], reference["heating_energy_kwh"]
    return {
        "heating through the chat (kWh)": round(chat),
        "heating of first_model.yaml (kWh)": round(direct),
        "difference": f"{100 * (chat / direct - 1):+.1f}%",
        "peak through the chat (W)": round(gate.kpis["peak_heating_w"]),
        "peak of first_model.yaml (W)": round(reference["peak_heating_w"]),
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
