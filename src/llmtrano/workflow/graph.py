"""The workflow as a LangGraph graph (#38, #39 workflow over agent, #40, #41 human in the loop).

Intake turn: ``ingest -> extract -> merge -> plan -> respond``, then END (wait for the next user message) or,
when the Brief is ready, ``confirm``: an ``interrupt()`` with the Brief card. Nothing is simulated before the
user approves it. Approved: ``compile -> check -> simulate -> kpis``, once for the baseline and, when
renovation facts exist, once more for the renovation scenario (``kpis -> compile``), then ``compare`` (Python
computes the differences) and ``interpret``. Rejected with card edits: ``merge -> plan -> respond``; with a
chat message: ``ingest -> extract -> ...``. After a result, a message with new renovation plans goes through
the intake again and only the renovation scenario is simulated; a change to the baseline reruns both.

Python decides every step; the LLM extracts, words the reply and explains the numbers. Reply tokens and run
progress go out through LangGraph's custom stream (``stream_mode="custom"``) as ``{"event": ..., ...}``.
The graph is exported as Mermaid into docs/workflow.md (``python -m llmtrano.workflow.graph``).
"""

import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from llmtrano.brief import model as brief_model
from llmtrano.brief.card import build_card
from llmtrano.brief.defaults import apply_defaults
from llmtrano.brief.merge import Conflict, merge_facts
from llmtrano.brief.model import Fact, FieldProvenance, HouseBrief
from llmtrano.brief.overlay import renovation_brief
from llmtrano.brief.parse import ParseError, parse_value
from llmtrano.brief.plan import Option, Question, next_questions, ready_to_confirm
from llmtrano.compiler.compile import BriefNotReadyError, CompiledModel, compile_brief
from llmtrano.guardrails.numbers import GuardrailResult, check_numbers, fallback_text, retry_note
from llmtrano.kpi.compare import compare as compare_scenarios
from llmtrano.kpi.energy import enrich
from llmtrano.llm import calls
from llmtrano.llm.extraction import extraction_input, run_extraction
from llmtrano.llm.reply import detect_language, interpret_input, respond_input
from llmtrano.workflow.deps import Deps
from llmtrano.workflow.state import (
    RECENT_MESSAGES,
    Answer,
    ChatMessage,
    GuardrailReport,
    Phase,
    WorkflowState,
)

log = logging.getLogger(__name__)

Update = dict[str, Any]
# Types the checkpointer may deserialize (LangGraph's msgpack allowlist): the state and what it holds.
STATE_TYPES: tuple[type, ...] = (
    WorkflowState,
    HouseBrief,
    FieldProvenance,
    Fact,
    Conflict,
    Question,
    Option,
    ChatMessage,
    Answer,
    GuardrailReport,
    CompiledModel,
    Phase,
    brief_model.Source,
    brief_model.BuildingType,
    brief_model.Period,
    brief_model.InsulationLevel,
    brief_model.Glazing,
    brief_model.Airtightness,
    brief_model.Orientation,
    brief_model.Ventilation,
    brief_model.HeatingSystem,
    brief_model.Zoning,
)


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)


def _emit(event: str, **data: Any) -> None:
    get_stream_writer()({"event": event, **data})


def _recent(state: WorkflowState, role: str, content: str) -> list[ChatMessage]:
    return [*state.recent_messages, ChatMessage(role=role, content=content)][-RECENT_MESSAGES:]


def answer_fact(answer: Answer, turn: int) -> Fact | None:
    """The fact of an answer button or a card edit: the value is a Brief value, parsed like an extraction."""
    try:
        value = parse_value(answer.field, answer.value)
    except ParseError as error:
        log.warning("answer rejected field=%s value=%r error=%s", answer.field, answer.value, error)
        return None
    if value is None:
        return None
    return Fact.model_validate(
        {
            "field": answer.field,
            "value": value,
            "raw_value": answer.value,
            "scenario": "baseline",
            "evidence": answer.label,
            "confidence": "high",
            "turn": turn,
        }
    )


