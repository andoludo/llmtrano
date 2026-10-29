# Phase 2: Beyond the happy path

**Goal**: the same chat handles the houses that phase 1 turned away or simplified: a house with a
ventilation system, a day zone and a night zone, a real heating system (gas boiler or air-water heat
pump with radiators), and the renovation the user talked about. The result explains where the heat
goes (a heat-loss split per element), what it costs and what it emits, and compares the house before
and after the renovation. Everything stays local, and every number in an explanation still comes
from Python.

**Scope on purpose**

| In phase 2 | Deferred |
| --- | --- |
| Ventilation: natural, mechanical extract (system C), balanced with heat recovery (system D) | Demand-controlled ventilation, summer night cooling |
| Zoning: one zone, or a day zone and a night zone split by storey | User-named rooms, more than two zones |
| Heating: ideal (when unknown), non-condensing gas boiler, condensing gas boiler, air-water heat pump, all on radiators | Floor heating, hybrid systems, domestic hot water, PV |
| Renovation overlay: the renovation facts become a second scenario, simulated after the baseline and compared | Several renovation packages, cost of the works, payback |
| Heat-loss split per element (steady-state estimate from the U-values, areas and air flows) | Per-element split read from the result file (trano follow-up T11) |
| Energy cost and CO2 from configured prices and factors | Tariffs by time of day, primary energy, EPC labels |
| 15 eval cases, the reference house as the gate | 30 to 50 cases, model A/B, LLM judge (phase 3) |
| Apartments still answer "not supported yet" | Apartments |

**Gate (end of phase 2)**: trano's residential reference house (`examples/residential_before_renovation.yaml`,
a 1960s semi-detached house with a gas boiler and radiators) described in plain words in the chat, with
the deep renovation of `residential_after_renovation.yaml` described as plans, goes through confirmation,
two simulations and one explanation. The heating need before and after is compared with the numbers in
trano's `docs/validation/residential_reference_cases.md` (130 and 17.6 kWh/m2 of heat from the radiators,
Buildings): the chat must reproduce the direction and the order of magnitude of the reduction, and the
difference is explained (six zones against two, window placement, the exact radiators). The explanation
names the drivers with their share of the heat loss, the cost and the CO2 of each scenario, and every
number passes the guardrail.

**How to run this file with Claude Code**: `Read CLAUDE.md and docs/phase-2.md. Start step 2.1 in plan mode.`
Phase 2 was built in one go on 2026-10-28 at Ando's request; what the checkpoints would have shown and
the decisions that need Ando are in [phase-2-review.md](phase-2-review.md).

---

## Step 2.0 Plan and carried-over decisions

Write this file and `docs/phase-3.md`; record in `docs/decisions.md` what was decided from the phase 1
review (ventilation default, "argon" stays low-e, KPI method stays in llmtrano, heater sizing kept,
roof levels by the midpoint rule, the trano version pin). Update `CLAUDE.md`: phases 2 and 3 are in
scope now.

## Step 2.1 Brief v2

Concepts: #23 schema design, #24 intermediate representation, #31 slot filling.

New enums and fields in `src/llmtrano/brief/model.py` (write each enum in full, one member per line):

| Enum | Members |
| --- | --- |
| `Ventilation` | natural, mechanical_extract, balanced_heat_recovery, unknown |
| `HeatingSystem` | gas_boiler_old, gas_boiler_condensing, heat_pump_air_water, unknown |
| `Zoning` | one, day_night |

```python
ventilation: Ventilation | None            # how fresh air comes in
heating_system: HeatingSystem | None       # unknown -> the ideal heater of phase 1
zoning: Zoning | None                      # one zone, or day and night zones by storey
main_window_orientation: Orientation | None  # "big windows to the south": half of the glass on that facade
```

`src/llmtrano/brief/parse.py`: one parser per field with English, Dutch and French synonyms ("systeem D",
"warmteterugwinning", "VMC double flux", "extractor fans", "condensing boiler", "condensatieketel",
"warmtepomp", "pompe à chaleur", "old boiler", "day and night zones"). "I don't know" gives `unknown` for
the enums. Required fields do not change.

Tests: enum members, parser tables, the Brief's JSON Schema still has no `$ref` inside the extraction
schema (the field list grows, the shape does not).

## Step 2.2 Defaults, planner, prompts and labels

Concepts: #27 system prompt, #29 prompt assembly, #39 workflow over agent.

