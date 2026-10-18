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
