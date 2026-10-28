# Phase 2 notes

What each step does, why the design needs it and which flashcard concepts it uses (`#22` = concept 22 of
the LLM Flow Flashcards). Written while building phase 2 on 2026-10-28; the open questions for Ando are
collected in `docs/phase-2-review.md`.

## Step 2.0 Plan and carried-over decisions

Phase 2 starts from the phase 1 review: the decisions that only needed good practice were taken and written
in `docs/decisions.md` (ventilation modelled, "argon" stays low-e, KPIs stay in llmtrano, the trano pin
stays at 0.56 until Ando checks the newer releases), the plan for phase 2 and phase 3 was written in the
same step and checkpoint format as the earlier phases.

## Step 2.1 Brief v2

The Brief stays the intermediate representation (#24) and grows four slots (#31): the ventilation, the
heating system, the zoning and the facade with the big windows. Each enum is closed, so the extraction
schema stays flat and the planner can offer buttons; the parsers carry the Dutch and French words people
use ("systeem D", "warmtepomp", "chaudière à condensation") with the most specific words first, so
"condensing gas boiler" does not fall through to "gas boiler". "I don't know" gives `unknown`, and for the
heating system `unknown` is a real value: the ideal heater of phase 1, which gives the heating need rather
than a fuel use.

## Step 2.2 Defaults, planner, prompts and labels

The defaults follow the same rule as phase 1: a default never replaces a user value and is shown as an
assumption with its basis. The ventilation default comes from the construction period (natural before 2006,
mechanical extract from 2006, when the Flemish EPB started requiring a ventilation system); the heating
system defaults to "not stated", the zoning to one zone. The planner (#39 workflow over agent) asks for the
heating system and the ventilation after the airtightness, with buttons; zoning and the window orientation
are never asked, because most users do not think in those terms, but the card lets them change both. The
extraction prompt gets the synonyms and a fifth example (#28 few-shot).

## Step 2.3 Ventilation in the compiler

trano's `infiltration` zone takes outdoor air from a `ventilation_schedule` on top of the infiltration, and
a system D is an air handling unit with supply and extract ducts. The compiler writes the first for natural
ventilation (airing up to 0.5 1/h in total, the EN 12831 minimum trano's old reference house uses) and for a
mechanical extract (0.5 1/h on top of the infiltration), and the second for balanced ventilation (0.5 1/h
through an exchanger of effectiveness 0.8, as trano's after-renovation case). The design heat loss now
counts the air per element, which is also where the heat-loss split comes from (#24: code does what must
be exact). Every fixture house ventilates now, so the heaters of the phase 1 snapshots grew by 1 to 1.5 kW.

## Step 2.4 Day and night zones

Two zones split by storey are the smallest zoning that changes a result: the bedrooms are kept at 18 degC
from 6 to 23 h (trano's reference house) and occupied at night, and the floor between the zones is trano's
`INTERMEDIATE_FLOOR`. The geometry module builds a `HouseGeometry` of one or two zones; the one-zone output
is byte-identical to phase 1, which the snapshots prove. The main window orientation moves half of the glass
to one facade, with the rest spread in proportion and every window capped at 80% of its wall, so a strange
input can never produce a window larger than its facade.

## Step 2.5 Hydronic heating systems

A stated heating system turns the ideal heater into radiators with thermostatic valves and the hydronic loop
of trano's reference cases (generator, pump, three-way valve, temperature sensor, split valve), copied
component by component. The one rule added is the radiator oversizing for the supply temperature: a heat
pump at 40 degC needs about four times the radiator rated at 75/65/20 degC to cover the same loss (the
EN 442 exponent 1.3 on the ratio of log mean temperature differences). It is computed, not typed, and
recorded in the sizing so the explanation can name it. All three generators pass trano's schema check and
generate Modelica without OpenModelica (`tests/test_compile.py`).

## Step 2.6 KPIs v2

The KPI reader branches on what the network holds: ideal heaters keep the phase 1 meter method; radiators
give the heat delivered to each zone, and the generator gives its fuel or electricity from the kWh meters
trano 0.56 publishes on its data bus, or from the Buildings powers when the meters are absent (#46
grounding: every variable read is listed, a missing one is reported, never guessed). The variable names
were checked on trano's stored three-zone hydronic result file, reduced to 16 variables as a fixture: the
mapping from zone to radiator follows the network, not the ids. The cost and CO2 are Python arithmetic on
settings values that are written into the KPI JSON, so the explanation can cite the prices as assumptions.
The heat-loss split is the design loss per element applied to the simulated need, labelled an estimate.

## Step 2.7 The renovation overlay

The renovation facts, kept apart since phase 1, become a second Brief laid over the confirmed baseline, with
their own provenance and a column on the card (#41 human in the loop: the user confirms both scenarios on
one card). The graph runs the same compile, check, simulate and kpis nodes once per scenario (#42 task
decomposition), then a `compare` node computes every difference the explanation may quote (#46). A Brief
confirmed once is ready again as soon as the user adds a plan after a result, and only the new scenario is
simulated; a baseline change clears the results and reruns both.

## Step 2.8 Interpretation v2

The model gets a rounded summary per scenario (the need, the system with its fuel, cost, CO2 and the prices
assumed, the split with its shares, the design figures), the renovation changes and the comparison. The
prompt tells it to name drivers with their shares, to call the split an estimate, to name the prices as
assumptions and to compute nothing itself (#45, #46, #49: the guardrail now knows both scenarios and the
comparison, so a correct percentage passes and an invented one still fails). The fallback text covers the
system and the renovation.

## Step 2.9 Persistence and jobs per scenario

`runs.scenario` and `house_briefs.scenario` (#61 versioning): a result can be traced to the scenario, the
Brief version and the trano version that produced it. The migration is hand-written in the style of the
earlier ones; a test keeps the chain at one head.

## Step 2.10 API and UI

The api adds the renovation KPIs, the comparison and the renovation run to the session state and relays the
scenario on the run and KPI events; the ui draws the renovation column, a results table with one column per
scenario and the saving, the cost and CO2 lines and the split table, all by code (#62: the numbers on screen
never depend on the LLM).

## Step 2.11 Eval cases v1 and the gate

Five cases cover the new ground (#55 golden examples): a condensing boiler with mechanical extract, a heat
pump with day and night zones, system D, a French description, and the reference house with its deep
renovation as the gate. A case can carry a published reference with its source; the runner checks the
simulated need per m2 against it and writes a gate table (#58 eval gate). The reference house compiles to a
two-zone gas boiler house (design loss 12.1 kW, 17 kW boiler) and a two-zone heat pump house after
renovation (2.2 kW, 3 kW heat pump), against trano's six-zone 10.75 kW and 2.34 kW: the same order of
magnitude from a paragraph of text.
