# Decisions

ADR-style log. Newest entries at the bottom. Each entry: date, decision, why, alternatives.

## 2026-10-18 Repository lives in its own folder

- **Decision**: the project repo is `~/Documents/repos/llmtrano`, with `CLAUDE.md` and `docs/`
  copied from `llmtrano-docs/llmtrano-instructions`.
- **Why**: keep the instruction bundle (and its zip) separate from the code that grows from it.
- **Alternatives**: use the instructions folder itself as the repo.

## 2026-10-18 NVIDIA Container Toolkit with CDI

- **Decision**: NVIDIA Container Toolkit 1.20.1 from NVIDIA's apt repo, Docker runtime configured
  with `nvidia-ctk runtime configure`, CDI spec generated at `/etc/cdi/nvidia.yaml`.
- **Why**: Docker 29 resolves `--gpus` through CDI. Before the spec existed it failed with
  "failed to discover GPU vendor from CDI: no known GPU vendor found".
- **Alternatives**: none practical for GPU containers.

## 2026-10-18 Python tooling versions (step 0.2)

- **Decision**: pydantic `>=2.14.1,<3`, pydantic-settings `>=2.16.0,<3`; dev: ruff `>=0.17.1,<0.18`,
  mypy `>=2.4.0,<3`, pytest `>=9.1.1,<10`. Build backend `uv_build` (uv 0.13.1). Python 3.12 is
  provided by uv because the host only has 3.13 and 3.14.
- **Why**: latest stable releases on PyPI on 2026-10-18. ruff is capped at the minor version because
  0.x minor releases can add or change lint rules. mypy is on the 2.x line now; noted in case
  older docs or plugins assume 1.x.
- **Alternatives**: tighter `==` pins; the lockfile `uv.lock` already gives exact reproducibility.

## 2026-10-18 Settings read env vars only, with local defaults

- **Decision**: `Settings` reads environment variables only (no `.env` file loading in code). Every
  field has a safe local default equal to `.env.example`. The database URL uses the
  `postgresql+psycopg` (psycopg 3) driver name.
