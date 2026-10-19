# Phase 1: The golden path

**Goal**: a user describes a house in the chat, answers a few questions, confirms a House
Brief card, and gets an annual simulation with an explanation whose every number comes from
the simulation. One house, one thermal zone, ideal heating, one weather file.

**Scope on purpose (happy path)**

| In phase 1 | Deferred |
| --- | --- |
| Detached, semi-detached and terraced houses | Apartments (ask "not supported yet") |
| One zone (the plan's default is one or two; phase 1 builds one) | Day/night zones, user-defined zones (phase 2) |
| Ideal heating (`ideal_heating_cooling`, cooling off) | Boilers, heat pumps, radiators, hydronics (phase 2) |
| Uccle TMYx weather for every Belgian address | Weather per municipality |
| Baseline only; renovation facts are stored, not simulated | Renovation overlay and comparison (phase 2) |
| Heating energy, kWh/m², peak heating power | Heat-loss split per element, costs, CO2 (phase 2) |
| 10 eval cases | 30 to 50 eval cases, model A/B (phase 3) |

**Gate (end of phase 1)**: the tutorial house of trano (`tests/tutorials/first_model.yaml`),
described in plain words in the chat, goes through confirmation, simulation and explanation;
the stated facts land in the Brief with 100% field accuracy; every number in the explanation
matches the KPI JSON; and the annual heating need is compared with simulating
`first_model.yaml` directly, with the difference explained (geometry heuristics, window
placement, ventilation input). Note: the plan doc names trano's six-zone reference house for
this gate; that house needs zoning and hydronics, so it moves to the phase 2 gate.

**How to run this file with Claude Code**: `Read CLAUDE.md and docs/phase-1.md. Start step 1.0 in plan mode.`

---

## Step 1.0 trano changes T6, T7, T8

In the trano repo, on `feat/llmtrano-runtime`: implement T6 (phase callback), T7
(`trano.kpi` for ideal heating) and T8 (readable validation errors) from
`docs/trano-changes.md`. For T7, discover the result variable names from a real `.mat` file of
`first_model.yaml` for Buildings (the library this phase uses) and IDEAS; do not guess them.
Bump the pinned commit in this repo.

**CHECKPOINT 1.0**: show the variable names found, the KPI JSON for one simulated year of
`first_model.yaml`, and the readable error for a deliberately broken YAML.

## Step 1.1 The House Brief

Concepts: #23 schema design, #24 intermediate representation, #31 slot filling.

`src/llmtrano/brief/model.py`. Two models: the values, and their provenance.

Outline (write each enum in full, `member = "member"`, one per line):

| Enum | Members |
| --- | --- |
| `BuildingType` | detached, semi_detached, terraced, apartment |
| `Period` | before_1946, y1946_1970, y1971_1990, y1991_2005, y2006_2020, after_2020 |
| `InsulationLevel` | none, thin, medium, good, unknown |
| `Glazing` | single, double_old, double_low_e, triple, unknown |
| `Airtightness` | leaky, average, tight, unknown |
| `Orientation` | north, east, south, west |

```python
class HouseBrief(BaseModel):
    """Every field is None until filled. Use Field(default=None, description=..., ge=..., le=...)."""

    building_type: BuildingType | None
    construction_period: Period | None
    heated_floor_area_m2: float | None      # 20 to 1000
    storeys: int | None                     # 1 to 4
    room_height_m: float | None             # 2.2 to 4.0
    street_facade_orientation: Orientation | None
    wall_insulation: InsulationLevel | None
    roof_insulation: InsulationLevel | None
    floor_insulation: InsulationLevel | None
    glazing: Glazing | None
    window_to_wall_ratio: float | None      # 0.05 to 0.6
    airtightness: Airtightness | None
    heating_setpoint_day_c: float | None    # 15 to 25
    heating_setpoint_night_c: float | None  # 12 to 22
    day_start_h: int | None                 # 0 to 23
    day_end_h: int | None                   # 1 to 24
    occupants: int | None                   # 1 to 12
    municipality: str | None                # stored; phase 1 always uses Uccle weather

class Source(StrEnum):
    user = "user"
    default = "default"


class FieldProvenance(BaseModel):
    source: Source
    evidence: str | None      # the user's words
    confidence: Literal["low", "medium", "high"] | None
    turn: int | None
```

`src/llmtrano/brief/defaults.py`: one function `apply_defaults(brief) -> (brief, provenance)`
that fills missing optional fields and marks them `Source.default`. Defaults and their source:

| Field | Default | Where it comes from |
| --- | --- | --- |
| room_height_m | 2.6 | trano reference house |
| heating_setpoint_day_c / night_c | 21 / 16 | trano reference house (21 degC living, 16 degC setback) |
| day_start_h / day_end_h | 7 / 23 | trano reference house schedule |
| window_to_wall_ratio | 0.20 | rule of thumb, mark as assumption |
| airtightness unknown | `average` | rule of thumb, mark as assumption |
| insulation unknown | from `construction_period` (table below) | rule of thumb until TABULA data (phase 2) |
| street_facade_orientation | north | trano reference house (street facade north) |
| occupants | 2 | rule of thumb |

Period to default insulation (assumption, shown on the card): before 1946 and 1946-1970 none;
1971-1990 thin; 1991-2005 medium; 2006 and later good. Glazing default by period: before 1991
double_old, 1991-2005 double_low_e, after 2005 double_low_e.

Required before confirmation (no default allowed): `building_type`, `heated_floor_area_m2`,
`storeys`, `construction_period`.

Tests: enums and bounds, defaults never overwrite user values, provenance is complete.

## Step 1.2 Construction library

Concepts: #24, and "code does what must be exact".

`src/llmtrano/compiler/constructions.py`: trano materials, constructions and glazings per
level, built from trano's own examples so every value has a source.

| Element | none | thin | medium | good |
| --- | --- | --- | --- | --- |
| Wall | `EXTERNAL_WALL:001` of `residential_before_renovation.yaml` (U 1.47) | + 4 cm EPS | + 8 cm EPS | + 12 cm EPS as in `residential_after_renovation.yaml` (U 0.23) |
| Roof (ceiling under cold attic) | `ROOF:001` before (U 1.70) | + 6 cm mineral wool | + 12 cm | + 22 cm as after (U 0.15) |
| Floor on ground | `GROUND_FLOOR:001` before | + 3 cm PUR | + 5 cm | + 8 cm PUR as after |

Glazing: `double_old` = before `WINDOW:001` (Uw about 2.7, frame 2.2); `triple` = after
(Uw about 0.8, frame 1.1); `double_low_e` = two panes with one low-e coating and argon, frame
1.4 (built from the after-renovation glass materials and the tutorial's frame value);
`single` = one 4 mm pane, frame 2.2 (assumption, flagged). Copy material properties
(conductivity, density, heat capacity, glass optics) from those files; do not invent them.

Test: compute the U-value of each opaque construction with the same method as
`Case.resistance` in trano's `examples/residential_cases.py` and assert that the
none and good levels reproduce the U-values written in the example comments within 0.02 W/m2K.
Print a table of all levels in the test output for Ando to review.

## Step 1.3 Geometry heuristics

Concepts: #24 compiler, and the plan's "geometry strategy" missing step.

`src/llmtrano/compiler/geometry.py`, pure functions, fully unit tested:

- Footprint = heated floor area / storeys. Rectangle with aspect ratio 1.5 (assumption,
  flagged); the long side faces the street orientation.
- Azimuths follow trano's convention in `residential_cases.py`: south 0, west 1.5708,
  north 3.1416, east -1.5708 (radians).
- Facade height = storeys x room height. Gross facade area per orientation.
- Party walls: semi-detached, one short side is an adiabatic wall; terraced, both short sides.
  Detached, none.
- Windows: window-to-wall ratio x gross area of each exposed facade, one window per facade.
- Roof: area = footprint, `tilt: ceiling`, roof construction. Floor: `floor_on_grounds` with
  area = footprint and perimeter = exposed perimeter (needed by IDEAS, harmless for Buildings).

Output a typed `Geometry` model (surfaces with area, azimuth, tilt, kind) that the compiler
turns into YAML. Tests: areas add up, party walls remove the right facades, no negative or
zero areas, orientation rotation is correct.

## Step 1.4 The compiler and its validation

Concepts: #24, #25 validation and repair, #22 (why the LLM never writes this).

`src/llmtrano/compiler/compile.py`: `compile_brief(brief, provenance) -> CompiledModel`
with `yaml_text`, `model_dict`, `assumptions: list[str]` and `schema_hash` (sha256 of trano's
`trano_final.yaml` in the installed trano).

Structure, mirroring `first_model.yaml`:
- `material`, `constructions`, `glass_material`, `gas`, `glazings` from step 1.2, only the
  levels used.
- One space `HOUSE:001`, `variant: infiltration`, parameters `floor_area`,
  `average_room_height`, `n50` (leaky 12, average 6, tight 3: 12 and 3 are the trano before and
  after cases, 6 is a rule of thumb).
- `occupancy` from the day schedule, `external_boundaries` from step 1.3.
- `emissions: [ideal_heating_cooling]` with `heating_setpoint_schedule` built from the
  set points and hours (format as in `first_model.yaml`, temperatures in kelvin),
  `maximum_cooling_power: 0` (cooling off), heating capacity left at trano's default.
- `weather.parameters.path` = the Uccle TMYx resource string used in
  `residential_before_renovation.yaml`.
- All ids generated by the compiler (`HOUSE:001`, `EXTERNAL_WALL_GOOD:001`, ...).

Validation: `trano.api.check_yaml(model_dict, library=settings.simulation_library)` runs in the
worker (job `check_model`). Readable errors (T8) are stored on the run.

Tests: snapshot tests (`syrupy`) of the YAML for three fixture Briefs (detached old,
semi-detached 1980s, terraced renovated); `@pytest.mark.simulation` test that all three pass
`check_yaml`.

**CHECKPOINT 1.4**: show the YAML for a Brief that describes the tutorial house next to
`first_model.yaml`, and list the differences.

## Step 1.5 LLM client and prompt files

Concepts: #27 system prompt, #29 prompt assembly, #16 prefix caching, #61 versioning.

- Prompts live in `src/llmtrano/llm/prompts/` as Markdown files: `extract.md`,
  `respond.md`, `interpret.md`. `prompt_version` = first 8 hex characters of the sha256 of the
  file. Stored with every message.
- Assembly order is fixed, static first: rules, then field descriptions (generated from
  `HouseBrief.model_json_schema()`), then few-shot examples, then the dynamic part (state
  summary, last user message). Never put a timestamp or session id in the static part.
- `src/llmtrano/llm/calls.py`: `extract()`, `respond()`, `interpret()`, each with its own
  sampling settings, `max_tokens`, thinking flag (from step 0.4) and model id from settings.
  Each logs model, prompt version, input and output tokens, TTFT and total latency.

## Step 1.6 Extraction

Concepts: #21 structured output, #22 constrained decoding, #23 flat schema, #28 few-shot,
#35 argument hallucination, #7 sampling.

Response schema (keep it flat for llama.cpp: no `$ref`, no `anyOf` mixed with properties):

```json
{"type": "object",
 "properties": {
   "facts": {"type": "array", "items": {"type": "object",
     "properties": {
       "field": {"enum": ["building_type", "construction_period", "...every HouseBrief field"]},
       "value": {"type": "string"},
       "scenario": {"enum": ["baseline", "renovation", "unclear"]},
       "evidence": {"type": "string"},
       "confidence": {"enum": ["low", "medium", "high"]}},
     "required": ["field", "value", "scenario", "evidence", "confidence"]}},
   "user_says_complete": {"type": "boolean"}},
 "required": ["facts", "user_says_complete"]}
```

- `value` is always a string; Python parses it per field (numbers with units such as
  "120 m2", enums with a synonym table: "dubbel glas" -> double_old, "HR glass" ->
  double_low_e). This keeps the grammar simple and the parsing testable.
- Sampling: temperature 0.1, thinking off, `max_tokens` 600.
- Validate with Pydantic. On failure, one retry that includes the validation message; then
  give up on this turn's extraction and let the planner ask again.
- Facts with `scenario: renovation` go to a `renovation_facts` list in the state and the DB;
  the reply acknowledges them ("noted for the renovation step").
- Few-shot: four short examples in `extract.md`: a vague description, a precise one, a mixed
  baseline + renovation sentence, and a correction of an earlier answer.

Tests: parser unit tests for every field; recorded LLM outputs as fixtures for the extraction
pipeline; `@pytest.mark.llm` live tests on 5 sentences.

## Step 1.7 Merge and plan (pure Python)

Concepts: #31 slot filling, #39 workflow over agent, #42 task decomposition.

`src/llmtrano/brief/merge.py`: user values override defaults; a new user value that conflicts
with an earlier user value is not applied but added to `conflicts` (the planner asks).
Low-confidence facts are applied but asked back for confirmation.

`src/llmtrano/brief/plan.py`: `next_questions(brief, provenance, conflicts) -> list[Question]`
(at most 2). Priority, highest impact on heating demand first: conflicts, building_type,
heated_floor_area_m2, storeys, construction_period, wall_insulation, glazing, roof_insulation,
airtightness, floor_insulation, set points. Each `Question` has the field, a plain-language
prompt seed and answer options (enum values with human labels plus "I don't know").
Phase change to confirmation when all required fields are filled and either the user said the
description is complete or at least wall, roof and glazing are user-given.

Tests: table-driven unit tests for both functions.

## Step 1.8 Respond

Concepts: #27, #62 streaming.

`respond()` gets: facts acknowledged this turn, questions from the planner, renovation facts
noted. It writes a short reply (at most 4 sentences) and asks the questions in plain words.
Thinking off, the vendor's non-thinking sampling (temperature 0.7, top_p 0.8, top_k 20,
presence_penalty 1.5), streamed. The answer buttons in the UI come from the planner's
options, not from the LLM text.

## Step 1.9 The workflow (LangGraph)

Concepts: #38 architectures, #39 workflow, #40 LangGraph, #41 human in the loop.

`src/llmtrano/workflow/graph.py`. State (Pydantic): `session_id`, `phase`, `brief`,
`provenance`, `conflicts`, `renovation_facts`, `pending_questions`, `recent_messages` (last 6),
`compiled` (yaml, assumptions), `run_id`, `kpis`, `explanation`.

Nodes and edges:

```
intake turn:   extract -> merge -> plan -> route
route:         confirm_ready ? confirm : respond -> END (wait for next user message)
confirm:       interrupt() with the Brief card (values, sources, assumptions)
               resume {"approved": true}            -> compile
               resume {"approved": false, edits}    -> merge -> plan -> respond
compile:       compile_brief -> enqueue check_model -> await -> ok ? simulate : explain_error
simulate:      enqueue run_simulation -> await result (progress goes to Valkey, see 1.10)
kpis:          read KPI JSON from the run
interpret:     interpret() + number guardrail -> END
```

- Checkpointer: the Postgres checkpointer from `langgraph-checkpoint-postgres`,
  `thread_id = session_id`. Kill the api mid-session and resume: the session continues.
- Export the graph as Mermaid (`graph.get_graph().draw_mermaid()`) into
  `docs/workflow.md` on every change (a test fails if it is stale).

Tests: graph tests with a fake LLM and a fake job queue covering: a two-turn intake, a
rejection with an edit, a validation error, a successful run.

## Step 1.10 Jobs: check, simulate, KPIs, progress

Concepts: #63 async jobs, #15 latency, #46 grounding.

- `check_model(run_id)`: `trano.api.check_yaml`, stores readable errors.
- `run_simulation(run_id)`: `trano.api.simulate_yaml` with `start_time 0`,
  `end_time 365 * 86400`, `number_of_intervals 8760`; `on_phase` publishes
  `{"run_id", "phase", "t"}` to the Valkey channel `run:<id>` and updates the row; then
  `trano.kpi.building_kpis` and stores the KPI JSON in `kpis`.
- Timeouts from settings; failures keep the log tail and the error kind.
- The api exposes `GET /runs/{id}/events` as SSE by subscribing to `run:<id>`.

## Step 1.11 Interpretation with a number guardrail

Concepts: #45 hallucination, #46 grounded generation, #49 output guardrail, #6 thinking.

- Input to `interpret()`: the KPI JSON, the confirmed Brief with sources, the assumptions list.
  Model: `settings.llm_interpret_model` (the phase 0 decision), thinking allowed.
- Instructions: explain what the heating need means for this house, do not compare kWh/m2 with
  external benchmarks (none are loaded in phase 1), and name likely drivers **only** from the
  Brief (for example "walls without insulation") and say they are likely, because the
  per-element heat-loss split arrives in phase 2. List the main assumptions.
- Guardrail (`src/llmtrano/guardrails/numbers.py`): extract every number from the text; each
  must match a value in the KPI JSON or the Brief after rounding (relative tolerance 1%, or the
  same integer). On a mismatch, regenerate once with the list of wrong numbers; if it fails
  again, show the KPI table and a fixed template sentence instead of the LLM text, and log it.
- The UI shows the KPI table drawn by code next to the explanation.

Tests: unit tests for the guardrail with good and bad texts (units, thousands separators,
decimals with comma and point).

## Step 1.12 Persistence

Concepts: #61 versioning, #60 feedback loop.

Alembic migration adding: `sessions`, `messages` (role, content, model, prompt_version,
tokens_in, tokens_out, ttft_ms, latency_ms), `facts` (field, value, scenario, evidence,
confidence, accepted), `house_briefs` (session, version, values JSON, provenance JSON,
schema_hash, trano_version, confirmed_at), `runs` gets `session_id` and `kind`
(check|simulate), `kpis` (run_id, JSON), `feedback` (message_id, thumbs, comment).
Store the municipality as given; no street addresses (GDPR minimisation).

## Step 1.13 API and UI

API (replace the phase 0 `/chat`):
- `POST /sessions` -> id.
- `POST /sessions/{id}/messages` -> SSE: `token` events while respond streams, then
  `brief` (the updated card), `questions` (with options), `phase`.
- `POST /sessions/{id}/confirm` with `{approved, edits}` -> resumes the graph; SSE events
  `run_phase`, `kpis`, `token` (interpretation), `guardrail` (if it triggered).
- `GET /sessions/{id}` (state for reloads), `GET /models`, `POST /feedback`.

UI (Gradio Blocks), two columns:
- Left: chat with streaming, answer buttons from `questions`, model dropdown for interactive
  turns.
- Right: the House Brief card (field, value, a "you said" or "assumed" badge, evidence on
  hover), conflicts highlighted, a Confirm button active only in the confirm phase, run
  progress, KPI table, thumbs up/down under each answer.

## Step 1.14 Eval set v0 and the gate

Concepts: #54 offline evaluation, #55 golden examples, #56 agent tests, #58 eval gate.

`evals/cases/*.yaml`, 10 cases. Each case: an opening description, a scripted user that
answers by field (a dict field -> answer text, plus "I don't know" for the rest), the expected
Brief values for the stated fields, and whether the user mentions renovation.
Include: the tutorial house (gate case), a vague 1960s semi-detached house, a precise
terraced house, a mixed baseline + renovation description, a correction mid-conversation,
two Dutch-language descriptions, an apartment (expect "not supported yet"), a house with
contradictory area, and one with numbers in unusual units.

`src/llmtrano/evals/run.py` drives the real workflow (live LLM, real worker) and writes
`evals/results/<date>-<model>.json` and a Markdown summary with:

| Metric | Definition |
| --- | --- |
| Field accuracy | stated fields extracted with the right value / stated fields |
| Scenario accuracy | renovation facts tagged renovation / renovation facts |
| Turns to confirmation | user turns until the confirm phase |
| Valid model rate | compiled models passing `check_yaml` / cases reaching compile |
| Simulation success | successful runs / runs |
| Number faithfulness | explanations passing the guardrail first time / explanations |
| Latency | median and p95 TTFT and turn time |

Run it for the default model; run it once for the other model for comparison.

**CHECKPOINT 1.14 (phase 1 gate)**: the tutorial-house case end to end in the UI (screen
recording or screenshots), the eval summary, the comparison of annual heating with
`first_model.yaml` simulated directly and the reasons for the difference, and a short list of
what failed and why. Ando decides what goes into phase 2.
