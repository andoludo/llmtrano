"""Latency and memory benchmark for the `llm` service (phase 0, step 0.5).

Run with ``uv run python -m llmtrano.bench``. For each model it:

1. unloads everything (llama-swap ``/unload``) and times the first request, which includes model load;
2. runs three prompts (about 50 tokens, about 500 tokens, a 2,000-token system prefix plus a short
   question), each ``cold`` and ``warm``, ``RUNS`` times, streaming, ``max_tokens`` 256, thinking off.

``cold`` puts a unique nonce at the very start of the prompt, so no cached prefix can be reused.
``warm`` repeats the exact prompt of the cold run just before it, so the whole prefix is cached (#16).

TTFT and decode tokens/s are measured on the client. llama-server's own ``timings`` (prompt tokens/s,
decode tokens/s, cached tokens) come from the final streamed chunk. Memory is sampled after each model:
``nvidia-smi`` VRAM, ``docker stats`` for the llm container, ``free -m`` for the host.

The markdown result is printed and inserted into docs/measurements.md under "Benchmark (step 0.5)".
"""

import argparse
import logging
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from llmtrano.settings import Settings

log = logging.getLogger("llmtrano.bench")

RUNS = 3
MAX_TOKENS = 256
NO_THINKING: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}
REPO_ROOT = Path(__file__).resolve().parents[2]
MEASUREMENTS = REPO_ROOT / "docs" / "measurements.md"
LLAMA_SWAP_CONFIG = REPO_ROOT / "config" / "llama-swap.yaml"
LLM_CONTAINER = "llmtrano-llm-1"
BENCH_HEADING = "## Benchmark (step 0.5)"

# 38 tokens per sentence with the Qwen tokenizer (measured); repeated to reach a target length.
# The chat template, nonce and question add about 45 tokens.
FILLER = (
    "A detached house in Belgium loses heat through its walls, roof, floor, windows and by air leakage, "
    "and the heating system must replace that heat to keep the rooms at the setpoint. "
)
QUESTION = (
    "Explain step by step, in detail, how insulation and airtightness reduce the annual heating demand."
)


@dataclass(frozen=True)
class Prompt:
    name: str
    system: str | None
    user: str


@dataclass(frozen=True)
class Sample:
    model: str
    prompt: str
    mode: str
    ttft_s: float
    decode_tps: float
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    server_prompt_tps: float
    server_decode_tps: float


@dataclass(frozen=True)
class Memory:
    vram_mib: int
    container_mem: str
    host_available_mib: int


def build_prompts() -> list[Prompt]:
    return [
        Prompt("short (~50 tok)", None, QUESTION),
        Prompt("medium (~500 tok)", None, FILLER * 12 + QUESTION),
        Prompt("2k system prefix", FILLER * 53, QUESTION),
    ]


def with_nonce(prompt: Prompt, nonce: str) -> Prompt:
    """Put a unique marker at the very start, so the cached prefix is zero tokens long."""
    if prompt.system is not None:
        return Prompt(prompt.name, f"[{nonce}] {prompt.system}", prompt.user)
    return Prompt(prompt.name, None, f"[{nonce}] {prompt.user}")


def messages_for(prompt: Prompt) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = []
    if prompt.system is not None:
        messages.append({"role": "system", "content": prompt.system})
    messages.append({"role": "user", "content": prompt.user})
    return messages


def run_once(client: OpenAI, model: str, prompt: Prompt, mode: str) -> Sample:
    start = time.perf_counter()
    first: float | None = None
    last = start
    usage: Any = None
    timings: dict[str, Any] = {}
    stream = client.chat.completions.create(
        model=model,
        messages=messages_for(prompt),
        max_tokens=MAX_TOKENS,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=NO_THINKING,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            now = time.perf_counter()
            if first is None:
                first = now
            last = now
        if chunk.usage is not None:
            usage = chunk.usage
        extra = chunk.model_extra or {}
        if "timings" in extra:
            timings = extra["timings"]
    if first is None or usage is None:
        raise RuntimeError(f"no content or usage streamed for model={model} prompt={prompt.name}")
    decode_tokens = usage.completion_tokens - 1
    decode_s = last - first
    return Sample(
        model=model,
        prompt=prompt.name,
        mode=mode,
        ttft_s=first - start,
        decode_tps=decode_tokens / decode_s if decode_s > 0 else 0.0,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=int(timings.get("cache_n", 0)),
        server_prompt_tps=float(timings.get("prompt_per_second", 0.0)),
        server_decode_tps=float(timings.get("predicted_per_second", 0.0)),
    )


def unload(base_url: str) -> None:
    root = base_url.removesuffix("/").removesuffix("/v1")
    with urllib.request.urlopen(f"{root}/unload", timeout=60) as response:
        response.read()


def load_time_s(client: OpenAI, model: str) -> float:
    start = time.perf_counter()
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Say OK."}],
        max_tokens=4,
        extra_body=NO_THINKING,
    )
    return time.perf_counter() - start


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def sample_memory() -> Memory:
    vram = _run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    container = _run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", LLM_CONTAINER])
    free = _run(["free", "-m"]).splitlines()
    available = int(free[1].split()[6]) if len(free) > 1 else -1
    return Memory(int(vram) if vram.isdigit() else -1, container.split(" / ")[0] or "?", available)


