"""Logs with context ids (text and JSON) and the Prometheus metrics (phase 3)."""

import json
import logging

from prometheus_client import REGISTRY

from llmtrano import metrics
from llmtrano.logs import JsonFormatter, bind_context, current_context, make_handler, reset_context


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("llmtrano.test", logging.INFO, __file__, 1, message, None, None)


def test_json_log_line_carries_the_context_ids() -> None:
    handler = make_handler("json")
    tokens = bind_context(session_id="s-1", run_id="r-9")
    try:
        record = _record("run end status=%s")
        record.args = ("succeeded",)
        assert all(f.filter(record) for f in handler.filters)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_context(tokens)
    assert payload["message"] == "run end status=succeeded"
    assert (payload["session_id"], payload["run_id"], payload["level"]) == ("s-1", "r-9", "INFO")
    assert payload["ts"].endswith("+00:00")
    assert current_context() == {}  # reset


def test_text_format_stays_the_phase_0_line() -> None:
    handler = make_handler("text")
    record = _record("hello")
    assert all(f.filter(record) for f in handler.filters)
    assert record.session_id == "" and record.run_id == ""  # type: ignore[attr-defined]
    assert handler.format(record).endswith(" INFO llmtrano.test hello")


def _sample(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return float(value or 0.0)


def test_llm_call_metrics() -> None:
    before = _sample("llmtrano_llm_tokens_total", {"call": "extract", "model": "m", "direction": "in"})
    metrics.observe_llm_call("extract", "m", latency_ms=1500, ttft_ms=200, tokens_in=900, tokens_out=40)
    assert (
        _sample("llmtrano_llm_tokens_total", {"call": "extract", "model": "m", "direction": "in"})
        == before + 900
    )
    assert _sample("llmtrano_llm_call_seconds_bucket", {"call": "extract", "model": "m", "le": "2.0"}) >= 1
    assert _sample("llmtrano_llm_ttft_seconds_bucket", {"call": "extract", "model": "m", "le": "0.25"}) >= 1


def test_timed_run_counts_the_status() -> None:
    before = _sample(
        "llmtrano_runs_total", {"kind": "simulate", "scenario": "baseline", "status": "succeeded"}
    )
    with metrics.timed_run("simulate", "baseline") as status:
        status.append("succeeded")
    assert (
        _sample("llmtrano_runs_total", {"kind": "simulate", "scenario": "baseline", "status": "succeeded"})
        == before + 1
    )
    failed = _sample("llmtrano_runs_total", {"kind": "check", "scenario": "renovation", "status": "failed"})
    with metrics.timed_run("check", "renovation"):
        pass  # no status appended: counted as failed
    assert (
        _sample("llmtrano_runs_total", {"kind": "check", "scenario": "renovation", "status": "failed"})
        == failed + 1
    )


def test_exposition_is_prometheus_text() -> None:
    body, content_type = metrics.exposition()
    assert content_type.startswith("text/plain")
    assert b"llmtrano_turns_total" in body and b"llmtrano_guardrail_total" in body
