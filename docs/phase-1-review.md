# Phase 1 review

Phase 1 was built in one go (steps 1.0 to 1.14) without stopping at the checkpoints, as Ando asked on
2026-10-20. This file collects what the checkpoints would have shown and every decision that needs Ando.
What each step does and why is in [phase-1-notes.md](phase-1-notes.md); technical decisions are in
[decisions.md](decisions.md); numbers are in [measurements.md](measurements.md).

## Where it was built and what was verified

Built in a cloud sandbox: 4 vCPU, no GPU, Docker without access to `ghcr.io` blobs or OpenModelica's library
server. So:

- **Verified on the real stack** (Postgres 17, Valkey 8, the worker with OpenModelica 1.26.9, Buildings
  13.0.0 and IDEAS 3.0.0 cloned from their GitHub tags): migrations up, down and up with no drift; the Postgres
  checkpointer surviving a restart; `check_model` and one-year simulations through the queue; progress relayed
  from Valkey; KPIs, Briefs, facts, messages and feedback stored; the api and the Gradio ui driven end to end
  with Playwright (screenshots in [screenshots/phase-1](screenshots/phase-1/)).
- **With a stub instead of the LLM**: no model could run here. The workflow, api and ui tests use a fake LLM;
  the screenshots use a scripted OpenAI-compatible stub. The extraction fixtures are hand-written in the
  model's output format.
- **Not run**: the live LLM tests (`-m llm`), the eval set (`python -m llmtrano.evals.run`) and the worker and
  app image builds (`ghcr.io` was blocked; the Dockerfiles changed only the trano module name).

Test status at the last commit: 435 fast tests pass; `-m simulation` (3) and `-m compose` without `llm` (4)
pass on the sandbox stack; ruff, ruff format and `mypy --strict` clean.

## To run on the laptop

```bash
docker compose --profile gpu up -d --build        # api and worker images change (new dependencies)
uv run pytest -m llm                              # 5 live extraction sentences + a session message
uv run python -m llmtrano.evals.run               # the eval set, default model (writes evals/results/)
uv run python -m llmtrano.evals.run --model qwen3.6-35b-a3b
uv run pytest -m llm tests/test_extraction.py     # then replace tests/fixtures/extraction.yaml by recordings
```

Then open http://localhost:7860 and describe the tutorial house (the gate case's opening is in
`evals/cases/tutorial_house.yaml`) for the screen recording of checkpoint 1.14.

## Checkpoint 1.0 (trano T6, T7, T8)

Shown on 2026-10-19: trano 0.56.0 from PyPI has all three; variable names confirmed from real result files;
KPI JSON and readable errors in [measurements.md](measurements.md) and [decisions.md](decisions.md).

## Checkpoint 1.4: the tutorial house as a Brief vs `first_model.yaml`

The Brief of the gate case (what a perfect extraction gives) compiles to (abridged; ids from the compiler):

```yaml
spaces:
- id: HOUSE:001
  variant: infiltration
  parameters: {floor_area: 100.0, average_room_height: 2.5, n50: 6.0}
  occupancy: {parameters: {occupancy: 3600*{7, 23}, heat_gain_if_occupied: 2/100}}
  emissions:
  - ideal_heating_cooling:
      id: HEATING:001
      parameters:
        heating_setpoint_schedule: '[0, 291.15; 25200, 291.15; 25200, 294.15; 82800, 294.15; 82800, 291.15; 86400, 291.15]'
        maximum_heating_power: 4500.0
        maximum_cooling_power: 0.0
  external_boundaries:
    external_walls:   # 30.62 m2 N and S, 20.41 m2 E and W: EXTERNAL_WALL_GOOD:001; 100 m2 ROOF_MEDIUM:001 (ceiling)
    floor_on_grounds: # 100 m2 GROUND_FLOOR_GOOD:001, perimeter 40.82 m
    windows:          # 6.12 m2 N and S, 4.08 m2 E and W: WINDOW_DOUBLE_LOW_E:001, frame fraction 0.25
```

| Aspect | `first_model.yaml` | Compiled from the Brief |
| --- | --- | --- |
| Space | `LIVING:001`, 100 m2, 2.5 m | `HOUSE:001`, 100 m2, 2.5 m |
| Air | `ach: 0.5` | `n50: 6` (airtightness not stated: `average`), 0.3 ach |
| Footprint | 12.5 x 8 m | 12.25 x 8.16 m (aspect 1.5) |
| Walls | brick 9 + insulation 12 + block 14 + plaster (U about 0.26) | EPS 12 + cavity brick wall (U 0.23) |
| Roof | flat roof, 16 cm insulation on 15 cm concrete (U about 0.2) | ceiling under a cold attic, 12 cm mineral wool (`medium`, U 0.25) |
| Floor | 8 cm insulation under 15 cm concrete | concrete, 8 cm PUR, screed, tile |
| Windows | 12 m2 south, 4 m2 north, frame fraction 0.1 (default) | 20.4 m2 over four facades (ratio 0.2), frame fraction 0.25 |
| Glazing | 4-16-4 argon, no low-e, frame 1.4 | 4-16-4 argon, low-e on face 3, frame 1.4 (Uw 1.17) |
| Occupancy | 7-9 h and 18-23 h, trano's default 0.042 people/m2 (4.2) | 7-23 h, 2 people |
| Heating | 21 / 18 degC, 7-23 h, 8 kW | the same schedule, 4.5 kW (sized) |
| Cooling | 4 kW above 26 degC | off |
| Weather | trano's default (the same Uccle TMYx file) | Uccle TMYx, explicit |
| Ground | 10 degC (default) | 10 degC (default) |

