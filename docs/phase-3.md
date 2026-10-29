# Phase 3: Quality at scale and operations

**Goal**: know how good the assistant is, keep it that way, and run it like a service. A larger eval set
with a gate and an A/B comparison, an LLM judge for the explanations, grounded references so the
explanation can compare the house with something real, observability, and the hardening a shared
instance needs (authentication, erasure, retention, limits).

**Scope on purpose**

| In phase 3 | Deferred |
| --- | --- |
| 30 to 50 eval cases, generated from archetypes and phrasings plus the hand-written ones | Human-rated transcripts |
| Eval gate thresholds and an A/B report between two runs (prompt or model changes) | Automatic prompt optimisation |
| LLM judge: faithfulness and clarity of the explanation, scored with a schema | Judge calibration against human ratings |
| Grounded references with sources (trano's validation results), retrieved by house and added to the known values | Document retrieval over a corpus, vector search |
| JSON logs with session and run ids, Prometheus metrics, a compose profile with Prometheus and Grafana | Tracing, alerting |
| Optional api token, rate limit per session, session erasure and export, a retention job, non-root worker | Multi-user accounts, TLS termination (a reverse proxy's job) |

**Gate (end of phase 3)**: a release checklist in `docs/runbook.md` that a second person can follow: run
the eval set, compare it with the previous run, read the judge scores, check the metrics, and decide. The
eval set has at least 30 cases, the gate thresholds are in a file, and the comparison report says whether
a change is a regression.

**How to run this file with Claude Code**: `Read CLAUDE.md and docs/phase-3.md. Start step 3.1 in plan mode.`
Phase 3 was built in one go on 2026-10-28; see [phase-3-review.md](phase-3-review.md).

---

## Step 3.1 Eval set v2

Concepts: #54 offline evaluation, #55 golden examples, #57 synthetic data.

`src/llmtrano/evals/generate.py`: a deterministic generator (fixed seed) that combines house archetypes
(type, period, size, envelope, systems, renovation plans) with phrasing templates in English, Dutch and
French into case files with their expected values. The generated cases are written into `evals/cases/`
with `generated: true`, reviewed once and committed: the generator is a starting point, the files are the
golden set. Tests: at least 30 cases load, ids are unique, every expected value is a valid Brief value.

## Step 3.2 A/B comparison and the eval gate

Concepts: #58 eval gate, #61 versioning.

`evals/gate.yaml` holds the thresholds (field accuracy, scenario accuracy, valid model rate, simulation
success, number faithfulness, turns to confirmation). `python -m llmtrano.evals.run --gate` exits non-zero
when a threshold fails. `python -m llmtrano.evals.compare a.json b.json` writes a Markdown report: metric
deltas, cases that regressed, cases that improved, and whether the gate passes for each run.

## Step 3.3 LLM judge

Concepts: #59 LLM as judge, #21 structured output.

`judge.md` scores an explanation against the KPI JSON and the Brief: `faithful` (every claim follows from
the numbers), `clear` (1 to 5), `actionable` (1 to 5), `issues` (short strings), with a JSON Schema. The
judge is the interpretation model with thinking on. `--judge` adds the scores to the eval results and the
summary; a case with `faithful: false` is listed. The judge is a signal, not the gate: the number
guardrail stays the hard check.

## Step 3.4 Grounded references

Concepts: #46 grounding, #47 retrieval.

`src/llmtrano/knowledge/references.yaml`: a small table of reference values with their source, for example
trano's reference house before and after renovation (heating demand 130 and 17.6 kWh/m2, design heat
loss 97 and 21 W/m2) and the tutorial house. `retrieve(brief)` picks the entries that apply to the house
(building type, period, renovated or not); they are given to the interpretation as "References" and their
numbers join the guardrail's known values. The prompt rule changes from "compare with nothing" to
"compare only with the references given, and name the source". Ando can add TABULA or EPC references
later in the same file.

## Step 3.5 Observability

Concepts: #65 observability, #15 latency.

`LOG_FORMAT=json` switches the logs to one JSON object per line; a logging filter adds the session id and
the run id from context variables. `llmtrano.metrics` defines Prometheus metrics (LLM call latency and
tokens by call and model, guardrail outcomes, graph turns, runs by kind and status) and the api serves
`GET /metrics`. The `observability` compose profile adds Prometheus (scraping the api) and Grafana with a
provisioned datasource and one dashboard.

## Step 3.6 Hardening

Concepts: #66 production hardening, GDPR minimisation.

- `API_TOKEN`: when set, every endpoint but `/health` and `/metrics` needs `Authorization: Bearer`; the ui
  sends it.
- A token bucket per session on the message and confirm endpoints (`RATE_LIMIT_PER_MINUTE`).
- `DELETE /sessions/{id}` erases a session, its messages, facts, Briefs, runs and checkpoints;
  `GET /sessions/{id}/export` returns everything stored about it.
- A daily retention job in the worker deletes sessions older than `RETENTION_DAYS` and their run
  directories.
- The worker runs as a non-root user; the Modelica libraries are installed for that user.

## Step 3.7 Runbook and review

`docs/runbook.md`: start, upgrade, backup and restore, logs and metrics, the release checklist.
`docs/phase-3-notes.md`, `docs/phase-3-review.md`, README.

**CHECKPOINT 3.7 (phase 3 gate)**: the runbook followed once on the laptop: eval run, comparison with the
previous run, judge scores, the Grafana dashboard with the run's metrics.
