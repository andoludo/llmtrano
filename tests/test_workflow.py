"""Workflow graph with a fake LLM and fake jobs: intake, rejection with an edit, validation error, a run."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fakes import FakeLLM
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from openai import AsyncOpenAI

from llmtrano.compiler.compile import CompiledModel
from llmtrano.settings import Settings
from llmtrano.workflow.deps import CheckOutcome, Deps, NullRecorder, PhaseCallback, SimulationOutcome
from llmtrano.workflow.graph import build_graph, make_serializer, workflow_doc
from llmtrano.workflow.state import Phase

KPIS = {
    "heated_floor_area_m2": 120.0,
    "heating_energy_kwh": 6469.2,
    "heating_energy_kwh_per_m2": 53.91,
    "peak_heating_w": 4335.0,
    "zones": [],
}


def facts(*items: tuple[str, str, str], complete: bool = False) -> str:
    return json.dumps(
        {
            "facts": [
                {"field": f, "value": v, "scenario": s, "evidence": v, "confidence": "high"}
                for f, v, s in items
            ],
            "user_says_complete": complete,
        }
    )


@dataclass
class FakeJobs:
    check_ok: bool = True
    simulate_ok: bool = True
    checked: list[CompiledModel] = field(default_factory=list)
    simulated: list[CompiledModel] = field(default_factory=list)

    scenarios: list[str] = field(default_factory=list)

    async def check_model(
        self, session_id: str, compiled: CompiledModel, scenario: str = "baseline"
    ) -> CheckOutcome:
        self.checked.append(compiled)
        errors = [] if self.check_ok else ["spaces[0].parameters.floor_area: must be >= 0 (Floor area [m2])"]
        return CheckOutcome(run_id=uuid.uuid4(), ok=self.check_ok, errors=errors)

    async def simulate(
        self, session_id: str, compiled: CompiledModel, on_phase: PhaseCallback, scenario: str = "baseline"
    ) -> SimulationOutcome:
        self.simulated.append(compiled)
        self.scenarios.append(scenario)
        for phase in ("generating", "compiling", "simulating", "done"):
            await on_phase(phase)
        if not self.simulate_ok:
            return SimulationOutcome(
                run_id=uuid.uuid4(), ok=False, error_kind="solver", error_message="diverged"
            )
        return SimulationOutcome(run_id=uuid.uuid4(), ok=True, kpis=KPIS)


@dataclass
class Harness:
    llm: FakeLLM
    jobs: FakeJobs
    saver: InMemorySaver
    config: dict[str, Any]

    def graph(self) -> Any:
        deps = Deps(
            llm=cast(AsyncOpenAI, self.llm), jobs=self.jobs, recorder=NullRecorder(), settings=Settings()
        )
        return build_graph(deps, self.saver)

    async def say(self, message: str) -> dict[str, Any]:
        return await self.graph().ainvoke({"session_id": "s1", "message": message}, self.config)

    async def resume(self, decision: dict[str, Any]) -> dict[str, Any]:
        return await self.graph().ainvoke(Command(resume=decision), self.config)

    async def state(self) -> Any:
        return (await self.graph().aget_state(self.config)).values


@pytest.fixture
def harness() -> Harness:
    return Harness(
        FakeLLM(), FakeJobs(), InMemorySaver(serde=make_serializer()), {"configurable": {"thread_id": "s1"}}
    )


async def _intake_to_confirm(h: Harness) -> dict[str, Any]:
    h.llm.queue(
        "extract",
        facts(
            ("building_type", "semi_detached", "baseline"),
            ("construction_period", "1978", "baseline"),
            ("heated_floor_area_m2", "120 m2", "baseline"),
            ("glazing", "triple", "renovation"),
        ),
        facts(("storeys", "2", "baseline"), complete=True),
    )
    h.llm.queue("respond", "Thanks. How many floors?", "All set: please check the card and confirm.")
    first = await h.say("Semi-detached from 1978, 120 m2; we want triple glazing next year.")
    assert first["phase"] == Phase.intake
    assert "__interrupt__" not in first
    return await h.say("Two floors, that's all.")


async def test_two_turn_intake_reaches_confirmation(harness: Harness) -> None:
    result = await _intake_to_confirm(harness)
    assert result["phase"] == Phase.confirm
    card = result["__interrupt__"][0].value["card"]
    rows = {row["field"]: row for row in card["rows"]}
    assert rows["storeys"]["value"] == "2" and rows["storeys"]["source"] == "user"
    assert rows["wall_insulation"]["source"] == "default"  # assumed from the period, shown on the card
    state = await harness.state()
    assert [f.field for f in state["renovation_facts"]] == ["glazing"]
    assert state["brief"].glazing is None  # renovation facts never touch the baseline
    assert state["turn"] == 2
    assert len(state["recent_messages"]) == 4
    first_reply = harness.llm.calls("respond")[0]["messages"][-1]["content"]
    assert "Renovation plans noted: glazing = Triple glazing" in first_reply
    assert "Questions to ask:" in first_reply
    assert harness.jobs.checked == []  # nothing runs before the confirmation


async def test_rejection_with_an_edit_goes_back_to_merge(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("respond", "Updated to 130 m2; please confirm again.")
    result = await harness.resume({"approved": False, "edits": {"heated_floor_area_m2": "130"}})
    state = await harness.state()
    assert state["brief"].heated_floor_area_m2 == 130.0  # an edit on the card replaces the value
    assert state["conflicts"] == []
    assert result["phase"] == Phase.confirm  # still ready: back to the card
    assert len(harness.llm.calls("extract")) == 2  # a card edit needs no extraction
    reply = harness.llm.calls("respond")[-1]["messages"][-1]["content"]
    assert "Noted this turn: heated floor area (m2) = 130" in reply


async def test_rejection_with_an_edit_of_a_default_is_applied(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("respond", "Noted, walls well insulated. Please confirm.")
    result = await harness.resume({"approved": False, "edits": {"wall_insulation": "good"}})
    assert result["phase"] == Phase.confirm  # still ready: back to the card
    state = await harness.state()
    assert state["brief"].wall_insulation.value == "good"
    assert state["provenance"]["wall_insulation"].evidence == "edited on the card: good"


async def test_validation_error_is_explained(harness: Harness) -> None:
    harness.jobs.check_ok = False
    await _intake_to_confirm(harness)
    result = await harness.resume({"approved": True})
    assert result["phase"] == Phase.failed
    assert result["errors"] == ["spaces[0].parameters.floor_area: must be >= 0 (Floor area [m2])"]
    assert harness.jobs.simulated == []
    assert "floor_area: must be >= 0" in result["recent_messages"][-1].content


async def test_successful_run_is_explained_with_grounded_numbers(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "The house needs 6,469 kWh a year, 53.9 kWh per m2, with a 4335 W peak.")
    result = await harness.resume({"approved": True})
    assert result["phase"] == Phase.done
    assert {key: result["kpis"][key] for key in KPIS} == KPIS
    assert set(result["kpis"]) == {*KPIS, "energy", "heat_loss_split", "sizing"}  # enriched by Python
    assert result["kpis"]["energy"]["cost_eur"] is None  # an ideal heater has no fuel
    # the intake mentioned triple glazing: the renovation scenario ran too (same fake KPIs, so no saving)
    assert result["renovation_kpis"] is not None
    assert result["comparison"]["heating_energy_kwh"]["saving"] == 0.0
    assert result["explanation"].startswith("The house needs 6,469 kWh")
    assert result["guardrail"].passed_first_time
    compiled = harness.jobs.simulated[0]
    assert compiled is not None and "HOUSE:001" in compiled.yaml_text
    interpret_request = harness.llm.calls("interpret")[0]
    assert interpret_request["model"] == Settings().llm_interpret_model


async def test_guardrail_retries_then_falls_back(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "It needs 7,777 kWh.", "It needs 9,999 kWh.")
    result = await harness.resume({"approved": True})
    report = result["guardrail"]
    assert (report.wrong_first, report.wrong_retry, report.fallback_used) == (["7,777"], ["9,999"], True)
    assert result["explanation"].startswith("The simulated house needs 6469 kWh")
    retry = harness.llm.calls("interpret")[1]["messages"][-1]["content"]
    assert "not in the Results or the House: 7,777" in retry


async def test_guardrail_retry_that_passes(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "It needs 7,777 kWh.", "It needs 6469 kWh.")
    result = await harness.resume({"approved": True})
    assert result["explanation"] == "It needs 6469 kWh."
    assert not result["guardrail"].passed_first_time and not result["guardrail"].fallback_used


async def test_empty_interpretation_counts_as_a_failure(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "", "It needs 6469 kWh.")
    result = await harness.resume({"approved": True})
    assert result["guardrail"].wrong_first == ["(no text)"]
    assert result["explanation"] == "It needs 6469 kWh."


async def test_simulation_failure_is_explained(harness: Harness) -> None:
    harness.jobs.simulate_ok = False
    await _intake_to_confirm(harness)
    result = await harness.resume({"approved": True})
    assert result["phase"] == Phase.failed
    assert result["errors"] == ["solver: diverged"]


async def test_answer_button_skips_the_llm(harness: Harness) -> None:
    harness.llm.queue("respond", "Thanks, detached. How big is it?")
    result = await harness.graph().ainvoke(
        {"session_id": "s1", "answer": {"field": "building_type", "value": "detached", "label": "Detached"}},
        harness.config,
    )
    assert result["brief"].building_type.value == "detached"
    assert harness.llm.calls("extract") == []


async def test_session_survives_a_restart(harness: Harness) -> None:
    """A new graph (as after an api restart) on the same checkpointer continues the session."""
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "It needs 6469 kWh.")
    restarted = Harness(harness.llm, harness.jobs, harness.saver, harness.config)
    result = await restarted.resume({"approved": True})
    assert result["phase"] == Phase.done


async def test_message_during_confirmation_reopens_the_intake(harness: Harness) -> None:
    await _intake_to_confirm(harness)
    harness.llm.queue("extract", facts(("occupants", "4", "baseline")))
    harness.llm.queue("respond", "Four people, noted. Please confirm.")
    result = await harness.resume({"approved": False, "message": "We are four people."})
    assert result["brief"].occupants == 4
    assert result["phase"] == Phase.confirm


def test_workflow_doc_is_up_to_date() -> None:
    from llmtrano.workflow.graph import WORKFLOW_DOC

    assert WORKFLOW_DOC.read_text() == workflow_doc(), "run `uv run python -m llmtrano.workflow.graph`"


# ---------- phase 2: the renovation scenario


RENOVATION_KPIS = {
    **KPIS,
    "heating_energy_kwh": 3100.0,
    "heating_energy_kwh_per_m2": 25.83,
    "peak_heating_w": 2500.0,
}


@dataclass
class ScenarioJobs(FakeJobs):
    """Fake jobs whose KPIs depend on the model: the renovated house (triple glazing) needs less."""

    async def simulate(
        self, session_id: str, compiled: CompiledModel, on_phase: PhaseCallback, scenario: str = "baseline"
    ) -> SimulationOutcome:
        outcome = await super().simulate(session_id, compiled, on_phase, scenario)
        if outcome.ok and "WINDOW_TRIPLE" in compiled.yaml_text:
            return SimulationOutcome(run_id=outcome.run_id, ok=True, kpis=RENOVATION_KPIS)
        return outcome


async def test_renovation_facts_give_a_second_scenario_and_a_comparison(harness: Harness) -> None:
    harness.jobs = ScenarioJobs()
    await _intake_to_confirm(harness)  # the user wants triple glazing next year
    harness.llm.queue("interpret", "Now 6,469 kWh, after the renovation 3,100 kWh: 52% less.")
    result = await harness.resume({"approved": True})
    assert result["phase"] == Phase.done
    assert [c.model_dict["glazings"][0]["id"] for c in harness.jobs.simulated] == [
        "WINDOW_DOUBLE_OLD:001",
        "WINDOW_TRIPLE:001",
    ]
    assert harness.jobs.scenarios == ["baseline", "renovation"]
    assert result["kpis"]["heating_energy_kwh"] == 6469.2
    assert result["renovation_kpis"]["heating_energy_kwh"] == 3100.0
    comparison = result["comparison"]
    assert comparison["heating_energy_kwh"] == {
        "baseline": 6469.2,
        "renovation": 3100.0,
        "saving": 3369.2,
        "saving_percent": 52,
    }
    assert result["guardrail"].passed_first_time  # 52 and 3,100 are known values: they come from Python
    assert result["scenario"] == "baseline"
    assert result["renovation_compiled"].yaml_text != result["compiled"].yaml_text


async def test_renovation_card_shows_the_change_before_the_run(harness: Harness) -> None:
    result = await _intake_to_confirm(harness)
    rows = {row["field"]: row for row in result["__interrupt__"][0].value["card"]["rows"]}
    assert rows["glazing"]["renovation"] == "Triple glazing"
    assert rows["glazing"]["renovation_evidence"] == "triple"
    assert result["__interrupt__"][0].value["card"]["renovation_fields"] == ["glazing"]


async def test_renovation_added_after_a_result_reruns_only_the_renovation(harness: Harness) -> None:
    harness.jobs = ScenarioJobs()
    harness.llm.queue(
        "extract",
        facts(
            ("building_type", "detached", "baseline"),
            ("construction_period", "1978", "baseline"),
            ("heated_floor_area_m2", "120 m2", "baseline"),
            ("storeys", "2", "baseline"),
            complete=True,
        ),
    )
    harness.llm.queue("respond", "Please confirm.", "Triple glazing noted, confirm to compare.")
    harness.llm.queue("interpret", "It needs 6469 kWh.", "After the renovation 3100 kWh, 3369 kWh less.")
    await harness.say("Detached, 1978, 120 m2, two floors, that's all.")
    first = await harness.resume({"approved": True})
    assert first["phase"] == Phase.done and len(harness.jobs.simulated) == 1
    harness.llm.queue("extract", facts(("glazing", "triple", "renovation")))
    second = await harness.say("What if I put in triple glazing?")
    assert second["phase"] == Phase.confirm  # confirmed before: ready again at once
    third = await harness.resume({"approved": True})
    assert len(harness.jobs.simulated) == 2  # the baseline was not simulated again
    assert third["comparison"]["heating_energy_kwh"]["saving"] == 3369.2
    assert third["kpis"]["heating_energy_kwh"] == 6469.2


async def test_baseline_change_after_a_result_reruns_both(harness: Harness) -> None:
    harness.jobs = ScenarioJobs()
    await _intake_to_confirm(harness)
    harness.llm.queue("interpret", "Baseline 6469 kWh, renovation 3100 kWh.", "Updated: 6469 and 3100 kWh.")
    await harness.resume({"approved": True})
    assert len(harness.jobs.simulated) == 2
    harness.llm.queue("extract", facts(("wall_insulation", "none", "baseline")))
    harness.llm.queue("respond", "Walls noted, please confirm again.")
    await harness.say("By the way the walls are not insulated.")
    result = await harness.resume({"approved": True})
    assert len(harness.jobs.simulated) == 4 and result["comparison"] is not None


async def test_interpretation_gets_references_and_may_quote_them(harness: Harness) -> None:
    harness.llm.queue(
        "extract",
        facts(
            ("building_type", "semi_detached", "baseline"),
            ("construction_period", "1965", "baseline"),
            ("heated_floor_area_m2", "110 m2", "baseline"),
            ("storeys", "2", "baseline"),
            complete=True,
        ),
    )
    harness.llm.queue("respond", "Please confirm.")
    harness.llm.queue(
        "interpret", "It needs 6469 kWh, 53.9 kWh/m2, less than trano's reference house at 130 kWh/m2."
    )
    await harness.say("Semi-detached from 1965, 110 m2, two floors, that's all.")
    result = await harness.resume({"approved": True})
    request = harness.llm.calls("interpret")[0]["messages"][-1]["content"]
    assert "References (published results" in request and "130 kWh/m2" in request
    assert result["guardrail"].passed_first_time  # 130 is a known value: it comes from the references
