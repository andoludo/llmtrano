# Phase 1 notes

What each step does, why the design needs it and which flashcard concepts it uses (`#22` = concept 22 of
the LLM Flow Flashcards). Written while building phase 1; the open questions for Ando are collected in
`docs/phase-1-review.md`.

## Step 1.0 trano T6, T7, T8

trano 0.56.0 already ships the phase callback, the KPI module and the readable validation errors, so the
step became a dependency bump from a git commit to the PyPI release, plus a check of each change against
real OpenModelica result files. The KPI variables were listed from the `.mat` files, not guessed. The check
found that the KPI's trapezoid on the hourly grid reads 1.5% above the heater's own energy meter.

## Step 1.1 The House Brief

The Brief is the intermediate representation (#24) between the conversation and the trano model: a flat set
of typed slots (#31 slot filling) that the LLM fills and Python validates. Each enum is closed and each number
has bounds, so a wrong extraction fails loudly in Pydantic instead of reaching the compiler (#23 schema
design). Provenance is kept next to the values: the card can show "you said" or "assumed", and the planner
knows what the user actually stated. Defaults come from trano's reference house where one exists and are
marked as assumptions otherwise; `unknown` counts as "not given", so "I don't know" gets the default while
the user's words stay visible.
