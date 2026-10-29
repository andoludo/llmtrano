"""Prometheus metrics of the api process (phase 3, #65 observability), served on ``GET /metrics``.

What a dashboard needs to see the assistant work: LLM calls (latency and tokens by call and model), the
number guardrail's outcomes, the turns of the workflow, and the runs the api waited for (by kind, scenario
and status, with their wall time). The worker has no HTTP server: its runs are counted here when the api
gets their outcome, and the ``runs`` table in Postgres holds the detail for Grafana's Postgres datasource.
Registered on the default registry; labels are closed sets (call names, run kinds), never user text.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

LLM_LATENCY = Histogram(
    "llmtrano_llm_call_seconds",
    "Wall time of an LLM call, first byte to last",
    labelnames=("call", "model"),
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 80, 160),
)
LLM_TTFT = Histogram(
    "llmtrano_llm_ttft_seconds",
    "Time to the first content token of an LLM call",
    labelnames=("call", "model"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)
LLM_TOKENS = Counter(
    "llmtrano_llm_tokens_total", "Prompt and completion tokens", labelnames=("call", "model", "direction")
)
GUARDRAIL = Counter(
    "llmtrano_guardrail_total",
    "Outcomes of the number guardrail per explanation",
    labelnames=("outcome",),  # passed, retried, fallback
)
TURNS = Counter("llmtrano_turns_total", "User turns handled by the workflow")
RUNS = Counter(
    "llmtrano_runs_total", "Worker runs the api waited for", labelnames=("kind", "scenario", "status")
)
RUN_SECONDS = Histogram(
    "llmtrano_run_seconds",
    "Wall time the api waited for a run",
    labelnames=("kind",),
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200),
)


def observe_llm_call(
    call: str,
    model: str,
    *,
    latency_ms: float | None,
    ttft_ms: float | None,
    tokens_in: int | None,
    tokens_out: int | None,
) -> None:
    if latency_ms is not None:
        LLM_LATENCY.labels(call, model).observe(latency_ms / 1000)
    if ttft_ms is not None:
        LLM_TTFT.labels(call, model).observe(ttft_ms / 1000)
    if tokens_in:
        LLM_TOKENS.labels(call, model, "in").inc(tokens_in)
    if tokens_out:
        LLM_TOKENS.labels(call, model, "out").inc(tokens_out)


@contextmanager
def timed_run(kind: str, scenario: str) -> Iterator[list[str]]:
    """Times a run and counts it with the status the caller appends (``succeeded`` or ``failed``)."""
    status: list[str] = []
    started = perf_counter()
    try:
        yield status
    finally:
        RUN_SECONDS.labels(kind).observe(perf_counter() - started)
        RUNS.labels(kind, scenario, status[0] if status else "failed").inc()


def exposition() -> tuple[bytes, str]:
    """The metrics in Prometheus text format and the content type to serve them with."""
    return generate_latest(), CONTENT_TYPE_LATEST
