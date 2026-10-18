"""Factory for the single OpenAI-compatible client. Every LLM call in llmtrano goes through it.

The endpoint is llama-swap (`LLM_BASE_URL`); the `model` field of each request picks the model.
Never import an engine-specific SDK.
"""

from typing import Any

from openai import AsyncOpenAI, Timeout

from llmtrano.settings import Settings

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 120.0
# One retry. The SDK retries connection errors and 5xx answers; llama-swap occasionally answers
# 502 "proxy error: EOF" (docs/decisions.md, step 0.5), which one retry absorbs.
MAX_RETRIES = 1
# Qwen3.5/3.6 think by default; interactive calls switch it off per request (docs/decisions.md, step 0.4).
NO_THINKING: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}


def make_llm_client(settings: Settings | None = None) -> AsyncOpenAI:
    settings = settings or Settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key="local",  # llama-swap needs no key; the SDK requires a non-empty one
        timeout=Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        max_retries=MAX_RETRIES,
    )
