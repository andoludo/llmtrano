"""SQLAlchemy 2 typed models.

Phase 0: ``runs``, one row per worker job. Phase 1 adds the conversation: ``sessions``, ``messages`` (with the
model, prompt version, tokens and latency of each LLM call), ``facts`` (every extracted fact, applied or not),
``house_briefs`` (each confirmed Brief with its provenance and the trano schema hash), ``kpis`` (the KPI JSON
of a simulation run) and ``feedback``. Only the municipality is stored, never a street address (GDPR
minimisation).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names, so Alembic autogenerate produces stable, reviewable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
# JSONB in Postgres, plain JSON in the SQLite unit tests.
Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(enum_type, name=name, values_callable=lambda e: [m.value for m in e])


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunKind(enum.StrEnum):
    CHECK = "check"
    SIMULATE = "simulate"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), default=RunStatus.QUEUED)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[RunKind] = mapped_column(
        _enum(RunKind, "run_kind"), default=RunKind.SIMULATE, server_default=RunKind.SIMULATE.value
    )
    library: Mapped[str] = mapped_column(String(64))
    start_s: Mapped[float] = mapped_column(Float, default=0.0)
    end_s: Mapped[float] = mapped_column(Float)
    yaml: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(String(32))  # trano's last on_phase: generating ... done
    result_path: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(Float)
    error_kind: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list[str] | None] = mapped_column(Json)  # readable validation errors, one per line (T8)
    log_tail: Mapped[str | None] = mapped_column(Text)
    trano_version: Mapped[str | None] = mapped_column(String(64))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user, assistant, extract
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)


class FactRow(Base):
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)  # the typed value as text
    raw_value: Mapped[str] = mapped_column(Text)  # what the model wrote
    scenario: Mapped[str] = mapped_column(String(16))  # baseline, renovation, unclear
    evidence: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(8))
    accepted: Mapped[bool] = mapped_column(Boolean)  # applied to the baseline Brief


class HouseBriefRow(Base):
    __tablename__ = "house_briefs"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_house_briefs_session_id_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    values: Mapped[dict[str, Any]] = mapped_column(Json)
    provenance: Mapped[dict[str, Any]] = mapped_column(Json)
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    trano_version: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KpiRow(Base):
    __tablename__ = "kpis"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    values: Mapped[dict[str, Any]] = mapped_column(Json)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    thumbs: Mapped[int] = mapped_column(SmallInteger)  # +1 up, -1 down
    comment: Mapped[str | None] = mapped_column(Text)
