# Phase 3 notes

What each step does, why the design needs it and which flashcard concepts it uses (`#22` = concept 22 of
the LLM Flow Flashcards). Written while building phase 3 on 2026-10-29; the open questions for Ando are
collected in `docs/phase-3-review.md`.

## Step 3.1 Eval set v2

Ten hand-written cases cannot tell a 90% model from a 95% one. The generator (#57 synthetic data) turns ten
archetypes, one per construction period and system, into thirty cases in three languages, each with an
opening, scripted answers and the expected values. The trick that makes them trustworthy is the
parser-consistency test: every phrase the generator writes is run through llmtrano's own parsers and must
give the expected value, so a perfect extraction scores 100% and anything below measures the model, not the
cases (#55 golden examples). The files are committed, not generated at test time: the golden set must be a
visible diff.

## Step 3.2 The gate and the comparison

An eval without a threshold is a report; with one it is a gate (#58). `evals/gate.yaml` holds the
thresholds on the summary metrics, and a metric the run could not measure fails: a gate that cannot be checked
is not passed. The comparison tool reads two result files and says what changed, metric by metric and case
by case, marking better and worse by the direction of each metric, so a prompt or model change is judged on
the regressions it causes, not on an aggregate that can hide them (#61 versioning: the header of each run
carries its date and models).

## Step 3.3 The LLM judge

The number guardrail catches invented numbers, not an invented driver, a benchmark that was never given or
an explanation nobody can read. The judge (#59) grades faithfulness (true only when every claim follows from
the inputs), clarity and actionability with a schema-constrained answer (#21), from the interpretation model
with thinking on. It is a signal next to the hard checks: its scores are compared between runs, and the cases
it finds unfaithful are listed for a human to read. It is never allowed to pass a text the guardrail rejected.

## Step 3.4 Grounded references

Phase 1 forbade comparisons because nothing trustworthy was loaded. Phase 3 loads a small table of published
results with a source for every number (#46 grounding) and retrieves the entries that apply to the house by
building type, period and scenario (#47 retrieval, as a lookup: the table is small and the match must be
explainable). The interpretation gets them as "References", the prompt rule becomes "compare only with these
and name the source", and the numbers join the guardrail's known values, so a quoted reference passes and an
invented benchmark still fails.

## Step 3.5 Observability

Every LLM call already logged one line; phase 3 makes it machine-readable (#65): `LOG_FORMAT=json` writes one
object per line with the session id and the run id of the current context, bound by the workflow per turn
and by the worker per job. Prometheus metrics give the dashboard what a log cannot: latency and first-token
histograms per call and model, guardrail outcomes, runs by kind, scenario and status. The compose profile
adds Prometheus and Grafana with provisioned datasources and one dashboard, including the last simulations
from the `runs` table through the Postgres datasource.

## Step 3.6 Hardening

A shared instance needs four things the laptop did not (#66): a bearer token (the ui sends it; health,
metrics and docs stay open for the probes), a per-session token bucket so one client cannot take the LLM and
the worker for everyone, the right to see and erase one's data (export and delete, with the rows deleted
explicitly rather than by cascade, the checkpoints removed and the result files swept by the worker), and a
retention period applied every night. The worker image drops root: the Modelica libraries are installed in
the worker user's home, where OpenModelica's package manager puts them.

## Step 3.7 Runbook and review

`docs/runbook.md` is the release checklist a second person can follow: the fast checks, the stack tests, the
eval run with the gate and the judge for both models, the comparison with the previous run, the dashboard,
the measurements. The review collects every decision that waits for Ando, across all three phases.
