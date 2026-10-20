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

## Step 1.3 Geometry heuristics

Users describe a house by floor area, storeys and a few words, never by wall areas, so the compiler needs a
fixed way to turn those into surfaces (the plan's missing "geometry strategy" step, #24). The rules are simple
on purpose and each one is listed as an assumption: a 1.5 rectangle with its long side on the street, gross
facade areas from storeys times room height, one window per exposed facade from the window-to-wall ratio, and
party walls replacing one or both short sides. The output is a typed `Geometry` model, so the YAML step only
formats numbers that have been checked (positive areas, windows smaller than their wall).

## Step 1.4 The compiler and its validation

`compile_brief` is the only place that writes trano YAML (#22: the LLM never writes it, so a wrong key or a
made-up id cannot reach OpenModelica). It applies the defaults, builds the geometry, picks the constructions
of the levels in the Brief and writes one space `HOUSE:001` with infiltration, occupancy, an ideal heater with
cooling off and the Uccle weather, mirroring `first_model.yaml`. Every id comes from the compiler, and the
model carries the list of assumptions and the sha256 of the trano schema it was written against (#25
validation: the worker's `check_yaml` is the second gate, its readable errors are stored on the run). Snapshot
tests freeze the YAML of three fixture houses; unit tests also run trano's schema check and its Modelica
generation without OpenModelica, and a `simulation` test runs `check_yaml` on all three.

Occupants: trano's occupancy model multiplies `heat_gain_if_occupied` (occupants per m2) by 35 W radiant,
70 W convective and 30 W latent per person (`SimpleOccupancy` in trano's `Trano.jinja2`), so the compiler
writes `occupants / floor area`.

## Step 1.5 LLM client and prompt files

Prompts are files, not strings in code, so each has a version (the first 8 hex characters of its sha256)
that is stored with every message and lets an eval result be traced to the exact prompt (#61). The system
message is assembled in a fixed order, static first: rules, field descriptions generated from the Brief's JSON
Schema, few-shot examples (#27, #29). The dynamic part (state summary, last message) is always the final user
message, so the system prefix is byte-identical across turns and llama.cpp skips its prefill (#16; phase 0
measured 2.4 s to 0.12 s for a cached 2k prefix). The three calls differ in sampling: extraction is near-greedy
with thinking off, the reply uses Qwen's non-thinking sampling, the interpretation may think. Every call is
streamed internally and logs model, prompt version, tokens, TTFT and latency on one line.

## Step 1.6 Extraction

Extraction turns free text into slot values with structured output (#21): `response_format: json_schema`
makes llama.cpp compile the schema into a grammar so the model can only emit valid JSON with allowed field
names (#22 constrained decoding). The schema is kept flat and `value` is always a string (#23): the grammar
stays simple, and Python does what must be exact, parsing "1,290 sq ft", "jaren 60" or "HR++" with tested
rules. Pydantic validates the output again, because constrained decoding guarantees shape, not sense: a value
can still be out of range or invented (#35 argument hallucination). One retry shows the model its own errors;
after that the turn's extraction is dropped and the planner simply asks again. Sampling is near-greedy
(temperature 0.1, #7) and four few-shot examples show the vague, precise, mixed and correction cases (#28).
Facts tagged `renovation` are kept apart for phase 2.

The pipeline fixtures (`tests/fixtures/extraction.yaml`) are hand-written in the model's output format,
because no LLM was available while building this step; the `llm` tests run 5 live sentences.

## Step 1.7 Merge and plan

This is the workflow-over-agent choice (#39): the LLM does not decide what to ask, Python does, from a fixed
priority list ordered by impact on the heating demand (conflicts, then building type, area, storeys, period,
walls, glazing, roof, airtightness, floor, set points). Merging is slot filling (#31) with rules that are easy to
test: a user value beats a default, a contradiction becomes a conflict that is asked back instead of silently
overwritten, a low-confidence or unclear fact is applied and asked back, and renovation plans are kept for
phase 2. Splitting a turn into extract, merge, plan, respond is task decomposition (#42): each part is small,
deterministic where it can be, and testable alone. Two additions to the spec: answer buttons send a structured
`{field, value}` that skips the LLM, and an optional field asked twice without an answer is not asked again,
so a vague user cannot cause a loop.

## Step 1.8 Respond

The reply is the only place where the model writes freely, and even there it only words decisions made by
Python: the facts noted, the renovation plans, the planner's questions and whether the Brief is ready (#27
system prompt with hard rules: at most four sentences, no numbers of its own, no advice before the
simulation). It streams token by token to the ui (#62), with Qwen's non-thinking sampling. The answer buttons
come from the planner's options, so a reply that words a question badly cannot change what can be answered.
The reply language follows the user (English, Dutch or French, by a stop-word count kept in the state, so a
one-word answer does not switch it).
