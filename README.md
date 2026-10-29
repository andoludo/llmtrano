# llmtrano

A local-first LLM assistant that turns a plain-language house description into a
[trano](https://github.com/andoludo/trano) building model, simulates it with OpenModelica and explains
the result. Phase 1 is the golden path: describe one house in the chat, answer a few questions, confirm the
House Brief card, and get a one-year heating simulation with an explanation whose every number comes from
the simulation. Phase 2 adds ventilation, day and night zones, gas boilers and heat pumps on radiators, the
renovation you describe as a second scenario with its savings, a heat-loss split, the energy cost and the
CO2. Every service runs locally, both models answer through one OpenAI-compatible endpoint, and trano
simulates inside the `worker` container without the Docker socket. The workflow graph is in
[docs/workflow.md](docs/workflow.md); what each step does is in [docs/phase-1-notes.md](docs/phase-1-notes.md)
and [docs/phase-2-notes.md](docs/phase-2-notes.md). Phase 3 adds a 45-case eval set with a gate and an A/B
comparison, an LLM judge, grounded references, metrics and dashboards, and the hardening of a shared instance
([docs/phase-3-notes.md](docs/phase-3-notes.md)); how to run it all is in [docs/runbook.md](docs/runbook.md).

Project rules for contributors (human or Claude Code) are in [CLAUDE.md](CLAUDE.md); the phase plans are
in [docs/](docs/); the decisions that still need the owner are in
[docs/phase-3-review.md](docs/phase-3-review.md).

## Services

| Service | What it does | Port (localhost only) | Profile |
| --- | --- | --- | --- |
| `db` | Postgres 17 (pgvector image): runs, sessions, messages, facts, Briefs, KPIs, LangGraph checkpoints | 5432 | core |
| `queue` | Valkey 8: arq job queue | 6379 | core |
| `worker` | OpenModelica 1.26.9 + trano + llmtrano; runs model checks and simulations one at a time | none | core |
| `api` | FastAPI: `/sessions` (chat, confirmation, export, erasure; SSE), `/runs`, `/feedback`, `/health`, `/models`, `/metrics` | 8000 | core |
| `ui` | Gradio: chat with answer buttons, House Brief card with its renovation column, Confirm, run progress, results per scenario, heat-loss split | 7860 | core |
| `prometheus` | Scrapes the api's metrics | 9090 | `observability` |
| `grafana` | Dashboard: LLM latency, guardrail, runs, the last simulations | 3000 | `observability` |
| `model-fetch` | One-shot: downloads the pinned GGUF models into the `models` volume | none | `gpu` |
| `llm` | llama-swap + llama-server (CUDA 13): `qwen3.5-9b` and `qwen3.6-35b-a3b` | 8080 | `gpu` |

## Prerequisites

Checked on an HP Victus 16 (Ryzen 7 5800H, 32 GB RAM, RTX 3060 Laptop 6 GB), Ubuntu 26.04.

- Docker Engine with Compose v2 (`docker compose version`).
- For the `gpu` profile: an NVIDIA driver whose `nvidia-smi` reports CUDA 13 or newer, and the NVIDIA
  Container Toolkit configured for Docker with a CDI spec:

  ```bash
  sudo nvidia-ctk runtime configure --runtime=docker
  ```

  ```bash
  sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
  ```

  ```bash
  sudo systemctl restart docker
  ```

  Check with `docker run --rm --gpus all ubuntu:24.04 nvidia-smi`.
- Disk: about 60 GB free (models 28 GB, worker image 5.2 GB, app image 2.3 GB).
- RAM: the MoE model holds about 17.6 GB of host RAM and a year simulation peaks at about 4 GB. Close
  large applications (virtual machines) before loading the MoE. See [docs/measurements.md](docs/measurements.md).
- For development on the host: [uv](https://docs.astral.sh/uv/) (it provides Python 3.12).

## First run

```bash
cp .env.example .env
```

```bash
docker compose --profile gpu up -d
```

The first start builds the `worker` image (about 10 minutes: it installs the Modelica libraries) and
the `app` image, and `model-fetch` downloads about 28 GB of models before `llm` starts. Watch it with
`docker compose ps` until every service is `healthy`, then open <http://localhost:7860>.

Without a GPU, or to work on the ui and simulations only, leave out the profile:

```bash
docker compose up -d
```

The api then reports `"status": "degraded"` on `/health` (LLM unreachable) and stays healthy.

Check the simulation path from the command line (one day, then one year):

```bash
docker compose run --rm worker python -m llmtrano.jobs.smoke
```

## Switching models

- In the ui, pick the model in the dropdown. llama-swap loads the requested model and stops the other
  one, because 6 GB of VRAM holds one model at a time. The first request after a switch waits for the
  load (about 9 s for the 9B, about 33 s for the MoE).
- Through the api, set `"model"` in the `POST /sessions/{id}/messages` body to `qwen3.5-9b` or
  `qwen3.6-35b-a3b`; the interpretation always uses `LLM_INTERPRET_MODEL`.
- Defaults come from `.env`: `LLM_DEFAULT_MODEL` (preselected in the ui) and `LLM_INTERPRET_MODEL`.
- llama-server flags live in [config/llama-swap.yaml](config/llama-swap.yaml); llama-swap reloads the
  file when it changes. Every flag used appears in [docs/llama-server-help.txt](docs/llama-server-help.txt).
- Model files and revisions are pinned in `.env` (`MODEL_9B_*`, `MODEL_MOE_*`) and recorded with their
  sha256 in [docs/decisions.md](docs/decisions.md).

## Where measurements and decisions live

- [docs/measurements.md](docs/measurements.md): host facts, the latency benchmark (TTFT, tokens/s, VRAM,
  RAM per model, cold and warm) and simulation times and peak RAM, with the MoE loaded or not.
  Re-run the benchmark with `uv run python -m llmtrano.bench` (needs the `gpu` profile up).
- [docs/decisions.md](docs/decisions.md): every pinned version and digest, every flag, and why.

## Development

```bash
uv sync
```

```bash
uv run ruff format && uv run ruff check && uv run mypy src && uv run pytest
```

`pytest` skips tests marked `llm`, `simulation` and `compose` by default. Against the running stack:

```bash
uv run pytest -m compose
```

The eval set, its gate and the comparison of two runs (details in [docs/runbook.md](docs/runbook.md)):

```bash
uv run python -m llmtrano.evals.run --gate --judge
uv run python -m llmtrano.evals.compare evals/results/a.json evals/results/b.json --gate evals/gate.yaml
uv run python -m llmtrano.evals.generate          # rewrite the generated cases after changing an archetype
```

Database migrations run when the api starts. To create a new one after changing `llmtrano.db.models`
(with `.env` exported so `DATABASE_URL` points at the local `db`):

```bash
uv run alembic revision --autogenerate -m "describe the change"
```
