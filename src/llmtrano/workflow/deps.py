"""What the workflow needs from the outside, as small protocols: jobs, persistence, the LLM and settings.

The graph is built with a ``Deps`` (``build_graph(deps)``), so tests pass fakes and the api passes the real
queue, database and LLM client.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel

from llmtrano.brief.model import Fact, HouseBrief, Provenance, Scenario
from llmtrano.compiler.compile import CompiledModel
from llmtrano.llm.calls import CallStats
from llmtrano.settings import Settings

PhaseCallback = Callable[[str], Awaitable[None]]


class CheckOutcome(BaseModel):
    run_id: uuid.UUID
    ok: bool
    errors: list[str] = []  # readable lines (trano T8)
    error_kind: str | None = None


class SimulationOutcome(BaseModel):
    run_id: uuid.UUID
    ok: bool
    kpis: dict[str, Any] | None = None
    error_kind: str | None = None
    error_message: str | None = None


class Jobs(Protocol):
    """The worker jobs, awaited: each call creates a ``runs`` row, queues the job and waits for its end."""

    async def check_model(
        self, session_id: str, compiled: CompiledModel, scenario: Scenario = "baseline"
    ) -> CheckOutcome: ...

    async def simulate(
        self,
        session_id: str,
        compiled: CompiledModel,
        on_phase: PhaseCallback,
        scenario: Scenario = "baseline",
    ) -> SimulationOutcome: ...


class Recorder(Protocol):
    """Persistence of the conversation (``messages``, ``facts`` and ``house_briefs`` tables)."""

    async def message(
        self, session_id: str, role: str, content: str, stats: CallStats | None = None
    ) -> uuid.UUID | None: ...

    async def facts(self, session_id: str, facts: list[Fact], accepted: bool) -> None: ...

    async def brief(
        self,
        session_id: str,
        brief: HouseBrief,
        provenance: Provenance,
        schema_hash: str | None,
        confirmed: bool,
        scenario: Scenario = "baseline",
    ) -> None: ...


class NullRecorder:
    async def message(
        self, session_id: str, role: str, content: str, stats: CallStats | None = None
    ) -> uuid.UUID | None:
        return None

    async def facts(self, session_id: str, facts: list[Fact], accepted: bool) -> None:
        return None

    async def brief(
        self,
        session_id: str,
        brief: HouseBrief,
        provenance: Provenance,
        schema_hash: str | None,
        confirmed: bool,
        scenario: Scenario = "baseline",
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Deps:
    llm: AsyncOpenAI
    jobs: Jobs
    recorder: Recorder
    settings: Settings
