"""The workflow's ``Recorder`` on the database: messages, facts and confirmed Briefs, per session.

Synchronous SQLAlchemy in a thread, like the rest of llmtrano's database code. Only the municipality is part
of a Brief; no street address is ever stored (GDPR minimisation).
"""

import asyncio
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from llmtrano.brief.model import Fact, HouseBrief, Provenance
from llmtrano.db.models import FactRow, HouseBriefRow, Message
from llmtrano.llm.calls import CallStats
from llmtrano.trano_info import trano_version


def _text(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


class DbRecorder:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    async def message(
        self, session_id: str, role: str, content: str, stats: CallStats | None = None
    ) -> uuid.UUID | None:
        def insert() -> uuid.UUID:
            with self.sessions() as session:
                row = Message(session_id=uuid.UUID(session_id), role=role, content=content)
                if stats is not None:
                    row.model, row.prompt_version = stats.model, stats.prompt_version
                    row.tokens_in, row.tokens_out = stats.tokens_in, stats.tokens_out
                    row.ttft_ms, row.latency_ms = stats.ttft_ms, stats.latency_ms
                session.add(row)
                session.commit()
                return row.id

        return await asyncio.to_thread(insert)

    async def facts(self, session_id: str, facts: list[Fact], accepted: bool) -> None:
        if not facts:
            return

        def insert() -> None:
            with self.sessions() as session:
                session.add_all(
                    FactRow(
                        session_id=uuid.UUID(session_id),
                        turn=fact.turn,
                        field=fact.field,
                        value=_text(fact.value),
                        raw_value=fact.raw_value,
                        scenario=fact.scenario,
                        evidence=fact.evidence,
                        confidence=fact.confidence,
                        accepted=accepted,
                    )
                    for fact in facts
                )
                session.commit()

        await asyncio.to_thread(insert)

    async def brief(
        self,
        session_id: str,
        brief: HouseBrief,
        provenance: Provenance,
        schema_hash: str | None,
        confirmed: bool,
    ) -> None:
        def insert() -> None:
            with self.sessions() as session:
                sid = uuid.UUID(session_id)
                last = session.scalar(
                    select(func.max(HouseBriefRow.version)).where(HouseBriefRow.session_id == sid)
                )
                session.add(
                    HouseBriefRow(
                        session_id=sid,
                        version=(last or 0) + 1,
                        values=brief.model_dump(mode="json"),
                        provenance={name: item.model_dump(mode="json") for name, item in provenance.items()},
                        schema_hash=schema_hash,
                        trano_version=trano_version(),
                        confirmed_at=datetime.now(UTC) if confirmed else None,
                    )
                )
                session.commit()

        await asyncio.to_thread(insert)
