"""The workflow state (Pydantic), checkpointed per session by LangGraph (``thread_id = session_id``).

The Brief in the state holds only what the user said; defaults are applied when the card is shown and when
the model is compiled, so a later answer can still replace them.
"""

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from llmtrano.brief.merge import Conflict
from llmtrano.brief.model import Fact, FieldProvenance, HouseBrief, Scenario
from llmtrano.brief.plan import Question
from llmtrano.compiler.compile import CompiledModel
from llmtrano.llm.reply import Language

RECENT_MESSAGES = 6


class Phase(StrEnum):
    intake = "intake"
    confirm = "confirm"
    compiling = "compiling"
    simulating = "simulating"
    done = "done"
    failed = "failed"


class ChatMessage(BaseModel):
    role: str
    content: str


class Answer(BaseModel):
    """A click on an answer button: the field and the Brief value, no LLM needed."""

    field: str
    value: str
    label: str


class GuardrailReport(BaseModel):
    passed_first_time: bool
    wrong_first: list[str] = Field(default_factory=list)
    wrong_retry: list[str] = Field(default_factory=list)
    fallback_used: bool = False


class WorkflowState(BaseModel):
    session_id: str
    phase: Phase = Phase.intake
    brief: HouseBrief = Field(default_factory=HouseBrief)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)
    renovation_facts: list[Fact] = Field(default_factory=list)
    pending_questions: list[Question] = Field(default_factory=list)
    recent_messages: list[ChatMessage] = Field(default_factory=list)  # the last RECENT_MESSAGES
    compiled: CompiledModel | None = None
    run_id: uuid.UUID | None = None
    check_run_id: uuid.UUID | None = None
    kpis: dict[str, Any] | None = None  # the baseline, with energy and heat-loss split added (phase 2)
    explanation: str | None = None
    # phase 2: the renovation scenario, simulated after the baseline when renovation facts exist
    scenario: Scenario = "baseline"  # the scenario being compiled, checked and simulated
    renovation_compiled: CompiledModel | None = None
    renovation_run_id: uuid.UUID | None = None
    renovation_kpis: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    # turn bookkeeping
    turn: int = 0
    language: Language = "English"
    model: str | None = None  # interactive model chosen in the ui; Settings.llm_default_model when None
    message: str | None = None  # the user message of this turn (input)
    answer: Answer | None = None  # or an answer button (input)
    turn_facts: list[Fact] = Field(default_factory=list)  # extracted this turn, before the merge
    edited_fields: list[str] = Field(default_factory=list)  # edited on the card: they override, no conflict
    noted: list[Fact] = Field(default_factory=list)  # facts applied this turn
    noted_renovation: list[Fact] = Field(default_factory=list)
    extraction_gave_up: bool = False
    user_says_complete: bool = False
    asked: dict[str, int] = Field(default_factory=dict)  # times each field was asked
    ready: bool = False
    errors: list[str] = Field(default_factory=list)  # readable check errors or the simulation error
    guardrail: GuardrailReport | None = None
