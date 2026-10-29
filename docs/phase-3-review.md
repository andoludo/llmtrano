# Phase 3 review, and every decision that waits for Ando

Phases 2 and 3 were built in one session (2026-10-28 and 29) at Ando's request, in a cloud sandbox without
Docker, OpenModelica or a GPU. This file is the entry point for the review: what was verified, what to run
on the laptop, and the complete list of decisions, across all phases, that need Ando.

## What was verified

- 599 fast tests pass; `ruff format`, `ruff check` and `mypy --strict` are clean; every step is one commit
  (`phase-N step N.M: ...`) so the history reads as a tutorial.
- Everything the compiler can write passes trano's schema check and generates Modelica (no OpenModelica);
  the KPI reader runs on a real result file; the workflow, api and ui run both scenarios on fakes.
- The generated eval cases are parser-consistent (a perfect extraction scores 100%); the gate, the comparison
  tool and the judge are unit-tested with fake runs and a fake LLM.

## What was not run

Any simulation, any live LLM call, the eval set, the image builds, the observability profile and the
non-root worker image. The phase 2 and 3 gates are therefore pending the laptop:

```bash
docker compose --profile gpu --profile observability up -d --build
uv run pytest -m "simulation or compose"
uv run python -m llmtrano.evals.run --gate --judge
uv run python -m llmtrano.evals.run --gate --judge --model qwen3.6-35b-a3b
uv run python -m llmtrano.evals.compare evals/results/<a>.json evals/results/<b>.json --gate evals/gate.yaml
```

Then `docs/runbook.md`, release checklist, from step 5.

## Decisions for Ando, all phases

Ordered by what blocks a gate first. Each names what was built and where the decision is applied.

### Blocking a gate

1. **Run the live steps** (phase 1, 2 and 3 gates): the commands above and in `docs/phase-1-review.md`.
   Record the numbers in `docs/measurements.md`, replace the hand-written extraction fixtures by recordings.
2. **Energy prices and CO2 factors** (`Settings`, `.env`): 0.10 and 0.35 EUR/kWh, 0.202 and 0.20 kg/kWh are
   orders of magnitude. The cost and CO2 lines of every explanation depend on them.
3. **Gate thresholds** (`evals/gate.yaml`): starting values; tune after the first two live runs.
4. **trano version**: 0.65.0 on PyPI, llmtrano pins 0.56. Bumping needs the worker image rebuilt and the
   measurements redone; FastSmoothBuildings became a default library since.

### Modelling choices, kept unless you object

5. **Natural ventilation as 0.5 1/h in total**, mechanical extract as 0.5 1/h on top of the infiltration,
   system D at 0.5 1/h with 80% recovery (`compiler/ventilation.py`).
6. **Ventilation default by period**: natural before 2006, extract from 2006 (`brief/defaults.py`); TABULA
   or EPB statistics could not be fetched.
7. **"Argon" means low-e glazing** (phase 1 item 2); the tutorial's uncoated glass gets no level.
8. **Condensing boiler at 0.95 on the lower heating value, 60/50 degC**; non-condensing 0.85 at 75/65 degC
   (trano's reference); heat pump at 40/35 degC with trano's reference parameters (`compiler/systems.py`).
9. **Radiators oversized for the supply temperature** (1.0, 1.6, 3.9 times the 75/65/20 rating).
10. **Night zone at 18 degC from 6 to 23 h**, not user-settable yet.
11. **Heat-loss split as a steady-state estimate** from the design loss; the simulated split is trano T11.
12. **Heater sizing 1.4 times the design loss, KPIs from the heater meter, roof levels by the midpoint rule,
    occupants during the day hours** (phase 1 items 3 to 6).

### Workflow and product choices, kept unless you object

13. **Respond before the confirmation interrupt; chat corrections become conflicts, card edits replace;
    answer buttons skip the LLM; an optional field asked twice is not asked again** (phase 1 items 7 to 9).
14. **A Brief confirmed once is ready again as soon as new renovation plans arrive**; a baseline change
    reruns both scenarios (`brief/plan.py`, `workflow/graph.py`).
15. **The guardrail accepts kW and MWh at 1000 times, and counts the comparison, the split, the references and
    the assumption lines as known values** (`guardrails/numbers.py`, `workflow/graph.py`).
16. **The judge is a signal, not a gate**; its thresholds wait for a calibration against your reading.
17. **References are a hand-curated lookup**; add TABULA or EPC entries in `knowledge/references.yaml` with
    a source each.
18. **Retention 90 days, rate limit 30 turns per minute per session, token optional** (`Settings`).

### For trano

19. **T9 to T12** in `docs/trano-changes.md`: the heater meter in `trano.kpi`, radiator and generator KPIs,
    the per-element split from the result file, and the small fixes (`py.typed`, the dict package name, the
    float32 warnings, empty control mappings in `validate_schema`). Nothing in the trano repository changed.

### Deferred (not started, by design)

A user-set night zone temperature, domestic hot water, PV, floor heating, costs of the works and payback,
apartments, user-named rooms, tracing, alerting, multi-user accounts.

## What phase 3 would show at its checkpoint

The runbook followed once: an eval run with the gate table and the judge section, the comparison report
between the two models, and the Grafana dashboard with the run's latencies, guardrail outcomes and runs.