def build_graph(
    deps: Deps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
    settings, recorder = deps.settings, deps.recorder

    def interactive_model(state: WorkflowState) -> str:
        return state.model or settings.llm_default_model

    # ---------- intake turn

    async def ingest(state: WorkflowState) -> Update:
        text = state.message or (state.answer.label if state.answer else "")
        await recorder.message(state.session_id, "user", text)
        language = detect_language(state.message, state.language) if state.message else state.language
        return {
            "turn": state.turn + 1,
            "language": language,
            "recent_messages": _recent(state, "user", text),
            "phase": Phase.intake,
            "noted": [],
            "noted_renovation": [],
            "turn_facts": [],
            "extraction_gave_up": False,
            "ready": False,
        }

    async def extract(state: WorkflowState) -> Update:
        if state.answer is not None:
            fact = answer_fact(state.answer, state.turn)
            return {"turn_facts": [fact] if fact else [], "answer": None}
        last_question = " ".join(q.prompt_seed for q in state.pending_questions) or None
        dynamic = extraction_input(
            brief=state.brief,
            provenance=state.provenance,
            last_question=last_question,
            message=state.message or "",
        )
        result = await run_extraction(deps.llm, dynamic, model=interactive_model(state), turn=state.turn)
        for stats in result.calls:
            await recorder.message(state.session_id, "extract", str(len(result.facts)), stats)
        return {
            "turn_facts": result.facts,
            "extraction_gave_up": result.gave_up,
            "user_says_complete": state.user_says_complete or result.user_says_complete,
            "message": None,
        }

    async def merge(state: WorkflowState) -> Update:
        result = merge_facts(
            state.brief, state.provenance, state.conflicts, state.turn_facts, overrides=state.edited_fields
        )
        await recorder.facts(state.session_id, result.applied, accepted=True)
        rejected = [f for f in state.turn_facts if f not in result.applied and f.scenario != "renovation"]
        await recorder.facts(state.session_id, rejected + result.renovation, accepted=False)
        return {
            "brief": result.brief,
            "provenance": result.provenance,
            "conflicts": result.conflicts,
            "renovation_facts": [*state.renovation_facts, *result.renovation],
            "noted": result.applied,
            "noted_renovation": result.renovation,
            "turn_facts": [],
            "edited_fields": [],
        }

    async def plan(state: WorkflowState) -> Update:
        ready = ready_to_confirm(
            state.brief,
            state.provenance,
            state.conflicts,
            user_says_complete=state.user_says_complete,
            confirmed_before=state.kpis is not None,
        )
        questions = (
            [] if ready else next_questions(state.brief, state.provenance, state.conflicts, state.asked)
        )
        asked = dict(state.asked)
        for question in questions:
            asked[question.field] = asked.get(question.field, 0) + 1
        return {"pending_questions": questions, "ready": ready, "asked": asked}

    async def respond(state: WorkflowState) -> Update:
        dynamic = respond_input(
            noted=state.noted,
            renovation=state.noted_renovation,
            questions=state.pending_questions,
            ready_to_confirm=state.ready,
            language=state.language,
            gave_up=state.extraction_gave_up,
        )
        streamed = calls.respond(deps.llm, dynamic, model=interactive_model(state))
        async for piece in streamed:
            _emit("token", text=piece, stream="reply")
        message_id = await recorder.message(state.session_id, "assistant", streamed.text, streamed.stats)
        _emit("message", id=str(message_id) if message_id else None, stream="reply")
        return {
            "recent_messages": _recent(state, "assistant", streamed.text),
            "phase": Phase.confirm if state.ready else Phase.intake,
        }

    def after_respond(state: WorkflowState) -> str:
        return "confirm" if state.ready else END

    # ---------- confirmation (human in the loop)

    async def confirm(state: WorkflowState) -> Command[Literal["compile", "ingest", "merge"]]:
        card = build_card(
            state.brief,
            state.provenance,
            state.conflicts,
            ready=True,
            renovation_facts=state.renovation_facts,
        )
        decision: dict[str, Any] = interrupt({"card": card.model_dump(mode="json")})
        if decision.get("approved"):
            return Command(goto="compile", update={"phase": Phase.compiling})
        if decision.get("message"):
            return Command(goto="ingest", update={"message": decision["message"], "answer": None})
        turn = state.turn + 1
        edits: dict[str, str] = decision.get("edits") or {}
        facts = [
            fact
            for name, value in edits.items()
            if (
                fact := answer_fact(
                    Answer(field=name, value=value, label=f"edited on the card: {value}"), turn
                )
            )
        ]
        return Command(
            goto="merge",
            update={
                "turn": turn,
                "turn_facts": facts,
                "edited_fields": [fact.field for fact in facts],
                "phase": Phase.intake,
                "ready": False,
                "noted": [],
            },
        )

    # ---------- compile, check, simulate, compare, explain

    def current(state: WorkflowState) -> CompiledModel:
        compiled = state.renovation_compiled if state.scenario == "renovation" else state.compiled
        assert compiled is not None
        return compiled

    async def compile_(state: WorkflowState) -> Command[Literal["check", "explain_error", "compare"]]:
        """Compile what still needs a run: the baseline when it changed, else the renovation scenario."""
        try:
            baseline = compile_brief(state.brief, state.provenance)
        except BriefNotReadyError as error:
            return Command(goto="explain_error", update={"errors": [str(error)]})
        baseline_changed = (
            state.compiled is None or state.compiled.yaml_text != baseline.yaml_text or state.kpis is None
        )
        if baseline_changed:
            filled, provenance = apply_defaults(state.brief, state.provenance)
            await recorder.brief(state.session_id, filled, provenance, baseline.schema_hash, confirmed=True)
            _emit("run_phase", phase="compiled", kind="check", scenario="baseline")
            return Command(
                goto="check",
                update={
                    "compiled": baseline,
                    "scenario": "baseline",
                    "phase": Phase.compiling,
                    "kpis": None,
                    "run_id": None,
                    "renovation_compiled": None,
                    "renovation_run_id": None,
                    "renovation_kpis": None,
                    "comparison": None,
                },
            )
        overlay = renovation_brief(state.brief, state.provenance, state.renovation_facts)
        if overlay is None:
            return Command(goto="compare", update={"scenario": "baseline"})
        try:
            renovation = compile_brief(*overlay)
        except BriefNotReadyError as error:
            return Command(goto="explain_error", update={"errors": [f"renovation: {error}"]})
        same = (
            state.renovation_compiled is not None
            and state.renovation_compiled.yaml_text == renovation.yaml_text
        )
        if same and state.renovation_kpis is not None:
            return Command(goto="compare", update={"scenario": "baseline"})
        filled, provenance = apply_defaults(*overlay)
        await recorder.brief(state.session_id, filled, provenance, renovation.schema_hash, confirmed=True)
        _emit("run_phase", phase="compiled", kind="check", scenario="renovation")
        return Command(
            goto="check",
            update={
                "renovation_compiled": renovation,
                "scenario": "renovation",
                "phase": Phase.compiling,
                "renovation_run_id": None,
                "renovation_kpis": None,
                "comparison": None,
            },
        )

    async def check(state: WorkflowState) -> Command[Literal["simulate", "explain_error"]]:
        _emit("run_phase", phase="checking", kind="check", scenario=state.scenario)
        outcome = await deps.jobs.check_model(state.session_id, current(state))
        if not outcome.ok:
            errors = outcome.errors or [f"the model check failed ({outcome.error_kind})"]
            return Command(goto="explain_error", update={"check_run_id": outcome.run_id, "errors": errors})
        return Command(goto="simulate", update={"check_run_id": outcome.run_id, "phase": Phase.simulating})

    async def simulate(state: WorkflowState) -> Command[Literal["kpis", "explain_error"]]:
        scenario = state.scenario

        async def on_phase(phase: str) -> None:
            _emit("run_phase", phase=phase, kind="simulate", scenario=scenario)

        outcome = await deps.jobs.simulate(state.session_id, current(state), on_phase)
        run_key = "renovation_run_id" if scenario == "renovation" else "run_id"
        if not outcome.ok:
            reason = (
                f"{outcome.error_kind}: {outcome.error_message}" if outcome.error_kind else "unknown error"
            )
            return Command(goto="explain_error", update={run_key: outcome.run_id, "errors": [reason]})
        required = ("heating_energy_kwh", "heating_energy_kwh_per_m2", "peak_heating_w")
        if not outcome.kpis or any(outcome.kpis.get(key) is None for key in required):
            return Command(
                goto="explain_error", update={run_key: outcome.run_id, "errors": ["no KPI results"]}
            )
        kpis = enrich(outcome.kpis, current(state).sizing, settings)
        kpi_key = "renovation_kpis" if scenario == "renovation" else "kpis"
        return Command(goto="kpis", update={run_key: outcome.run_id, kpi_key: kpis})

    async def kpis(state: WorkflowState) -> Command[Literal["compile", "compare"]]:
        """Show the scenario's results, then run the renovation scenario if it is still due."""
        done = state.renovation_kpis if state.scenario == "renovation" else state.kpis
        _emit("kpis", kpis=done, scenario=state.scenario)
        renovation_due = (
            state.scenario == "baseline"
            and state.renovation_kpis is None
            and renovation_brief(state.brief, state.provenance, state.renovation_facts) is not None
        )
        return Command(goto="compile" if renovation_due else "compare")

    async def compare(state: WorkflowState) -> Update:
        if state.kpis is None or state.renovation_kpis is None:
            return {"comparison": None, "scenario": "baseline"}
        comparison = compare_scenarios(state.kpis, state.renovation_kpis)
        _emit("comparison", comparison=comparison)
        return {"comparison": comparison, "scenario": "baseline"}

    async def interpret(state: WorkflowState) -> Update:
        assert state.kpis is not None and state.compiled is not None
        filled, provenance = apply_defaults(state.brief, state.provenance)
        dynamic = interpret_input(
            kpis=state.kpis,
            brief=filled,
            provenance=provenance,
            assumptions=state.compiled.assumptions,
            language=state.language,
        )
        brief_values: dict[str, Any] = {
            **filled.model_dump(mode="json"),
            "assumptions": state.compiled.assumptions,
            "renovation": state.renovation_kpis,
            "comparison": state.comparison,
        }
        report = GuardrailReport(passed_first_time=False)
        text = ""
        for attempt in (1, 2):
            note = retry_note(report.wrong_first) if attempt == 2 else None
            streamed = calls.interpret(deps.llm, dynamic, model=settings.llm_interpret_model, retry_note=note)
            async for piece in streamed:
                _emit("token", text=piece, stream="interpretation", attempt=attempt)
            message_id = await recorder.message(state.session_id, "assistant", streamed.text, streamed.stats)
            result = check_numbers(streamed.text, state.kpis, brief_values)
            if not streamed.text.strip():  # e.g. the thinking used the whole token budget
                result = GuardrailResult(passed=False, wrong=["(no text)"])
            if result.passed:
                text = streamed.text
                report.passed_first_time = attempt == 1
                break
            if attempt == 1:
                report.wrong_first = result.wrong
                _emit("guardrail", action="retry", wrong=result.wrong)
            else:
                report.wrong_retry = result.wrong
                report.fallback_used = True
                text = fallback_text(state.kpis)
                _emit("guardrail", action="fallback", wrong=result.wrong, text=text)
                log.warning("guardrail fallback session=%s wrong=%r", state.session_id, result.wrong)
                message_id = await recorder.message(state.session_id, "assistant", text)
        _emit("explanation", text=text, message_id=str(message_id) if message_id else None)
        return {
            "explanation": text,
            "guardrail": report,
            "phase": Phase.done,
            "recent_messages": _recent(state, "assistant", text),
            "ready": False,
            "user_says_complete": False,
        }

    async def explain_error(state: WorkflowState) -> Update:
        text = "The simulation could not be run. " + _error_text(state.errors)
        _emit("error", text=text, errors=state.errors)
        await recorder.message(state.session_id, "assistant", text)
        return {"phase": Phase.failed, "recent_messages": _recent(state, "assistant", text), "ready": False}

    graph = StateGraph(WorkflowState)
    graph.add_node("ingest", ingest)
    graph.add_node("extract", extract)
    graph.add_node("merge", merge)
    graph.add_node("plan", plan)
    graph.add_node("respond", respond)
    graph.add_node("confirm", confirm, destinations=("compile", "ingest", "merge"))
    graph.add_node("compile", compile_, destinations=("check", "explain_error", "compare"))
    graph.add_node("check", check, destinations=("simulate", "explain_error"))
    graph.add_node("simulate", simulate, destinations=("kpis", "explain_error"))
    graph.add_node("kpis", kpis, destinations=("compile", "compare"))
    graph.add_node("compare", compare)
    graph.add_node("interpret", interpret)
    graph.add_node("explain_error", explain_error)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "extract")
    graph.add_edge("extract", "merge")
    graph.add_edge("merge", "plan")
    graph.add_edge("plan", "respond")
    graph.add_conditional_edges("respond", after_respond, {"confirm": "confirm", END: END})
    graph.add_edge("compare", "interpret")
    graph.add_edge("interpret", END)
    graph.add_edge("explain_error", END)
    return graph.compile(checkpointer=checkpointer)


