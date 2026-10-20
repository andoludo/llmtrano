"""Test doubles shared by the LLM, workflow and api tests."""

from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterable
from types import SimpleNamespace
from typing import Any

from llmtrano.llm.prompts import PromptName, load_prompt

PROMPTS: tuple[PromptName, ...] = ("extract", "respond", "interpret")


def chunk(
    content: str | None = None, *, reasoning: str | None = None, finish: str | None = None
) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def usage_chunk(prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[], usage=usage)


class FakeLLM:
    """Mimics ``AsyncOpenAI.chat.completions.create(stream=True)`` and ``models.list()``.

    Answers are queued per prompt (recognised by the system message) with ``queue(name, *texts)``; each text
    streams back in pieces of 7 characters, followed by a usage chunk. Every request is recorded.
    """

    def __init__(self, models: Iterable[str] = ("qwen3.5-9b", "qwen3.6-35b-a3b"), up: bool = True) -> None:
        self.answers: dict[str, deque[str]] = defaultdict(deque)
        self.requests: list[dict[str, Any]] = []
        self.up = up
        self.model_ids = list(models)
        self.models = SimpleNamespace(list=self._list)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def queue(self, name: PromptName, *texts: str) -> "FakeLLM":
        self.answers[name].extend(texts)
        return self

    def calls(self, name: PromptName) -> list[dict[str, Any]]:
        return [request for request in self.requests if request["prompt"] == name]

    async def _list(self) -> SimpleNamespace:
        if not self.up:
            raise ConnectionError("llm down")
        return SimpleNamespace(data=[SimpleNamespace(id=model) for model in self.model_ids])

    async def _create(self, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        if not self.up:
            raise ConnectionError("llm down")
        system = kwargs["messages"][0]["content"]
        name = next((n for n in PROMPTS if load_prompt(n).system == system), "unknown")
        self.requests.append({"prompt": name, **kwargs})
        if not self.answers[name]:
            raise AssertionError(f"no answer queued for the {name} prompt")
        text = self.answers[name].popleft()

        async def stream() -> AsyncIterator[SimpleNamespace]:
            for start in range(0, len(text), 7):
                yield chunk(text[start : start + 7])
            yield chunk(finish="stop")
            yield usage_chunk(len(system) // 4, len(text) // 4)

        return stream()

    async def close(self) -> None:
        return None
