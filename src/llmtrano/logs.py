"""Logging for long-running processes (api, worker), whose runners configure only their own loggers.

Messages are structured ``key=value`` text. ``LOG_FORMAT=json`` (phase 3) writes one JSON object per line
instead, with the session id and the run id of the current context, so a log shipper can index them. The
context ids are set with ``bind_context`` (the api sets the session id per request, the worker the run id
per job) and read by a logging filter; they travel with asyncio tasks and threads started from them.
"""

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, Literal

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LogFormat = Literal["text", "json"]
CONTEXT_KEYS = ("session_id", "run_id")

_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
_VARS = {"session_id": _session_id, "run_id": _run_id}


def bind_context(**ids: str | None) -> list[contextvars.Token[str | None]]:
    """Set the context ids for the current task; returns the tokens for ``reset_context``."""
    return [_VARS[key].set(value) for key, value in ids.items()]


def reset_context(tokens: list[contextvars.Token[str | None]]) -> None:
    for key, token in zip(CONTEXT_KEYS, tokens, strict=False):
        _VARS[key].reset(token)


def current_context() -> dict[str, str]:
    return {key: value for key, var in _VARS.items() if (value := var.get()) is not None}


class ContextFilter(logging.Filter):
    """Adds the context ids to every record (empty strings when unset, so text formats stay stable)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, var in _VARS.items():
            setattr(record, key, var.get() or "")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, "")
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def make_handler(fmt: LogFormat) -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(FORMAT))
    handler.addFilter(ContextFilter())
    return handler


def configure_logging(level: int = logging.INFO, fmt: LogFormat | None = None) -> None:
    """Give the `llmtrano` logger a stderr handler once; ``fmt`` defaults to the ``LOG_FORMAT`` variable."""
    logger = logging.getLogger("llmtrano")
    if not logger.handlers:
        chosen: LogFormat = fmt or (
            "json" if os.environ.get("LOG_FORMAT", "text").lower() == "json" else "text"
        )
        logger.addHandler(make_handler(chosen))
        logger.setLevel(level)
        logger.propagate = False
