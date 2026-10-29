"""The LLM calls: ``extract``, ``respond`` and ``interpret`` (phase 1) and ``judge`` (phase 3 evals).

Each has its own sampling settings, ``max_tokens``, thinking flag and model id (from ``Settings``). Every
call is streamed internally so time to first token is measured the same way for all three, and each logs one
line with the model, the prompt version, input and output tokens, TTFT and total latency.
"""

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from llmtrano.llm.client import NO_THINKING
from llmtrano.llm.prompts import Prompt, PromptName, load_prompt
from llmtrano.metrics import observe_llm_call

log = logging.getLogger(__name__)

THINKING: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": True}}


@dataclass(frozen=True, slots=True)
class Sampling:
    temperature: float
    max_tokens: int
    thinking: bool
    top_p: float | None = None
    top_k: int | None = None  # not an OpenAI parameter: sent in the body, llama-server reads it
    presence_penalty: float | None = None

    def request(self) -> dict[str, Any]:
        body = dict(THINKING if self.thinking else NO_THINKING)
        if self.top_k is not None:
            body["top_k"] = self.top_k
        options: dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "extra_body": body,
        }
        if self.top_p is not None:
            options["top_p"] = self.top_p
        if self.presence_penalty is not None:
            options["presence_penalty"] = self.presence_penalty
        return options


# Extraction: near-greedy, short, thinking off (step 1.6).
EXTRACT_SAMPLING = Sampling(temperature=0.1, max_tokens=600, thinking=False)
# Reply: Qwen's recommended non-thinking sampling (step 1.8).
RESPOND_SAMPLING = Sampling(
    temperature=0.7, max_tokens=300, thinking=False, top_p=0.8, top_k=20, presence_penalty=1.5
)
# Interpretation: thinking allowed, Qwen's recommended thinking sampling; the budget covers the reasoning.
INTERPRET_SAMPLING = Sampling(temperature=0.6, max_tokens=4096, thinking=True, top_p=0.95, top_k=20)
# Judge (evals): thinking allowed, near-greedy, a JSON answer of a few lines after the reasoning.
JUDGE_SAMPLING = Sampling(temperature=0.1, max_tokens=2048, thinking=True)


@dataclass(slots=True)
class CallStats:
    """What is stored with every assistant message (``messages`` table)."""

    call: PromptName
    model: str
    prompt_version: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    ttft_ms: float | None = None
    latency_ms: float | None = None
    reasoning_chars: int = 0
    finish_reason: str | None = None


@dataclass(slots=True)
class Streamed:
    """A streamed answer: iterate for the content pieces; ``text`` and ``stats`` are complete afterwards."""

    stats: CallStats
    _pieces: AsyncIterator[str]
    text: str = ""
    _parts: list[str] = field(default_factory=list)

    async def __aiter__(self) -> AsyncIterator[str]:
        async for piece in self._pieces:
            self._parts.append(piece)
            yield piece
        self.text = "".join(self._parts)

    async def collect(self) -> str:
        async for _ in self:
            pass
        return self.text


def _stream(
    llm: AsyncOpenAI,
    prompt: Prompt,
    dynamic: str,
    model: str,
    sampling: Sampling,
    response_format: dict[str, Any] | None = None,
) -> Streamed:
    stats = CallStats(call=prompt.name, model=model, prompt_version=prompt.version)
    messages: list[ChatCompletionMessageParam] = prompt.messages(dynamic)

    async def pieces() -> AsyncIterator[str]:
        started = time.perf_counter()
        options = sampling.request()
        if response_format is not None:
            options["response_format"] = response_format
        stream = await llm.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **options,
        )
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    stats.tokens_in, stats.tokens_out = (
                        chunk.usage.prompt_tokens,
                        chunk.usage.completion_tokens,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                stats.finish_reason = choice.finish_reason or stats.finish_reason
                reasoning = getattr(choice.delta, "reasoning_content", None)
                if reasoning:
                    stats.reasoning_chars += len(reasoning)
                if choice.delta.content:
                    if stats.ttft_ms is None:
                        stats.ttft_ms = (time.perf_counter() - started) * 1000
                    yield choice.delta.content
        finally:
            stats.latency_ms = (time.perf_counter() - started) * 1000
            observe_llm_call(
                stats.call,
                stats.model,
                latency_ms=stats.latency_ms,
                ttft_ms=stats.ttft_ms,
                tokens_in=stats.tokens_in,
                tokens_out=stats.tokens_out,
            )
            log.info(
                "llm call=%s model=%s prompt_version=%s tokens_in=%s tokens_out=%s ttft_ms=%s "
                "latency_ms=%.0f reasoning_chars=%d finish=%s",
                stats.call,
                stats.model,
                stats.prompt_version,
                stats.tokens_in,
                stats.tokens_out,
                f"{stats.ttft_ms:.0f}" if stats.ttft_ms is not None else None,
                stats.latency_ms,
                stats.reasoning_chars,
                stats.finish_reason,
            )

    return Streamed(stats=stats, _pieces=pieces())


async def extract(
    llm: AsyncOpenAI, dynamic: str, *, model: str, response_schema: dict[str, Any]
) -> tuple[str, CallStats]:
    """The extraction JSON text, constrained by ``response_schema`` (``response_format: json_schema``)."""
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "extraction", "strict": True, "schema": response_schema},
    }
    streamed = _stream(llm, load_prompt("extract"), dynamic, model, EXTRACT_SAMPLING, response_format)
    return await streamed.collect(), streamed.stats


def respond(llm: AsyncOpenAI, dynamic: str, *, model: str) -> Streamed:
    """The reply to the user, streamed."""
    return _stream(llm, load_prompt("respond"), dynamic, model, RESPOND_SAMPLING)


def interpret(llm: AsyncOpenAI, dynamic: str, *, model: str, retry_note: str | None = None) -> Streamed:
    """The explanation of the simulation results, streamed; ``retry_note`` lists numbers to correct."""
    if retry_note:
        dynamic = f"{dynamic}\n\n{retry_note}"
    return _stream(llm, load_prompt("interpret"), dynamic, model, INTERPRET_SAMPLING)


async def judge(
    llm: AsyncOpenAI, dynamic: str, *, model: str, response_schema: dict[str, Any]
) -> tuple[str, CallStats]:
    """The judge's JSON text, constrained by ``response_schema`` (evals only)."""
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "judge", "strict": True, "schema": response_schema},
    }
    streamed = _stream(llm, load_prompt("judge"), dynamic, model, JUDGE_SAMPLING, response_format)
    return await streamed.collect(), streamed.stats
