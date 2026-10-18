# Phase 0: Foundations

**Goal**: every service runs locally from one `docker compose` command, both models answer
through one OpenAI-compatible endpoint, trano simulates inside the worker without the Docker
socket, and the real latency and RAM numbers of your laptop are written down.

**Gate (end of phase 0)**
- `trano.api.simulate_yaml()` simulates `first_model.yaml` for a full year inside the `worker`
  container with `TRANO_SIMULATION_RUNTIME=local`.
- `docs/measurements.md` holds TTFT, tokens/s and RAM for both models, cold and warm, plus RAM
  with the MoE model loaded while a simulation runs.
- `docker compose --profile gpu up -d` brings everything up healthy from a clean clone.

**How to run this file with Claude Code**: open the repo, then prompt
`Read CLAUDE.md and docs/phase-0.md. Start step 0.1 in plan mode.` Do one step per session or
per few prompts, commit, and stop at each CHECKPOINT.

Hardware this phase is sized for: HP Victus 16, Ryzen 7 5800H (8 cores), 32 GB RAM, RTX 3060
Laptop GPU (6 GB VRAM) plus AMD iGPU, Ubuntu 26.04, 512 GB disk.

---

## Step 0.1 Host prerequisites (Ando runs the commands, Claude Code checks the output)

Concepts: #13 offload, #64 infrastructure as code.

1. `nvidia-smi` shows the RTX 3060 Laptop GPU, the driver version and the CUDA version it
   supports. Note the CUDA version: the llama-swap image tag depends on it (step 0.4).
2. The NVIDIA Container Toolkit is installed:
   `docker run --rm --gpus all ubuntu:24.04 nvidia-smi` prints the same GPU.
3. `docker compose version` is v2.
4. Free disk: at least 60 GB (models about 27 GB, OpenModelica image with libraries several
   GB, Postgres and results).
5. Hybrid graphics: the GPU must be visible to Docker (step 2 proves it). If it is not, fix
   the driver or PRIME setup before continuing.

Write the outputs (driver, CUDA, toolkit version, free disk) into `docs/measurements.md`
under "Host".

**CHECKPOINT 0.1**: show the four outputs.

## Step 0.2 Repository skeleton

Concepts: #61 versioning, #66 dev/prod parity.

Create:

```
pyproject.toml          uv project "llmtrano", Python 3.12, package "llmtrano" in src/
src/llmtrano/__init__.py
src/llmtrano/settings.py   Settings(BaseSettings): llm_base_url, llm_default_model,
                            llm_interpret_model, database_url, valkey_url, runs_dir,
                            simulation_library="Buildings", simulation_timeout_s=1200
tests/test_settings.py
.env.example            every Settings field with a safe local default, no secrets
.gitignore              .env, models/, runs/, .venv/, __pycache__/
docs/decisions.md       ADR-style log: date, decision, why, alternatives
docs/measurements.md    empty tables for steps 0.1, 0.5 and 0.7
```

Tooling in `pyproject.toml`: ruff (lint + format, line length 110), mypy strict for `src/`,
pytest with markers `llm`, `simulation`, `compose` registered and deselected by default
(`addopts = -m "not llm and not simulation and not compose"`).

Acceptance: `uv sync && uv run ruff check && uv run mypy src && uv run pytest` pass.

## Step 0.3 Model download as code (`model-fetch` service)

Concepts: #11 GGUF and quantization, #61 versioning, #64 pinned artifacts.

Models are data, not images. A one-shot service downloads them into the named volume
`models` at a pinned Hugging Face revision.

1. Look up, do not guess, the current files and commit hashes:
   - `bartowski/Qwen_Qwen3.5-9B-GGUF`, file ending `Q4_K_M.gguf`
   - `bartowski/Qwen_Qwen3.6-35B-A3B-GGUF`, file ending `Q4_K_M.gguf` (it may be split in
     several parts; download all parts)
   Use `hf` (huggingface_hub CLI) or the Hub API to list files and get the commit sha.
2. Put repo, filename pattern and revision for each model into `.env.example`
   (`MODEL_9B_REPO`, `MODEL_9B_FILE`, `MODEL_9B_REVISION`, same for `MODEL_MOE_*`).
3. `model-fetch` service: image `python:3.12-slim` (pin digest), installs `huggingface_hub`,
   runs `hf download <repo> --include <pattern> --revision <sha> --local-dir /models/<name>`
   for both models, then writes `/models/MANIFEST.json` with file names, sizes and sha256.
   It is idempotent: a second run with the files present exits quickly.
4. Record both sha256 values in `docs/decisions.md`.

Acceptance: `docker compose --profile gpu run --rm model-fetch` completes; the manifest lists
both models; file sizes are close to the published ones (about 5.6 GB and about 21 GB).

## Step 0.4 The `llm` service: llama-swap + llama-server

Concepts: #10 inference engines, #13 offload, #12 KV cache quantization, #16 prefix caching,
#18 OpenAI-compatible API, #19 model swapping, #6 thinking mode.

