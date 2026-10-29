# Runbook

How to start, upgrade, check, back up and release llmtrano on the laptop. Written in phase 3; every command
was checked for syntax in the sandbox, none was run against a real stack there (see phase-3-review.md).

## Start and stop

```bash
cp .env.example .env            # once; set API_TOKEN when the api is reachable by others
docker compose --profile gpu up -d --build
docker compose ps               # every service healthy; the first start builds the worker (about 10 min)
```

Without a GPU: `docker compose up -d` (the api reports `degraded`). With the dashboards:
`docker compose --profile gpu --profile observability up -d`, then Grafana at <http://localhost:3000>
(admin, `GRAFANA_PASSWORD`) and Prometheus at <http://localhost:9090>.

Stop: `docker compose --profile gpu --profile observability down` (volumes stay). Remove the data too:
add `-v` (models, Postgres, runs, Prometheus and Grafana volumes).

## Upgrade

1. `git pull`, read `docs/decisions.md` for new settings and `.env.example` for new variables.
2. `docker compose --profile gpu up -d --build`: the api applies the Alembic migrations at start (idempotent).
3. `docker compose logs -f api worker` until `api ready` and `worker ready`.
4. Run the smoke test: `docker compose run --rm worker python -m llmtrano.jobs.smoke`.

A trano bump needs the worker image rebuilt (`--build` is enough) and the measurements redone
(`docs/measurements.md`).

## Check that it works

| What | How |
| --- | --- |
| Services | `docker compose ps`; `curl -s localhost:8000/health` is `ok` (or `degraded` without the llm) |
| Models | `curl -s localhost:8000/models` lists both ids; the ui dropdown shows them |
| A simulation | `docker compose run --rm worker python -m llmtrano.jobs.smoke` (one day, then one year) |
| The chat | open <http://localhost:7860>, describe `evals/cases/reference_house.yaml`, confirm, two runs |
| Metrics | `curl -s localhost:8000/metrics \| grep llmtrano_` |
| Logs | `docker compose logs api worker`; `LOG_FORMAT=json` for one JSON object per line with ids |

## Logs and metrics

- Every LLM call logs one line: `llm call=... model=... prompt_version=... tokens_in=... ttft_ms=...`.
- Every run logs `run start` and `run end` with the run id; with `LOG_FORMAT=json` the session and run ids
  are fields of each line.
- The Grafana dashboard shows LLM latency and first token per call and model, guardrail outcomes, runs by
  status, tokens per hour, and the last simulations from the `runs` table.

## Backup and restore

```bash
docker compose exec db pg_dump -U llmtrano llmtrano > backup-$(date +%F).sql
docker compose exec -T db psql -U llmtrano llmtrano < backup-2026-10-29.sql
```

The `runs` volume holds the result files; they are reproducible from the stored YAML and need no backup.
The models volume is 28 GB and re-downloadable (`model-fetch`).

## Data of a user

- Export: `curl -s localhost:8000/sessions/<id>/export > session.json`.
- Erase: `curl -X DELETE localhost:8000/sessions/<id>` (rows, checkpoints, and the worker removes the result
  files at its next sweep).
- Retention: the worker deletes sessions, runs and result files older than `RETENTION_DAYS` every night at
  03:17.

With `API_TOKEN` set, add `-H "Authorization: Bearer $API_TOKEN"` to every call but `/health` and `/metrics`.

## Release checklist (the phase 3 gate)

1. Fast checks: `uv run ruff format --check && uv run ruff check && uv run mypy && uv run pytest`.
2. Against the stack: `uv run pytest -m "simulation or compose"`.
3. The eval set with the gate and the judge, for the default model and the other one:

   ```bash
   uv run python -m llmtrano.evals.run --gate --judge
   uv run python -m llmtrano.evals.run --gate --judge --model qwen3.6-35b-a3b
   ```

4. Compare with the previous release's run and read the regressions:

   ```bash
   uv run python -m llmtrano.evals.compare evals/results/<previous>.json evals/results/<today>.json \
       --gate evals/gate.yaml --fail-on-regression --output evals/results/<today>-compare.md
   ```

5. Read the judge section of the summary (`## Judge`): every case it finds unfaithful is a prompt bug or a
   guardrail gap; fix before releasing.
6. Look at the Grafana dashboard during the eval run: no guardrail fallbacks, no failed runs, latencies as in
   `docs/measurements.md`.
7. Record the numbers in `docs/measurements.md`, tag the commit, write the decision in `docs/decisions.md`.
