"""Retention: delete what is older than ``RETENTION_DAYS`` and the run directories nobody refers to (phase 3).

Runs daily in the worker (an arq cron job) and after an erasure request from the api. Sessions older than the
retention period go with everything that hangs off them (messages, facts, Briefs, feedback by cascade; runs
and their KPIs explicitly, since a run keeps its row when its session is deleted); then every directory under
``RUNS_DIR`` whose run row is gone is removed, so an erased session leaves no result file behind.
"""

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from llmtrano.db.models import FactRow, Feedback, HouseBriefRow, KpiRow, Message, Run
from llmtrano.db.models import Session as SessionRow
from llmtrano.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sweep:
    sessions_deleted: int
    runs_deleted: int
    directories_removed: int


def _run_ids(session: Session) -> set[str]:
    return {str(run_id) for run_id in session.scalars(select(Run.id))}


def _delete_sessions(session: Session, session_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Delete sessions and every row that refers to them, explicitly (an erasure does not rely on cascades).

    Returns the ids of the deleted runs, for their directories.
    """
    if not session_ids:
        return []
    run_ids = list(session.scalars(select(Run.id).where(Run.session_id.in_(session_ids))))
    message_ids = list(session.scalars(select(Message.id).where(Message.session_id.in_(session_ids))))
    if message_ids:
        session.execute(delete(Feedback).where(Feedback.message_id.in_(message_ids)))
        session.execute(delete(Message).where(Message.id.in_(message_ids)))
    session.execute(delete(FactRow).where(FactRow.session_id.in_(session_ids)))
    session.execute(delete(HouseBriefRow).where(HouseBriefRow.session_id.in_(session_ids)))
    _delete_runs(session, run_ids)
    session.execute(delete(SessionRow).where(SessionRow.id.in_(session_ids)))
    return run_ids


def _delete_runs(session: Session, run_ids: list[uuid.UUID]) -> None:
    if run_ids:
        session.execute(delete(KpiRow).where(KpiRow.run_id.in_(run_ids)))
        session.execute(delete(Run).where(Run.id.in_(run_ids)))


def erase_session(sessions: sessionmaker[Session], session_id: uuid.UUID) -> list[uuid.UUID]:
    """Delete a session and everything stored about it; returns the run ids for the directories."""
    with sessions() as session:
        run_ids = _delete_sessions(session, [session_id])
        session.commit()
    log.info("session erased session_id=%s runs=%d", session_id, len(run_ids))
    return run_ids


def remove_run_directories(runs_dir: Path, run_ids: list[uuid.UUID]) -> int:
    removed = 0
    for run_id in run_ids:
        directory = runs_dir / str(run_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    return removed


def sweep(sessions: sessionmaker[Session], settings: Settings, now: datetime | None = None) -> Sweep:
    """Apply the retention period and remove the run directories without a row."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=settings.retention_days)
    with sessions() as session:
        old_sessions = list(session.scalars(select(SessionRow.id).where(SessionRow.created_at < cutoff)))
        old_runs = _delete_sessions(session, old_sessions)
        stray_runs = list(session.scalars(select(Run.id).where(Run.created_at < cutoff)))  # sessionless
        _delete_runs(session, stray_runs)
        old_runs += stray_runs
        session.commit()
        kept = _run_ids(session)
    removed = 0
    runs_dir = Path(settings.runs_dir)
    if runs_dir.is_dir():
        for directory in runs_dir.iterdir():
            if directory.is_dir() and directory.name not in kept and _is_uuid(directory.name):
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
    result = Sweep(
        sessions_deleted=len(old_sessions), runs_deleted=len(old_runs), directories_removed=removed
    )
    log.info(
        "retention sweep cutoff=%s sessions=%d runs=%d directories=%d",
        cutoff.date(),
        result.sessions_deleted,
        result.runs_deleted,
        result.directories_removed,
    )
    return result


def _is_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


async def retention_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    """The arq cron job (daily) and the job the api enqueues after an erasure."""
    import asyncio

    result = await asyncio.to_thread(sweep, ctx["sessions"], ctx["settings"])
    return result.__dict__