Why llama-swap: 6 GB of VRAM holds one model at a time. llama-swap reads the `model` field of
each OpenAI request and starts the matching `llama-server`, stopping the other one.

1. Image: `ghcr.io/mostlygeek/llama-swap` with the `unified-cuda` tag (CUDA 12) unless
   `nvidia-smi` showed CUDA 13 support, then `unified-cuda13`. Pin the digest. Find the
   `llama-server` path inside the image (`docker run --rm --entrypoint sh <image> -c 'command -v llama-server'`)
   and run `llama-server --help` once; keep that output in `docs/llama-server-help.txt` and
   use only flags that appear in it.
2. `config/llama-swap.yaml` with two model ids. Starting values (tune in step 0.5):

   | Setting | `qwen3.5-9b` | `qwen3.6-35b-a3b` |
   | --- | --- | --- |
   | Model file | `/models/qwen3.5-9b/...Q4_K_M.gguf` | first part of the MoE GGUF |
   | GPU layers | `-ngl 99`, lower if it does not fit | `-ngl 99` |
   | MoE experts on CPU | none | `--n-cpu-moe 36` (40 layers; lower it if VRAM allows) |
   | Context | `-c 32768 -np 2` (two slots of 16K: extractor and responder) | `-c 16384 -np 1` |
   | Attention, KV cache | `-fa on -ctk q8_0 -ctv q8_0` | same |
   | Templates | `--jinja` | `--jinja` |
   | Port | `--port ${PORT}` | `--port ${PORT}` |

   No `ttl`: the selected model stays loaded.
3. Compose service `llm`: GPU reservation (`deploy.resources.reservations.devices` with
   `driver: nvidia`, `count: all`, `capabilities: [gpu]`), volume `models:/models:ro`, config
   mounted read-only, port `127.0.0.1:8080:8080`, healthcheck on `/v1/models`, profile `gpu`.
4. Thinking mode: Qwen3.5/3.6 think by default. Verify that a per-request
   `"chat_template_kwargs": {"enable_thinking": false}` (sent via `extra_body` in the `openai`
   SDK) switches it off: the reply has no reasoning block. If the server ignores it, add a
   second model id per model with `--chat-template-kwargs '{"enable_thinking": false}'` and note
   that switching between them reloads the model. Record the outcome in `docs/decisions.md`.
5. Structured output check: send `response_format: {"type": "json_schema", "json_schema":
   {"name": "t", "schema": {...}}}` with a two-field schema (one enum) and confirm the reply
   parses and respects the enum.

**CHECKPOINT 0.4**: show `/v1/models`, one plain answer per model, the thinking on/off
comparison and the JSON schema test.

## Step 0.5 Benchmark: measure before deciding anything

Concepts: #14 prefill vs decode, #15 TTFT and tokens/s, #16 prefix caching, #20 SLOs, #2 MoE.

`src/llmtrano/bench.py` (run with `uv run python -m llmtrano.bench`):

- For each model and three prompts (50 tokens, 500 tokens, 2,000-token fixed system prefix +
  short question): run cold (first request after load) and warm (same prefix again), 3 runs
  each, streaming, `max_tokens 256`, thinking off.
- Measure client side: TTFT (time to first content token) and tokens/s after the first token.
  Also read llama-server's own `timings` from the final response when present
  (prompt tokens/s, predicted tokens/s, cached tokens) and print both.
- Measure memory: `nvidia-smi --query-gpu=memory.used --format=csv` and
  `docker stats --no-stream llm` RSS, plus `free -m` for the host.
- Print a markdown table and append it to `docs/measurements.md` with the date, model file,
  quantization and flags.

Compare with the proposed SLOs in the plan (TTFT under 1.5 s warm, 20 tok/s or more). Tune
`-ngl`, `--n-cpu-moe`, `-c` and the KV cache type until each model fits, then re-run.

**CHECKPOINT 0.5**: show the table and a short reading: which model meets which SLO, what the
warm vs cold difference says about prefix caching, and the RAM left for OpenModelica.

## Step 0.6 Database and queue

Concepts: #66 dev/prod parity, #63 async jobs.

1. `db`: `pgvector/pgvector:pg17` (pin digest), volume `pgdata`, healthcheck `pg_isready`,
   port `127.0.0.1:5432`. Credentials from `.env`.
2. `queue`: `valkey/valkey:8` (pin digest), healthcheck `valkey-cli ping`.
3. SQLAlchemy 2 typed models in `src/llmtrano/db/models.py` for phase 0 only: `runs`
   (id uuid, created_at, status enum queued|running|succeeded|failed, library, start_s, end_s,
   yaml text, result_path, duration_s, error_kind, error_message, log_tail, trano_version).
4. Alembic initialised in `src/llmtrano/db/migrations`, first migration creates `runs`.
   Migrations run at `api` start (idempotent `alembic upgrade head`).

Acceptance: `docker compose up -d db queue` starts both healthy; migration applied.

## Step 0.7 trano changes T1 to T5, then the `worker` image

Concepts: #63 async jobs, #66 parity, plus the security rule "no Docker socket".