| Field | Default | Basis |
| --- | --- | --- |
| ventilation | natural before 2006, mechanical_extract from 2006 (the Flemish EPB ventilation requirement) | assumption, shown on the card |
| heating_system | unknown: the ideal heater of phase 1, which gives the heating need, not the fuel use | assumption, shown on the card |
| zoning | one | assumption; the card lets the user switch to day and night zones |
| main_window_orientation | none: windows spread evenly over the exposed facades | assumption |

Planner priority gains `heating_system` after `glazing` and `ventilation` after `airtightness`; both get
answer buttons. `zoning` and `main_window_orientation` are never asked (card edits or the user's own words).
`extract.md` gets the synonyms and one example with a heating system and a ventilation system; `respond.md`
says renovation plans are simulated after the baseline; field labels in `reply.py` and the ui.

## Step 2.3 Ventilation in the compiler

Concepts: #24 compiler, "code does what must be exact".

trano's `infiltration` zone takes `ventilation_schedule` (rows of time since midnight [s] and mass flow
[kg/s], Buildings' MixedAirInf and IDEAS' ZoneScheduledVentilation) on top of the infiltration `n50 / 20`.
The compiler writes:

| Ventilation | Model | Design heat loss (EN 12831 simplified, as trano's `residential_cases.py`) |
| --- | --- | --- |
| natural | outdoor air added so that infiltration plus airing reaches 0.5 1/h (the EN 12831 minimum trano uses for the old house) | 0.34 x max(n50 / 20, 0.5) x V |
| mechanical_extract | 0.5 1/h of extracted air (replaced by outdoor air) on top of the infiltration | 0.34 x (n50 / 20 + 0.5) x V |
| balanced_heat_recovery | supply and extract ducts in every zone and a system D air handling unit with heat recovery 0.8 at 0.5 1/h, as trano's after-renovation case | 0.34 x (n50 / 20 + 0.5 x (1 - 0.8)) x V |

The air flow is `0.5 x V x 1.2 / 3600` kg/s. Tests: the schedule string, the duct and AHU entries, trano's
schema check and Modelica generation for each ventilation kind, the design loss per kind.

## Step 2.4 Day and night zones

Concepts: #24, and the plan's "zoning strategy".

`geometry.py` builds zones: `one` is phase 1; `day_night` gives a day zone on the ground floor and a night
zone on the storeys above, split by storey, with an `INTERMEDIATE_FLOOR:001` internal wall between them
(trano's reference house construction). The day zone keeps the user's set points and hours; the night zone
is kept at 18 degC from 6 h to 23 h and at the night set point otherwise, as the bedrooms of trano's
reference house. The roof belongs to the night zone, the slab to the day zone. Space ids: `DAY:001`,
`NIGHT:001`; one zone keeps `HOUSE:001`.

Tests: areas per zone add up to the one-zone areas, the internal floor equals the footprint, two-zone
models pass trano's schema check and generate Modelica, the KPI sums both zones.

## Step 2.5 Hydronic heating systems

Concepts: #24, #25 validation.

For a stated heating system the compiler writes radiators with thermostatic valves (P control on the zone
set point schedule) and the hydronic loop of trano's reference cases: generator, pump, three-way valve,
temperature sensor and split valve. Only the generator and the sizing differ:

| System | Generator | Supply / return | Radiator size at 75/65/20 degC (EN 442, exponent 1.3) |
| --- | --- | --- | --- |
| gas_boiler_old | boiler `without_storage`, efficiency 0.85 (lower heating value), 1.4 x design loss, 1 kW steps | 75 / 65 degC | 1.2 x the zone's design loss |
| gas_boiler_condensing | boiler `without_storage`, efficiency 0.95, 1.4 x design loss | 60 / 50 degC | 1.2 x loss x 1.6 (so they cover the loss at 60 degC) |
| heat_pump_air_water | `air_water_heat_pump`, 1.25 x design loss in 0.5 kW steps, 100 l buffer, heating curve 40 degC at -8 degC | 40 / 35 degC | 1.2 x loss x 3.9 (low-temperature radiators) |

The radiator factor is `(LMTD at 75/65/20 / LMTD at the system's supply and return) ** 1.3`, computed, not
typed. `CompiledModel.sizing` records the design loss per zone, the radiator powers and the generator power
(they go into the KPI JSON, so the explanation may name them). Tests: the sizing numbers for one house per
system, the YAML structure against trano's reference case, schema check and Modelica generation.

## Step 2.6 KPIs v2

Concepts: #46 grounding, #24.

`llmtrano.kpi.compute` reads, per emission type:

- ideal heaters: as phase 1 (the heater's `EHea` meter);
- radiators: the heat flow `radiator_<id>.Q_flow` of each radiator integrated over the year (trapezoid on
  the hourly grid: radiators have no spikes), peak as the highest hourly mean;
- the generator: the boiler's fuel `boi.QFue_flow` (gas, lower heating value) or the heat pump's
  electricity `heaPum.P`, plus the circulation pumps' `P`. Variable names are found by pattern in the
  result file and listed in `source_variables`; a missing one is reported, not guessed.

The heat-loss split (`llmtrano.kpi.split`) is a steady-state estimate: the share of each element (walls,
windows, roof, floor, air) in the design heat loss, from the same U-values, areas and air flows the
compiler wrote. It is labelled an estimate everywhere it is shown.

Energy cost and CO2 (`llmtrano.kpi.energy`): final energy from the generator (gas or electricity; the heat
need itself when the heater is ideal, flagged as such), times the price and the emission factor from
`Settings` (`ENERGY_PRICE_GAS_EUR_PER_KWH`, `ENERGY_PRICE_ELECTRICITY_EUR_PER_KWH`, `CO2_GAS_KG_PER_KWH`,
`CO2_ELECTRICITY_KG_PER_KWH`); the values used are written into the KPI JSON so the explanation can cite
them.

Tests: a reduced real result file of trano's three-zone hydronic tutorial (radiators, boiler fuel,
pumps), the split sums to 100%, the cost and CO2 arithmetic.

**CHECKPOINT 2.6**: show the KPI JSON of one hydronic house and the split table.

## Step 2.7 The renovation overlay

Concepts: #41 human in the loop, #39 workflow, #42 task decomposition.

`src/llmtrano/brief/overlay.py`: the renovation facts (the last one per field wins) laid over the confirmed
baseline give the renovation Brief, with provenance `Source.renovation`. The card shows a renovation column
and the confirmation covers both scenarios. The workflow runs the baseline, then, when renovation facts
exist, the renovation: `compile -> check -> simulate -> kpis` once per scenario, then `compare` (Python
computes the differences in kWh, percent, EUR and kg CO2) and `interpret`. After a result, a new message
with renovation plans ("what if I insulate the roof?") goes through intake again, the card is confirmed
again and only the renovation scenario is simulated; a change to the baseline clears the results.

Tests: the overlay, the card column, a workflow run with both scenarios on fake jobs, the renovation-only
rerun after a result, `docs/workflow.md` regenerated.

## Step 2.8 Interpretation v2

Concepts: #45 hallucination, #46 grounded generation, #49 output guardrail.

`interpret.md` explains the baseline, the split (so drivers are named with their share), the cost and CO2
with the prices used, and the renovation comparison when present. The guardrail's known values are the
whole KPI JSON (both scenarios, the comparison, the split, the prices), the Brief and the assumptions. The
fallback text covers both scenarios.

## Step 2.9 Persistence and jobs per scenario

Migration: `runs.scenario` and `house_briefs.scenario` (`baseline` or `renovation`). The recorder and the
jobs carry the scenario.

## Step 2.10 API and UI

SSE: `kpis` events carry `scenario`; `GET /sessions/{id}` returns `renovation_kpis` and `comparison`. UI: the
card with its renovation column, a results table with one column per scenario, the split table and the
cost line, drawn by code.

## Step 2.11 Eval cases v1 and the gate

Five new cases: a gas boiler house, a heat pump house with day and night zones, a house with system D
ventilation, a French description, and the reference house (gate: `reference_house`, with its renovation).
`EvalCase.reference` holds the numbers of trano's validation page and the tolerance; the runner scores the
renovation scenario and writes the gate comparison.

**CHECKPOINT 2.11 (phase 2 gate)**: the reference house end to end in the ui (both scenarios), the eval
summary, the comparison with trano's reference results and the reasons for the differences, and what
failed and why. Ando decides what goes into phase 3.

## Step 2.12 Notes and review

`docs/phase-2-notes.md` (what each step does and why), `docs/phase-2-review.md` (checkpoints, decisions
for Ando), README and CLAUDE.md.
