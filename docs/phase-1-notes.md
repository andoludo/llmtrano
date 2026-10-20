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

## Step 1.2 Construction library

The compiler needs exact physics: a U-value is something code computes from layer properties, never something
the LLM states (#24, "code does what must be exact"). Every material and layer is copied from trano's
residential before/after renovation cases, and the intermediate levels only change the thickness of the
after-renovation insulation material, so every number has a source. The test recomputes U-values with the
method of `Case.resistance` in `residential_cases.py` and checks the none and good levels against the values
in the example comments.

U-values (W/m2K; `uv run pytest tests/test_constructions.py -k table -s`):

| Element | none | thin | medium | good |
| --- | --- | --- | --- | --- |
| Wall (+ 0, 4, 8, 12 cm EPS) | 1.47 | 0.52 | 0.31 | 0.23 |
| Ceiling under the attic (+ 0, 6, 12, 22 cm mineral wool) | 1.70 | 0.43 | 0.25 | 0.15 |
| Slab on ground (+ 0, 3, 5, 8 cm PUR, slab only, Rsi 0.17) | 3.43 | 0.63 | 0.41 | 0.27 |

| Glazing | Uw | Ug | g | Frame U | Source |
| --- | --- | --- | --- | --- | --- |
| single | 4.90 | 5.81 | 0.86 | 2.2 | assumption |
| double_old | 2.71 | 2.88 | 0.76 | 2.2 | before renovation |
| double_low_e | 1.17 | 1.10 | 0.66 | 1.4 | after-renovation glass, tutorial gap and frame |
| triple | 0.80 | 0.70 | 0.47 | 1.1 | after renovation |

Uw uses trano's own glazing calculation (EN 673) and a frame fraction of 0.25, as in the reference cases.
