"""Run progress on Valkey pub/sub: the worker publishes ``{"run_id", "phase", "t"}`` on ``run:<id>``.

``t`` is the time since the job started [s]. The api relays the channel as SSE (``GET /runs/{id}/events``) and
the workflow forwards it to the chat. The last phase is also written on the ``runs`` row, so a client that
subscribes late still sees where the run is.
"""

import json
import uuid
from typing import Protocol

TERMINAL_PHASES = frozenset({"succeeded", "failed"})


def channel(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


def progress_message(run_id: uuid.UUID, phase: str, t: float) -> str:
    return json.dumps({"run_id": str(run_id), "phase": phase, "t": round(t, 1)})


class Publisher(Protocol):
    def publish(self, channel: str, message: str) -> object: ...