## Checkpoint 1.14 (the phase 1 gate)

| Gate item | Status |
| --- | --- |
| The tutorial house goes through confirmation, simulation and explanation | Done in the ui on the real stack with a stub LLM (screenshots); live LLM pending |
| The stated facts land in the Brief with 100% field accuracy | Pending: the eval run with the live LLM (the parsers give 100% on the case's phrases) |
| Every number in the explanation matches the KPI JSON | Enforced by the guardrail (retry, then fallback); live faithfulness pending |
| Annual heating compared with `first_model.yaml`, difference explained | Done: 3,417 vs 5,996 kWh (-43%), decomposed below |

**Heating need through the chat vs `first_model.yaml`** (perfect extraction, measurements.md has the table):
the chat Brief needs 3,417 kWh, the tutorial model 5,996 kWh. One difference at a time: the glazing reading
(argon taken as low-e) +1,805 kWh, the default airtightness (0.3 vs 0.5 ach) +1,061 kWh, the other
constructions +381 kWh, window placement -488 kWh; occupancy and heater size do not matter.

**What failed or is unverified, and why**:

- The live LLM has not run once in phase 1: field accuracy, turns to confirmation, number faithfulness and
  latency are unmeasured. The eval runner and its metrics are unit-tested with a scripted fake LLM.
- Plausibility: the fixture results look low for Belgian houses (54 kWh/m2 for a 1980s semi-detached house),
  mostly because no ventilation beyond infiltration is modelled (decision 3).
- The worker and app images were not rebuilt (blocked registry), only reproduced by hand in the sandbox.

## Decisions for Ando

Ordered by impact. Each has what was built and a recommendation.

1. **Ventilation.** Built: infiltration only, `n50 / 20` (0.3 ach for `average`). Belgian houses ventilate
   for hygiene (system A or C, typically 0.3 to 0.5 ach more), the tutorial uses 0.5 ach, and the gate
   comparison shows +1.1 MWh from this alone. Recommendation: add a ventilation default in phase 1 (for example
   trano's `ventilation_schedule` or a total `ach` of 0.5 for houses without a heat recovery unit), shown as
   an assumption.
2. **"Argon" means low-e.** Built: "double glazing with argon" parses as `double_low_e`. Real argon double
   glazing almost always has a low-e coating, but trano's tutorial glass does not, which is the largest gate
   difference (+1.8 MWh). Recommendation: keep the mapping (it matches real houses) and explain the difference
   in the gate, or add an "argon without coating" level if the gate must match the tutorial.
3. **KPI method.** Built: llmtrano reads energy from the heater's `EHea` meter and the peak as the highest
   hourly mean, instead of `trano.kpi`'s trapezoid (off by -2.8% to +4.5% with sized heaters, 5 to 19 times
   with 1 MW). Recommendation: move this into `trano.kpi` (a T7 follow-up) and let llmtrano call it.
4. **Heater capacity.** The plan said trano's default capacity; built: 1.4 times a simplified design heat loss
   (trano's boiler rule), because the 1 MW default made the peak meaningless (300 to 500 kW). Recommendation:
   keep; revisit when real systems arrive in phase 2.
5. **Occupancy.** Built: occupants present from the day start to the day end (7-23 h) with trano's per-person
   gains. Recommendation: keep for phase 1; the gate shows it is negligible for heating.
6. **Roof insulation levels.** 16 cm parses as `medium` (12 cm library level) because the level boundary is
   halfway to `good` (22 cm). Recommendation: keep the midpoint rule, or switch to "nearest level by U-value".
7. **Graph change**: respond runs before the confirmation interrupt (the plan's graph had no reply when the
   Brief became ready). Recommendation: keep.
8. **Corrections**: a correction in the chat becomes a conflict that is asked back (as the plan says), while
   an edit on the card replaces the value at once. Recommendation: keep; reconsider for explicit chat
   corrections ("sorry, I meant") if the correction eval case shows an extra turn is annoying.
9. **Answer buttons skip the LLM** (a structured answer) and an optional field asked twice is not asked again.
   Recommendation: keep both.
10. **Guardrail scope**: kW and MWh are accepted when 1000 times the number matches; numbers in the assumption
    lines count as known. Recommendation: keep; tighten if the eval shows invented numbers getting through.
11. **Extraction fixtures** are hand-written. Recommendation: record real outputs on the first live run and
    replace them.
12. **trano issues** to file: the KPI trapezoid (3); pydantic serializer warnings from numpy `float32` in
    `convert_network`; the dict default package name `model` (a Modelica keyword); no `py.typed`.

## Candidates for phase 2 (from what phase 1 showed)

- Ventilation and heat recovery (decision 1), then the renovation overlay using the stored renovation facts.
- Per-element heat-loss split, so the explanation can name drivers with numbers instead of "likely".
- Construction-period defaults from TABULA instead of the rule of thumb.
- Window placement from the user ("big windows to the south") instead of an even split.
