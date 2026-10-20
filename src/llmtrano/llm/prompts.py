"""Prompt files and prompt assembly.

Prompts are Markdown files in ``llmtrano/llm/prompts/``; ``prompt_version`` is the first 8 hex characters of
the sha256 of the file and is stored with every message. Assembly order is fixed, static first, so the system
message is byte-identical across turns and llama.cpp reuses its cached prefix (#16):

1. the file's rules,
2. the field descriptions, generated from ``HouseBrief.model_json_schema()`` (the ``{{fields}}`` slot),
3. the file's few-shot examples,

then the dynamic part (state summary, last user message) as the final user message. Never put a timestamp or a
session id in the static part.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Literal

from openai.types.chat import ChatCompletionMessageParam

from llmtrano.brief.model import HouseBrief

PromptName = Literal["extract", "respond", "interpret"]
FIELDS_SLOT = "{{fields}}"


@dataclass(frozen=True, slots=True)
class Prompt:
    name: PromptName
    text: str  # the file as written
    version: str  # sha256 of the file, 8 hex characters

    @property
    def system(self) -> str:
        """The static system message: the file with the field descriptions in its slot."""
        return self.text.replace(FIELDS_SLOT, field_descriptions()) if FIELDS_SLOT in self.text else self.text

    def messages(self, dynamic: str) -> list[ChatCompletionMessageParam]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": dynamic}]


@cache
def load_prompt(name: PromptName) -> Prompt:
    data = files("llmtrano.llm").joinpath("prompts", f"{name}.md").read_bytes()
    return Prompt(name=name, text=data.decode(), version=hashlib.sha256(data).hexdigest()[:8])


@cache
def field_descriptions() -> str:
    """One line per Brief field: name, allowed values or range, description. Deterministic (field order)."""
    schema = HouseBrief.model_json_schema()
    definitions = schema.get("$defs", {})
    lines = []
    for name, spec in schema["properties"].items():
        lines.append(f"- {name}: {_allowed(spec, definitions)}. {spec.get('description', '')}".rstrip())
    return "\n".join(lines)


def _allowed(spec: dict[str, Any], definitions: dict[str, Any]) -> str:
    """'one of a, b, c', 'number from 20 to 1000', 'text' from a field's JSON Schema (``anyOf`` with null)."""
    branch = next((b for b in spec.get("anyOf", [spec]) if b.get("type") != "null"), spec)
    if "$ref" in branch:
        branch = definitions[branch["$ref"].rsplit("/", 1)[-1]]
    if "enum" in branch:
        return "one of " + ", ".join(str(value) for value in branch["enum"])
    kind = {"number": "number", "integer": "whole number", "string": "text"}.get(
        branch.get("type", ""), "value"
    )
    if "minimum" in branch and "maximum" in branch:
        return f"{kind} from {branch['minimum']:g} to {branch['maximum']:g}"
    return kind