- **Why**: one source of config per environment (#66 dev/prod parity): Compose injects variables with
  `env_file: .env`, the host exports them. psycopg 3 is what the LangGraph Postgres checkpointer uses,
  so one driver serves both SQLAlchemy and LangGraph.
- **Alternatives**: required fields with no defaults (safer against a silently wrong prod config;
  revisit in phase 4 hardening); `env_file=".env"` in code.

## 2026-10-18 Pinned model files (step 0.3)

- **Decision**: download these exact files into the `models` volume with the `model-fetch` service.

  | Model id | Repo | Revision (commit) | File | Size (bytes) | sha256 |
  | --- | --- | --- | --- | --- | --- |
  | `qwen3.5-9b` | `bartowski/Qwen_Qwen3.5-9B-GGUF` | `182be2fd6c7bc44887d88a91cb03ff009cc9f549` | `Qwen_Qwen3.5-9B-Q4_K_M.gguf` | 6,169,341,984 | `d784ce9eda1a5a7b51e8f705a9e6310844bf4f173654d115823c775fdea56d43` |
  | `qwen3.6-35b-a3b` | `bartowski/Qwen_Qwen3.6-35B-A3B-GGUF` | `5c2410d71524f4f72b023ce8daf7a80528226d5f` | `Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf` | 22,285,080,192 | `b46fedd33e0bfb0cae308aa3c158d0a4b2c4a1d2185a1ed6f093cdaf39064772` |

  Source: Hugging Face API `GET /api/models/<repo>?blobs=true` on 2026-10-18 (LFS sha256 per file).
  `model-fetch` recomputes each sha256 after download and fails on a mismatch.
- **Why**: a pinned commit plus checksum means every benchmark and eval refers to the same bytes (#61, #64).
- **Notes**: the MoE Q4_K_M is a single file, not split. The glob `*Q4_K_M*.gguf` still matches split
  parts if a later revision splits it. The fetch image is `python:3.12-slim` pinned by digest with
  `huggingface_hub==2.2.0` (ships `hf-xet` 1.7.0).
- **Alternatives**: bake models into the `llm` image (huge images, rebuild on every model change);
  download from llama-server's `-hf` flag at start (no pin, network needed at start).

## 2026-10-18 `llm` service: llama-swap image and llama-server flags (step 0.4)

- **Decision**: image `ghcr.io/mostlygeek/llama-swap:unified-cuda13` pinned at
  `sha256:75f8c2ca3a495c55792d9dc12da037a6a8fa8e86808b7d0366803d63698039ac` (llama-swap v265,
  llama-server at `/usr/local/bin/llama-server`, `--version` prints "0.7.0-dev, commit 547208d").
  CUDA 13 because the driver reports CUDA 13.2. Flags are checked against `docs/llama-server-help.txt`.
- **9B does not fit fully on the GPU**: `-ngl 99` failed with `cudaMalloc failed: out of memory`. The
  model has 33 blocks; `--fit` reported 5,821 MiB needed for all of them with only 5,574 MiB free.
  Starting value `-ngl 28` (5,255 MiB used with 2 x 16K q8_0 KV). Tune in step 0.5.
- **MoE**: `-ngl 99 --n-cpu-moe 36` loads and uses 4,375 MiB VRAM, so there is room to lower
  `--n-cpu-moe` in step 0.5. The "40 layers" figure from the plan is not yet checked against the GGUF.
- **Config mount**: `./config` is mounted as a directory. A single-file bind mount kept serving the old
  file after an edit (editors write a new inode), so llama-swap's `-watch-config` never saw the change.
- **Why**: one model at a time is all 6 GB of VRAM allows (#19); flags only from `--help` output.
- **Alternatives**: `unified-cuda` (CUDA 12) tag; plain llama-server without swapping (one model only).

## 2026-10-18 Thinking mode is switched off per request

- **Decision**: one model id per model. Interactive and extraction calls send
  `extra_body={"chat_template_kwargs": {"enable_thinking": false}}`.
- **Evidence** (2026-10-18, both models): with the kwarg, `reasoning_content` is empty and the answer
  is in `content`. Without it, both models think by default. The 9B spent all of `max_tokens=1024` on
  reasoning and returned empty `content`; the MoE thought for about 1,350 characters then answered.
  `response_format: json_schema` with an enum returned valid JSON that respected the enum on both.
- **Why**: no second model id with `--chat-template-kwargs`, so no reload when switching thinking.
- **Alternatives**: a second llama-swap entry per model with thinking disabled server side.

## 2026-10-18 Tuned llama-server flags (step 0.5)

- **Decision**: 9B stays at `-ngl 28`. MoE: `--n-cpu-moe 35 -b 2048 -ub 2048 --load-mode none`.
- **Why**:
  - `-b 2048 -ub 2048` doubled MoE prefill (230 to 480 tok/s on a cold 2k prompt) at about +150 MiB VRAM.
  - `--n-cpu-moe 34` reached 5,527 MiB VRAM (about 50 MiB free) for about 20% more prefill and no decode
    gain; 35 keeps about 500 MiB free. Each expert layer is about 500 MiB.
  - `--load-mode none`: with the default mmap, "loading" read little of the file and the first prompt of
    32 tokens or more paged in about 18 GB of experts (21 s for 16 output tokens), and llama-swap returned
    502 EOF on the first requests after a load. Reading weights at load costs 35 s instead of 20 s and makes
    the 17.8 GB visible as process RSS, so RAM accounting in step 0.7 is honest.
  - 9B `-ngl 29` or 30 would leave under 200 MiB VRAM for about 1 tok/s; not worth the OOM risk.
- **Open issue**: one 502 "proxy error: EOF" in 37 MoE requests even with `--load-mode none`; llama-server
  logs show no error, and the `openai` SDK's retry hid it. Suspected keep-alive reuse between llama-swap and
  llama-server. The step 0.8 client must keep a retry on 502 and connection errors; investigate if it recurs.
- **Benchmark definitions**: cold = unique nonce at the start of the prompt (no reusable prefix); warm = the
  same prompt again. Medians of 3 runs. The bench uses the `openai` SDK directly with `LLM_BASE_URL`; it moves
  to `llmtrano.llm.client` when that exists (step 0.8).
- **Alternatives**: mmap plus a warm-up request after each load (keeps graceful page eviction under RAM
  pressure, but the first real request after a swap is slow or fails); `--load-mode mlock`.
- **Dependency**: `openai>=3.35.1,<4` (released 2026-10-18, the same day; latest on PyPI).

## 2026-10-18 Database and queue (step 0.6)

- **Decision**:
  - `db`: `pgvector/pgvector:pg17` pinned at `sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`
    (PostgreSQL 17.11), volume `pgdata`, `127.0.0.1:5432`, credentials `POSTGRES_*` from `.env`.
  - `queue`: `valkey/valkey:8` pinned at `sha256:640c5e62cea04b6d6f2084232651d0cc70362d31f4f805e7be94dbed6855e8f2`
    (Valkey 8.1.10), `127.0.0.1:6379` so host-run scripts can enqueue. No volume: queued jobs are
    transient and the `runs` table in Postgres is the record of truth.
  - Libraries: `sqlalchemy>=2.1.5,<2.2`, `alembic>=1.20.0,<2`, `psycopg[binary]>=3.3.6,<4`.
    SQLAlchemy is capped at the minor version because 2.x minors have changed typing behaviour.
  - `runs.status` is a native Postgres enum `run_status`; constraint names follow a naming convention
    so autogenerate output stays stable.
  - Alembic `script_location = llmtrano.db:migrations` (package resource), so the same migrations run from
    the checkout and from the installed package in containers. `env.py` reads `DATABASE_URL` through
    `Settings`. `llmtrano.db.migrate.upgrade_head()` is what the api calls at start; it is idempotent
    (second run applies nothing). Autogenerated revisions are formatted by ruff via a post-write hook.
- **Checked**: upgrade, second upgrade (no-op), downgrade to base (table and enum type removed), upgrade
  again, `alembic check` reports no drift.
- **Alternatives**: plain `postgres:17` (pgvector is needed later for RAG; same image now avoids a switch);
  Redis instead of Valkey (license change; Valkey is the open fork with the same protocol).

## 2026-10-18 Worker image and jobs (step 0.7b)

- **Decision**:
  - `docker/worker.Dockerfile` from `openmodelica/openmodelica:v1.26.9-ompython` pinned at
    `sha256:91314c8bf3f26b11fbf8f3f3a8405be547ce3819d7eae915d988c34b7bd63a89` (Ubuntu 22.04, Python 3.10,
    so uv 0.13.1 provides Python 3.12; uv image pinned at
    `sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336`). Libraries are installed at
    build time with `python -m trano.simulate.install_libraries` (about 5 minutes; image 5.2 GB).
  - trano is a normal dependency from git, commit `4220d3cd2f9e2faee5613a4624dd363d8bd6064d` (branch
    `claude/gifted-maxwell-wna0b6`, not `feat/llmtrano-runtime` as the plan named it). It has T1 to T8. The
    api will need trano's validation in phase 1 anyway.
  - arq 0.28.0 (released 2026-04-16, within the 12-month rule). `max_jobs = 1`, `max_tries = 1` (a run
    that killed the worker would kill it again), `job_timeout = simulation_timeout_s + 60` so trano stops
    `omc` and reports `timeout` before arq cancels the job. The plan said `job_timeout = simulation_timeout_s`.
  - The worker's blocking work (trano, SQLAlchemy) runs in `asyncio.to_thread`.
  - Output points: one per simulated hour (`number_of_intervals = duration / 3600`), 8,760 for a year.
  - `trano_version` stored per run as `<version>+<commit>`, e.g. `0.52.0+4220d3c`.
  - Runs as root in phase 0: OpenModelica installs libraries under `/root/.openmodelica`.
    **Phase 4 hardening item**: non-root user with the libraries installed in a shared path.
  - mypy reads trano's inline types with `follow_untyped_imports` because trano has no `py.typed` marker.
- **trano issues found** (to fix in trano):
  - `simulate_yaml` with a dict and no `name` uses the package name `model`, a Modelica keyword:
    `checkModel(model.building)` fails with "No viable alternative near token: model". llmtrano passes
    `name=run_<8 hex>`.
  - `trano/simulate/simulate.py` emits `SyntaxWarning: invalid escape sequence '\;'` (the docker-runtime
    `find ... \;` string) on Python 3.12.
  - No `py.typed` marker.
- **Alternatives**: Dramatiq or Celery (not needed while arq is maintained); running the worker as non-root now.

## 2026-10-18 api skeleton (step 0.8)

- **Decision**:
  - `llmtrano.llm.client.make_llm_client()`: one `AsyncOpenAI` with timeouts connect 5 s / read 120 s and
    `max_retries=1`. The SDK retries connection errors and 5xx, which also absorbs llama-swap's occasional
    502 EOF (step 0.5). `NO_THINKING` lives next to it. (openai 3.x is built on `httpx2`, not `httpx`.)
  - FastAPI `>=0.145.0,<0.146` (released 2026-10-18, the same day) with its built-in SSE
    (`fastapi.sse.EventSourceResponse` + `ServerSentEvent`), uvicorn `>=0.54.0,<0.55`. No sse-starlette.
  - `/chat` events: `token` (`{"text": ...}`), then `done`, or `error`. Temporary, removed in phase 1.
  - `/health`: 503 when db or queue is down; when only the LLM is down it answers 200 `degraded`, so the
    api stays healthy without the `gpu` profile (checked: llm stopped -> `degraded`, `/models` 503).
  - `POST /runs` rejects text that is not a YAML mapping (422) before inserting; `GET /runs/{id}` does not
    echo the YAML back.
  - Services (sessions, queue, LLM) live on `app.state` and are injected by `create_app(services)` in tests.
    `enqueue_simulation` moved to `llmtrano.jobs.queue` so the api never imports trano's simulation code.
  - `docker/app.Dockerfile`: `python:3.12-slim` (same digest as model-fetch), uv, runs as uid 10001.
    Image 2.3 GB because trano's dependencies come along (needed for validation in phase 1).
  - `llmtrano.logs.configure_logging()` gives the `llmtrano` logger a handler in api and worker, whose
    runners (uvicorn, arq) configure only their own loggers.
- **Known gap**: `llmtrano.bench` still builds its own sync `OpenAI` client; move it onto the shared factory
  if it is kept beyond phase 0.
- **Alternatives**: sse-starlette (not needed now); api unhealthy without the LLM (would block the ui and
  simulation work without a GPU).

## 2026-10-18 ui skeleton (step 0.9)

- **Decision**:
  - Gradio `>=6.30.0,<7` Blocks app in `llmtrano.ui.app`, served by the `ui` service from the same
    `llmtrano/app:local` image as the api (`command: python -m llmtrano.ui.app`), `127.0.0.1:7860`.
  - The ui talks only to the api (`API_URL`, new `Settings.api_url`) with `httpx>=0.28.1,<1`; SSE from
    `/chat` is parsed by `parse_sse` (unit tested).
  - Gradio 6 stores chat content as a list of parts (`[{"type": "text", "text": ...}]`) plus `metadata` and
    `options`; `to_api_messages` turns history into plain `{"role", "content": str}`. Found in the browser:
    the second turn returned 422 before this conversion.
  - `GRADIO_ANALYTICS_ENABLED=False` (local-first: no usage analytics leave the machine).
  - `MPLCONFIGDIR=/tmp/matplotlib` in the app image: matplotlib (imported by Gradio) cannot write a cache
    under the non-root user's missing home.
  - trano's tutorial house moved from `tests/fixtures/` into the package (`llmtrano/data/first_model.yaml`,
    `llmtrano.data.tutorial_house_yaml()`), so the ui image ships it and the smoke run needs no mount.
- **Checked in the browser** (2026-10-18): dropdown filled from `/models`; a 9B answer streamed; a follow-up
  with the MoE selected used the history (3 messages); "Simulate tutorial house" (1 day) went
  queued -> running -> succeeded in 24 s and showed the result file path.
- **Alternatives**: a `gr.ChatInterface` (less control over the model dropdown and history format).

## 2026-10-18 Phase 1 default model (phase 0 gate, decided with Ando)

- **Decision**: `qwen3.5-9b` is the default for interactive turns (extraction, wording the next question,
  short answers): `LLM_DEFAULT_MODEL=qwen3.5-9b`. `qwen3.6-35b-a3b` is kept for interpretation (explaining
  simulation results): `LLM_INTERPRET_MODEL=qwen3.6-35b-a3b`. Both values are already the defaults in
  `Settings` and `.env.example`.
- **Why** (numbers in docs/measurements.md, "Summary for the phase 1 default model"):
  - RAM: the 9B uses about 4 to 5 GB of host RAM and leaves about 15 GB free during a year simulation; the
    MoE uses 17.6 GB and left 2.9 GB, and was OOM-killed once with a VM open.
  - Swaps: loading the 9B takes 8.6 s, the MoE 33 s. The interactive model is the one that stays loaded.
  - Latency: the 9B prefills an uncached 43-token turn in 0.33 s vs 1.30 s for the MoE; both meet the
    warm TTFT SLO, and the 9B meets the 20 tok/s decode SLO with little margin (23 to 24 tok/s).
- **Risks to watch in phase 1**: the 9B got one enum wrong in the step 0.4 extraction test ("roof insulated,
  walls bare" -> `none`, the MoE said `partial`). The phase 1 eval set decides whether extraction quality is
  good enough; if not, revisit (MoE for extraction, or a better prompt or schema).
- **Alternatives**: MoE for everything (no swaps, faster decode, better extraction in one test, but no RAM
  headroom next to a simulation on this laptop).

## 2026-10-19 trano from PyPI, T6 to T8 checked (step 1.0)

- **Decision**: depend on `trano>=0.56.0,<0.57` from PyPI instead of the git commit `4220d3c`. Capped at the
  minor version because trano is 0.x and 0.56.0 itself moved code into a new package.
- **Why**: T1 to T8 were merged into trano `master` (PR #55, which contains `4220d3c`) and every release from
  0.54.0 has them, so the plan's "pinned commit until a release contains them" no longer applies. The branches
  `feat/llmtrano-runtime` and `claude/gifted-maxwell-wna0b6` no longer exist. trano 0.56.0 (released
  2026-10-19) moves simulation into `trano-simulate` 0.2.0 (PR #57): `trano.simulate.*` still works as an
  alias, but llmtrano now imports `trano_simulate` (typed, ships `py.typed`) as trano advises, and the worker
  image installs libraries with `python -m trano_simulate.install_libraries`. `git` left the worker image.
  `trano_version()` now stores `0.56.0` (a PyPI install has no commit).
- **Checked** (OpenModelica 1.26.9, Buildings 13.0.0, IDEAS 3.0.0, numbers in docs/measurements.md, step 1.0):
  - T6: `on_phase` called with generating, compiling, simulating, done, in that order, for both libraries.
  - T7: the variable names in `trano.kpi` were confirmed from real result files of `first_model.yaml` for
    Buildings and for IDEAS (`Reader(path, "openmodelica").varNames(".*")`): `QHea_flow`, `TAir.T` and
    `heaSch.y[1]` under `building.emission1.heating_001`, the same in both libraries.
  - T8: a broken model gives one line per error with the YAML path and the field description.
- **Found, to fix in trano** (not blocking phase 1, Ando decides):
  - `trano.kpi` integrates `QHea_flow` with a trapezoid on the output grid. The heater's own `EHea` (an
    integrator of `QHea_flow` in trano's `IdealHeatingCooling`) is exact. On the hourly grid the trapezoid
    reads 1.5% high for a year (6,085 vs 5,996 kWh, Buildings; same for IDEAS) because the power spikes after
    the morning set point step and decays within the hour. With a 1-minute grid both agree to 0.02%.
    Proposed fix: energy = `EHea(end) - EHea(start)` when the variable exists, trapezoid otherwise.
  - Still open from step 0.7b: the dict default package name `model` (llmtrano passes `name=`), no `py.typed`.
- **Environment note**: the cloud sandbox where this was checked blocks `ghcr.io` blobs and
  `libraries.openmodelica.org`, so the worker image could not be built there; the same steps ran in the pinned
  OpenModelica image with the libraries cloned from their GitHub tags, and `ModelicaServices` patched the way
  OpenModelica's `+maint.om` build does (`loadResource` through `OpenModelica.Scripting.uriToFilename`; without
  it, `modelica://` weather paths do not resolve). On the laptop the Dockerfile is unchanged apart from the module
  name.
- **Alternatives**: keep the git pin on `master` (no reason now that releases exist); keep importing the
  `trano.simulate` aliases (works, but trano marks them as compatibility only).

## 2026-10-20 Phase 1 build decisions (steps 1.1 to 1.14)

Decisions taken while building phase 1 without stopping at the checkpoints; those that need Ando are listed in
`docs/phase-1-review.md`.

- **Dependencies** (latest stable on PyPI on 2026-10-20, capped at the minor version where 0.x or fast-moving):
  `langgraph>=1.2.16,<1.3`, `langgraph-checkpoint-postgres>=3.2.0,<3.3`, `psycopg-pool>=3.3.3,<4` (used directly
  by the checkpointer pool), `redis>=5.3.1,<6` (pub/sub for run progress; arq 0.28 requires redis < 6); dev:
  `syrupy>=6.1.1,<7`, `pytest-asyncio>=1.4.0,<2` (`asyncio_mode = "auto"`). arq's last release (0.28.0,
  2026-04-16) is within the 12-month rule.
- **The Brief in the state holds only user values**; defaults are applied for the card and at compile time, so a
  later answer can still replace them.
- **Heater sized at 1.4 times a simplified design heat loss** (EN 12831 at -8 degC, the rule of trano's
  residential cases) instead of trano's default 1 MW: measured in docs/measurements.md.
- **KPIs from the heater's `EHea` meter** (`llmtrano.kpi.compute`), trano's trapezoid kept for comparison; peak =
  highest hourly mean power. The proper home of this is `trano.kpi`.
- **Respond runs before the confirmation interrupt**, so the user is told the card is ready (the plan's graph
  went straight from route to confirm, which would leave the chat without a reply).
- **Answer buttons send a structured `{field, value}`** that skips the LLM; card edits override without a
  conflict; a chat correction becomes a conflict that is asked back.
- **Loop guard**: an optional field asked twice without an answer is not asked again (its default applies).
- **Checkpointer**: `AsyncPostgresSaver` on a psycopg pool, msgpack allowlist of llmtrano's state types only.
  Alembic ignores LangGraph's four checkpoint tables (`include_object` in `env.py`).
- **Guardrail**: also accepts a number written in kW or MWh when 1000 times it matches; the assumption lines
  count as known values; an empty interpretation counts as a failure.
- **Eval runner drives the graph directly** (same deps as the api, no HTTP), so it measures the workflow and
  the LLM, not uvicorn.
- **Gradio**: a fixed pool of 2 x 7 answer buttons (Gradio cannot add components while streaming); one chat
  bubble per message (`group_consecutive_messages=False`) so each answer has its own thumbs.
- **trano issues found** (for trano, not blocking): the KPI trapezoid (above); pydantic serializer warnings from
  numpy `float32` layout coordinates in `convert_network`; the dict default package name `model`; no `py.typed`.

## 2026-10-28 Phase 2 start: decisions carried over from the phase 1 review

Ando asked to build phases 2 and 3 in one go, taking the decisions of `docs/phase-1-review.md` on good practice and
collecting the ones that still need him at the end (`docs/phase-3-review.md`). Taken:

- **Ventilation** (review item 1): phase 2 models it. Natural ventilation adds outdoor air up to 0.5 1/h in total (the
  EN 12831 minimum trano's old reference house uses), mechanical extract adds 0.5 1/h on top of the infiltration, and
  balanced ventilation uses trano's system D with 0.8 heat recovery. The default by period is an assumption shown on the
  card (natural before 2006, extract from 2006, when the Flemish EPB started requiring a ventilation system).
- **"Argon" stays `double_low_e`** (item 2): real argon double glazing has a coating; the gate difference is explained,
  not hidden. The tutorial's uncoated argon glass gets no level of its own.
- **KPI method stays in llmtrano** (item 3) and grows to radiators and generators; moving it into `trano.kpi` is the
  trano follow-up T10 in `docs/trano-changes.md`.
- **Heater sizing, occupancy, roof midpoint rule, respond before the interrupt, corrections as conflicts, answer
  buttons without the LLM, the guardrail scope** (items 4 to 10): kept as built.
- **Extraction fixtures** (item 11) stay hand-written until the first live run on the laptop.
- **trano issues** (item 12) are written as T9 to T12 in `docs/trano-changes.md` for Ando to file; nothing in the
  trano repository changes in this phase.
- **trano version**: PyPI has trano 0.65.0 and trano-simulate 0.3.0 (checked 2026-10-28); llmtrano stays on
  `trano>=0.56.0,<0.57`, the version the worker image was measured with. The bump is a decision for Ando: the
  release notes since 0.57 (FastSmoothBuildings as a default library, the package split) have not been checked
  against the worker image here.
- **Heat-loss split**: a steady-state estimate from the compiler's U-values, areas and air flows, labelled an estimate.
  The per-element split read from the result file is the trano follow-up T11 (Buildings exposes
  `conExt[i].opa_b.Q_flow`, `conExtWin[i].glaUns_b.Q_flow`, `conBou[i].opa_b.Q_flow`; checked in trano's stored
  `three_zones_hydronic_heating.building_res.mat`).
- **Energy prices and CO2 factors** are settings with documented defaults (step 2.6); the values need Ando's check.

## 2026-10-29 Phase 3 build decisions (steps 3.1 to 3.7)

- **Generated eval cases are committed files**: `llmtrano.evals.generate` is deterministic and its phrases are
  checked against the parsers, but the files under `evals/cases/gen_*.yaml` are the golden set (reviewed once,
  a test fails when they are stale), so a change to the generator is a visible diff.
- **The gate fails on a metric it cannot measure** (None): a gate that cannot be checked is not passed.
- **The judge is a signal, not the gate**: it runs only with `--judge`, with the interpretation model, thinking
  on, near-greedy; the number guardrail stays the hard check. Its scores are compared between runs, not
  thresholded, until Ando has calibrated them against his own reading of a few transcripts.
- **References are a lookup, not a vector search**: a small YAML with a source per number; retrieval by building
  type, period and scenario, so the explanation can say where a comparison comes from. Entries without a
  document a reader can open are not accepted.
- **Dependency**: `prometheus-client>=0.26.0,<1` (0.26.0 released 2026-07-24; the official client, no
  surprises). No OpenTelemetry: tracing is deferred.
- **Observability images**: `prom/prometheus:v3.5.0` and `grafana/grafana:12.1.0` are pinned by tag only;
  the digests could not be resolved from the sandbox. Pin them by digest on the laptop like the other images
  (`docker compose --profile observability pull`, then `docker image inspect --format '{{index .RepoDigests 0}}'`).
- **Metrics live in the api process**: the worker has no HTTP server; the runs it executes are counted when
  the api receives their outcome, and the `runs` table is the source of truth for the Grafana table panel.
