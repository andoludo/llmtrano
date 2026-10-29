# llmtrano: instructions for Claude Code

This file is loaded in every Claude Code session in this repo. Keep it short and true.
Detailed task lists live in `docs/phase-0.md` to `docs/phase-3.md` and `docs/trano-changes.md`.

## What we are building

A local-first LLM assistant that turns a plain-language house description into a trano
building model, simulates it with OpenModelica, and explains the result. Phase 0 and 1 built
the **happy path**: one house, one zone, ideal heating, one annual simulation, one
explanation. Phase 2 adds ventilation, day and night zones, hydronic heating systems, the
renovation overlay, a heat-loss split, costs and CO2. Phase 3 adds the eval set at scale, an
LLM judge, grounded references, observability and hardening. Anything beyond the phase files
(user-named rooms, domestic hot water, PV, multi-user accounts) is not started here.

The owner (Ando) maintains trano and uses this project to learn LLM engineering. So:

- Explain each step in 3 to 6 sentences before you code it: what it does, why the design
  needs it, and which flashcard concepts it uses (`#22` = concept 22 in the LLM Flow
  Flashcards). Then build it.
- Stop at every **CHECKPOINT** in the phase files, show the result (command output,
  numbers, screenshots), and wait for Ando before continuing.
- Prefer clear, small, typed code over clever code. No speculative abstractions.

## Architecture rules (do not break)

1. The LLM never writes trano YAML. It fills a `HouseBrief`; `llmtrano.compiler` writes YAML.
2. Every extraction call uses a JSON Schema (`response_format: json_schema`) and is
   validated again with Pydantic afterwards.
3. Python, not the LLM, decides the next question, compiles, validates, simulates and
   computes KPIs. The LLM extracts, words questions and narrates numbers it is given.
4. Nothing is simulated before the user confirms the House Brief (a LangGraph interrupt).
5. Every number in an LLM answer must exist in the KPI JSON (number guardrail).
6. All LLM calls go through one OpenAI-compatible client (`llmtrano.llm.client`), configured
   by `LLM_BASE_URL`. Never import an engine-specific SDK.
7. No container mounts `/var/run/docker.sock`. Simulations run inside the `worker` image
   with trano's local runtime (see `docs/trano-changes.md`).
8. Element ids in generated YAML come from the compiler, never from user text.
9. Postgres is the only database, in every environment (SQLite only inside unit tests).

## Stack and conventions

- Python 3.12, `uv` for dependencies and lockfile, `src/` layout, one package `llmtrano`.
- Pydantic v2 and pydantic-settings for config (`llmtrano.settings.Settings`, env vars only).
- FastAPI (api), LangGraph with the Postgres checkpointer (workflow), arq + Valkey (jobs),
  SQLAlchemy 2 typed models + Alembic (db), Gradio Blocks (ui), `openai` SDK (LLM client).
- arq has been in low-activity maintenance; if its last release is over 12 months old when you
  start, propose Dramatiq or Celery (both work with Valkey) in `docs/decisions.md` before using it.
- Before adding a dependency, check its latest stable version on PyPI and pin it in
  `pyproject.toml` with a compatible range; record anything surprising in `docs/decisions.md`.
- `ruff format`, `ruff check`, `mypy --strict` on `src/`, `pytest`. All must pass before a commit.
- Tests: unit tests need no GPU, no Docker and no OpenModelica. Tests that need them carry a
  marker: `@pytest.mark.llm`, `@pytest.mark.simulation`, `@pytest.mark.compose`.
- Logging with the standard `logging` module, structured key=value messages; no prints.
- Commit after each numbered step with a message `phase-N step N.M: <what>`.
- Write in plain English. No em dashes in docs or user-facing text.

## Commands

```bash
uv sync                                  # install
uv run pytest -m "not llm and not simulation and not compose"   # fast tests
docker compose --profile gpu up -d       # full local stack
uv run python -m llmtrano.bench         # latency and RAM benchmark (phase 0)
uv run python -m llmtrano.evals.run --gate --judge   # eval set with the gate and the LLM judge (phase 3)
uv run python -m llmtrano.evals.compare a.json b.json  # A/B of two eval runs
```

## Where things are

```
compose.yaml              all services; core services have no profile, llm + model-fetch use profile gpu,
                          prometheus + grafana use profile observability
config/llama-swap.yaml    model definitions for the llm service
docker/worker.Dockerfile  OpenModelica + trano + llmtrano
docker/app.Dockerfile     api and ui
src/llmtrano/
  settings.py  llm/  brief/  compiler/  workflow/  jobs/  kpi/  db/  api/  ui/  evals/  knowledge/  bench.py
tests/
docs/  phase-0.md ... phase-3.md  trano-changes.md  decisions.md  measurements.md  runbook.md
```

## When unsure

Read the phase file again, then ask. Do not invent trano YAML keys, OpenModelica variable
names or llama.cpp flags: look them up (trano schema, a real `.mat` file, `--help` output)
and write down where you found them.
