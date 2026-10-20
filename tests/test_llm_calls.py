"""Prompt files, prompt assembly and the three LLM calls (fake client)."""

import hashlib
import logging
import re
from importlib.resources import files
from typing import cast

import pytest
from fakes import FakeLLM, chunk
from openai import AsyncOpenAI

from llmtrano.brief.model import BRIEF_FIELDS
from llmtrano.llm import calls
from llmtrano.llm.prompts import FIELDS_SLOT, field_descriptions, load_prompt


@pytest.mark.parametrize("name", ["extract", "respond", "interpret"])
def test_prompt_version_is_the_file_hash(name: str) -> None:
    data = files("llmtrano.llm").joinpath("prompts", f"{name}.md").read_bytes()
    assert load_prompt(name).version == hashlib.sha256(data).hexdigest()[:8]  # type: ignore[arg-type]


def test_extract_prompt_order_rules_fields_examples() -> None:
    system = load_prompt("extract").system
    assert FIELDS_SLOT not in system
    rules, fields, examples = (
        system.index("# Rules"),
        system.index(field_descriptions()),
        system.index("# Examples"),
    )
    assert rules < fields < examples


def test_field_descriptions_cover_every_field_with_bounds() -> None:
    text = field_descriptions()
    assert [line.split(":")[0].removeprefix("- ") for line in text.splitlines()] == list(BRIEF_FIELDS)
    assert "- heated_floor_area_m2: number from 20 to 1000." in text
    assert "- glazing: one of single, double_old, double_low_e, triple, unknown." in text


def test_static_part_is_stable_and_dynamic_part_is_last() -> None:
    prompt = load_prompt("extract")
    first, second = prompt.messages("turn 1"), prompt.messages("turn 2")
    assert first[0] == second[0]  # byte-identical system message: llama.cpp reuses the cached prefix
    assert first[-1] == {"role": "user", "content": "turn 1"}


@pytest.mark.parametrize("name", ["extract", "respond", "interpret"])
def test_no_volatile_values_in_static_prompts(name: str) -> None:
    """No unfilled slot, date, time or session id in a system message (they would break prefix caching)."""
    system = load_prompt(name).system  # type: ignore[arg-type]
    assert "{{" not in system and "}}" not in system
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|session", system, re.IGNORECASE)


async def test_extract_sends_schema_and_sampling() -> None:
    llm = FakeLLM().queue("extract", '{"facts": [], "user_says_complete": false}')
    schema = {"type": "object", "properties": {}}
    text, stats = await calls.extract(cast(AsyncOpenAI, llm), "hello", model="m", response_schema=schema)
    assert text == '{"facts": [], "user_says_complete": false}'
    request = llm.calls("extract")[0]
    assert request["temperature"] == 0.1
    assert request["max_tokens"] == 600
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["schema"] == schema
    assert stats.prompt_version == load_prompt("extract").version
    assert stats.tokens_in is not None and stats.tokens_out is not None
    assert stats.ttft_ms is not None and stats.latency_ms is not None


async def test_respond_streams_with_non_thinking_sampling() -> None:
    llm = FakeLLM().queue("respond", "Thanks! How big is the house?")
    streamed = calls.respond(cast(AsyncOpenAI, llm), "dynamic", model="qwen3.5-9b")
    pieces = [piece async for piece in streamed]
    assert len(pieces) > 1
    assert streamed.text == "Thanks! How big is the house?"
    request = llm.calls("respond")[0]
    assert (request["temperature"], request["top_p"], request["presence_penalty"]) == (0.7, 0.8, 1.5)
    assert request["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}, "top_k": 20}
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}


async def test_interpret_allows_thinking_and_appends_retry_note() -> None:
    llm = FakeLLM().queue("interpret", "Your house needs 6000 kWh.")
    streamed = calls.interpret(cast(AsyncOpenAI, llm), "results", model="moe", retry_note="Fix: 6001")
    assert await streamed.collect() == "Your house needs 6000 kWh."
    request = llm.calls("interpret")[0]
    assert request["model"] == "moe"
    assert request["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert request["messages"][-1]["content"] == "results\n\nFix: 6001"


async def test_reasoning_is_counted_not_returned(caplog: pytest.LogCaptureFixture) -> None:
    class ThinkingLLM(FakeLLM):
        async def _create(self, **kwargs):  # type: ignore[no-untyped-def]
            self.requests.append({"prompt": "interpret", **kwargs})

            async def stream():  # type: ignore[no-untyped-def]
                yield chunk(reasoning="Let me think about this.")
                yield chunk("Answer.")
                yield chunk(finish="stop")

            return stream()

    caplog.set_level(logging.INFO, logger="llmtrano")
    streamed = calls.interpret(cast(AsyncOpenAI, ThinkingLLM()), "x", model="moe")
    assert await streamed.collect() == "Answer."
    assert streamed.stats.reasoning_chars == len("Let me think about this.")
    assert streamed.stats.finish_reason == "stop"
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("llm call=interpret"))
    for key in ("model=moe", "prompt_version=", "tokens_in=", "tokens_out=", "ttft_ms=", "latency_ms="):
        assert key in line