def model_flags(model: str) -> str:
    """The llama-server command for `model` from config/llama-swap.yaml, comments dropped, on one line."""
    lines = LLAMA_SWAP_CONFIG.read_text().splitlines()
    flags: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if line.startswith("  ") and not line.startswith("   ") and stripped.endswith(":"):
            inside = stripped[:-1] == model
            continue
        if inside and line.startswith("      ") and stripped and not stripped.startswith("#"):
            flags.append(stripped)
    return " ".join(f for f in flags if not f.startswith(("llama-server", "--host", "--model")))


def summarise(samples: list[Sample]) -> list[dict[str, str]]:
    rows = []
    keys = dict.fromkeys((s.model, s.prompt, s.mode) for s in samples)
    for model, prompt, mode in keys:
        group = [s for s in samples if (s.model, s.prompt, s.mode) == (model, prompt, mode)]
        rows.append(
            {
                "Model": model,
                "Prompt": prompt,
                "Mode": mode,
                "Prompt tok": str(group[0].prompt_tokens),
                "Cached tok": str(round(statistics.median(s.cached_tokens for s in group))),
                "TTFT s": f"{statistics.median(s.ttft_s for s in group):.2f}",
                "TTFT range s": f"{min(s.ttft_s for s in group):.2f}-{max(s.ttft_s for s in group):.2f}",
                "Decode tok/s": f"{statistics.median(s.decode_tps for s in group):.1f}",
                "Server prefill tok/s": f"{statistics.median(s.server_prompt_tps for s in group):.0f}",
                "Server decode tok/s": f"{statistics.median(s.server_decode_tps for s in group):.1f}",
                "Gen tok": str(round(statistics.median(s.completion_tokens for s in group))),
            }
        )
    return rows


def markdown_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    header = list(rows[0])
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(row[h] for h in header) + " |" for row in rows]
    return "\n".join(lines)


def report(
    rows: list[dict[str, str]], loads: dict[str, float], memory: dict[str, Memory], when: datetime
) -> str:
    parts = [f"### Run {when:%Y-%m-%d %H:%M}", ""]
    parts.append(
        f"Median of {RUNS} runs, streaming, max_tokens {MAX_TOKENS}, thinking off. "
        "Cold = unique nonce at prompt start (nothing cached); warm = same prompt again."
    )
    parts.append("")
    parts.append(
        "| Model | Flags | Load + first reply s | VRAM MiB | llm container mem | Host available MiB |"
    )
    parts.append("| --- | --- | --- | --- | --- | --- |")
    for model, mem in memory.items():
        parts.append(
            f"| {model} | `{model_flags(model)}` | {loads[model]:.1f} | {mem.vram_mib} | "
            f"{mem.container_mem} | {mem.host_available_mib} |"
        )
    parts += ["", markdown_table(rows), ""]
    return "\n".join(parts)


def insert_into_measurements(text: str, section: str) -> str:
    """Insert `section` at the end of the benchmark section, before the next `## ` heading."""
    start = text.index(BENCH_HEADING)
    next_heading = text.find("\n## ", start + len(BENCH_HEADING))
    if next_heading == -1:
        return text.rstrip("\n") + "\n\n" + section
    return text[:next_heading].rstrip("\n") + "\n\n" + section + text[next_heading:]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--models", nargs="+", default=[settings.llm_default_model, settings.llm_interpret_model]
    )
    parser.add_argument(
        "--no-write", action="store_true", help="print only, do not edit docs/measurements.md"
    )
    args = parser.parse_args()

    client = OpenAI(base_url=settings.llm_base_url, api_key="local", timeout=900)
    samples: list[Sample] = []
    loads: dict[str, float] = {}
    memory: dict[str, Memory] = {}
    for model in args.models:
        unload(settings.llm_base_url)
        loads[model] = load_time_s(client, model)
        log.info("loaded model=%s load_s=%.1f", model, loads[model])
        for prompt in build_prompts():
            for run in range(RUNS):
                cold_prompt = with_nonce(prompt, uuid.uuid4().hex[:12])
                for mode in ("cold", "warm"):
                    sample = run_once(client, model, cold_prompt, mode)
                    samples.append(sample)
                    log.info(
                        "sample model=%s prompt=%s mode=%s run=%d ttft_s=%.2f decode_tps=%.1f cached=%d",
                        model,
                        prompt.name,
                        mode,
                        run,
                        sample.ttft_s,
                        sample.decode_tps,
                        sample.cached_tokens,
                    )
        memory[model] = sample_memory()

    section = report(summarise(samples), loads, memory, datetime.now())
    sys.stdout.write(section + "\n")
    if not args.no_write:
        MEASUREMENTS.write_text(insert_into_measurements(MEASUREMENTS.read_text(), section))
        log.info("written path=%s", MEASUREMENTS)


if __name__ == "__main__":
    main()