**0.7a In the trano repository** (separate Claude Code session opened in the trano repo):
implement T1, T2, T3, T4 and T5 from `docs/trano-changes.md`, with their tests, on branch
`feat/llmtrano-runtime`. Run trano's own test suite before and after.

**0.7b In this repo**:

1. `docker/worker.Dockerfile`:
   - `FROM openmodelica/openmodelica:v1.26.9-ompython` (the version trano pins in
     `ModelicaEnvironment.openmodelica_image`; pin the digest).
   - Check the image's `python3 --version`. trano needs Python 3.11 or newer; if the image
     has an older Python, install uv and let it provide Python 3.12 (`uv python install 3.12`).
   - Install trano from the git branch at a pinned commit and this repo's `llmtrano` package.
   - `RUN python -m trano.simulate.install_libraries` (T4): libraries are installed at build
     time, so containers start fast and need no network.
   - `ENV TRANO_SIMULATION_RUNTIME=local`.
   - Runs as root in phase 0 because OpenModelica installs libraries under `/root/.openmodelica`;
     log this in `docs/decisions.md` as a phase 4 hardening item.
2. `src/llmtrano/jobs/worker.py`: arq `WorkerSettings` with `max_jobs = 1`,
   `job_timeout = settings.simulation_timeout_s`, and one job
   `run_simulation(ctx, run_id: str)` that loads the `runs` row, calls
   `trano.api.simulate_yaml(yaml_dict, library=..., options=SimulationOptions(start_time=..., end_time=...))`
   in a fresh directory `runs_dir/<run_id>`, and writes status, durations and errors back.
3. `src/llmtrano/jobs/smoke.py`: enqueues `tests/fixtures/first_model.yaml` (copied from
   trano's `tests/tutorials/first_model.yaml`) twice: one day, then a full year
   (`end_time = 365 * 86400`, `number_of_intervals = 8760`), and prints the results.
4. Compose service `worker` (no profile), volume `runs:/runs`, `depends_on` db and queue
   healthy, memory limit 8 GB (adjust after measuring).

**CHECKPOINT 0.7**: show both runs: success, duration, result file size. trano's docs say an
envelope-only model simulates a week in about a second, so most of the time should be
translation and C compilation; report the split if the log shows it. Then load the MoE model
and run the year simulation again while it is loaded; record peak host RAM in
`docs/measurements.md`.

## Step 0.8 `api` skeleton

Concepts: #18 one client, #62 streaming.

1. `docker/app.Dockerfile`: `python:3.12-slim` (pin digest), uv, this package. Used by `api`
   and `ui`.
2. `src/llmtrano/llm/client.py`: one `AsyncOpenAI(base_url=settings.llm_base_url, api_key="local")`
   factory with timeouts (connect 5 s, read 120 s) and one retry on connection errors.
3. `src/llmtrano/api/app.py` (FastAPI):
   - `GET /health`: db query, Valkey ping, `GET {llm}/v1/models`; returns each status.
   - `GET /models`: the model ids from llama-swap.
   - `POST /chat` (temporary, removed in phase 1): body `{model, messages}`, returns an SSE
     stream of content tokens from the LLM, thinking off.
   - `POST /runs`: body `{yaml_text, library, end_time_s}`; inserts a `runs` row, enqueues
     `run_simulation`, returns the id. `GET /runs/{id}`: the row.
4. Compose service `api`, port `127.0.0.1:8000`, healthcheck on `/health`.

Tests: unit tests with FastAPI's test client and a fake LLM client; one `@pytest.mark.compose`
test that calls the running stack.

## Step 0.9 `ui` skeleton

Concepts: #62 streaming.

Gradio Blocks app in `src/llmtrano/ui/app.py`, served on `127.0.0.1:7860`:
- a model dropdown filled from `GET /models`;
- a chat that streams from `POST /chat`;
- a "Simulate tutorial house" button that posts the fixture to `POST /runs` and polls
  `GET /runs/{id}` every 2 s, showing status and duration.

The UI talks only to `api`, never to `llm` or `db` directly.

## Step 0.10 Compose wiring and the gate

1. Profiles: the core services (db, queue, worker, api, ui) carry **no** profile, so they always
   start; `llm` and `model-fetch` carry the profile `gpu`. `docker compose up -d` runs the app
   without the LLM (useful for UI and simulation work); `docker compose --profile gpu up -d` runs
   everything. Later stacks (observability, rag, kg) get their own profiles.
2. Every service: pinned image digest, healthcheck, `restart: unless-stopped` (except
   `model-fetch`), ports bound to `127.0.0.1` only, `depends_on: condition: service_healthy`.
3. Named volumes: `models`, `pgdata`, `runs`.
4. `docker compose config` passes; a fresh clone + `.env` copy + up reaches all-healthy.
5. README: prerequisites, first run, how to switch model, where measurements live.

**CHECKPOINT 0.10 (phase 0 gate)**: from a clean clone, show `docker compose ps` all healthy,
the UI chatting with both models, the tutorial house simulated for a year from the UI, and the
completed `docs/measurements.md`. Then decide, with Ando, the default model for phase 1
interactive turns, and record it in `docs/decisions.md`.