def _error_text(errors: Sequence[str]) -> str:
    if not errors:
        return "No reason was given."
    shown = "; ".join(errors[:5])
    return f"Reason: {shown}" + (f" (and {len(errors) - 5} more)" if len(errors) > 5 else "")


def mermaid() -> str:
    """The graph as Mermaid text; the dependencies are not used to draw it."""
    from llmtrano.settings import Settings
    from llmtrano.workflow.deps import NullRecorder

    deps = Deps(llm=None, jobs=None, recorder=NullRecorder(), settings=Settings())  # type: ignore[arg-type]
    return build_graph(deps).get_graph().draw_mermaid()


WORKFLOW_DOC = Path(__file__).parents[3] / "docs" / "workflow.md"
DOC_HEADER = """# Workflow

The LangGraph graph (`llmtrano.workflow.graph`), exported with `graph.get_graph().draw_mermaid()`.
Regenerate after every change with `uv run python -m llmtrano.workflow.graph`; `tests/test_workflow.py` fails
when this file is stale.

"""


def workflow_doc() -> str:
    return f"{DOC_HEADER}```mermaid\n{mermaid().strip()}\n```\n"


if __name__ == "__main__":
    WORKFLOW_DOC.write_text(workflow_doc())
    sys.stdout.write(f"written {WORKFLOW_DOC}\n")
