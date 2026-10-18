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
