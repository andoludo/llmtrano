# Phase 2 review

Phase 2 was built in one go (steps 2.0 to 2.12) on 2026-10-28, as Ando asked, together with phase 3. This
file collects what the checkpoints would have shown and every decision that needs Ando. What each step does
is in [phase-2-notes.md](phase-2-notes.md); technical decisions are in [decisions.md](decisions.md).

## Where it was built and what was verified

Built in a cloud sandbox without Docker, OpenModelica or a GPU. So:

- **Verified without OpenModelica**: every combination the compiler can write (three ventilation kinds, one or
  two zones, four heating systems, the renovation overlay) passes trano's schema check and trano's YAML to
  Modelica translation (`convert_network`); the KPI reader runs on a real result file of trano's three-zone
  hydronic tutorial (reduced to the 16 variables it reads); the workflow, api and ui run both scenarios with
  fake jobs and a fake LLM; 537 fast tests pass, ruff, ruff format and `mypy --strict` are clean.
- **Not run**: any simulation (the `simulation` and `compose` tests), the live LLM tests, the eval set, the
  worker and app image builds. The KPI variable names of the heat pump (`EEle`, `PEle`) and of trano 0.56's
  boiler meters (`EFue`, `EHea`) come from trano's templates, not from a result file: the stored tutorial
  result predates them, so the reader falls back to the Buildings powers there and the test covers that path.

## To run on the laptop

```bash
docker compose --profile gpu up -d --build        # the worker image changes (new compiler output only)
uv run pytest -m simulation                       # check_yaml on every fixture house, now with ventilation
uv run pytest -m compose                          # the stack test
uv run python -m llmtrano.evals.run               # 15 cases, the reference house as the gate
```

Then open http://localhost:7860, describe the reference house (`evals/cases/reference_house.yaml` has the
paragraph) and confirm: two runs, one explanation with the comparison. Keep the screenshots for the gate.

## Checkpoint 2.6: the KPI JSON of a hydronic house

From the reduced tutorial result (14 days, a gas boiler on three radiators; the tutorial's control barely
heats, so the numbers are small, the structure is the point):

```json
{"heating_energy_kwh": -7.2, "heated_floor_area_m2": 139.0,
 "system": {"kind": "default", "fuel": "gas", "gas_kwh": 421.1, "generator_heat_kwh": 329.6,
            "auxiliary_electricity_kwh": 0.16, "final_energy_kwh": 421.1},
 "source_variables": {"space_001.heating_power": "radiator_003.Q_flow",
                      "generator.gas": "boiler_001.boi.QFue_flow (trapezoid)",
                      "auxiliary_electricity": "boiler_001.pumBoi.P (trapezoid) + pump_001.pumRad.P (trapezoid)"}}
```

The split for a 1980s semi-detached house (120 m2, two zones, natural ventilation): walls 26%, windows 34%,
roof 12%, floor 7%, air 20% of a 5.7 kW design loss.

## Checkpoint 2.11: the gate

| Gate item | Status |
| --- | --- |
| The reference house goes through confirmation, two simulations and one explanation | Built; runs end to end with fake jobs; not simulated here |
| Heating need before and after against trano's 130 and 17.6 kWh/m2 | Pending the laptop run; the sizing already agrees in order of magnitude (12.1 kW and 2.2 kW design loss against trano's 10.75 and 2.34) |
| Drivers named with their shares, cost and CO2 per scenario | Built: the split, the cost and the CO2 are in the KPI JSON; the prompt asks for them |
| Every number passes the guardrail | Enforced: the comparison and the split are known values |

**Expected differences with trano's reference results**, to explain when the numbers come in: two zones
instead of six (the bathroom at 22 degC and the bedrooms at 18 degC are averaged into one night zone at
18 degC); the radiators are generic (1.2 times the zone loss) instead of trano's per-room sizes; the windows
follow the window-to-wall ratio instead of the measured 21.1 m2; the natural ventilation is modelled as
0.5 1/h in total (trano's old house has infiltration only, n50 12, 0.6 1/h); the ground temperature is
trano's default 10 degC instead of the ISO 13370 value.

## Decisions for Ando

1. **Energy prices and CO2 factors** (`Settings`): gas 0.10 EUR/kWh, electricity 0.35 EUR/kWh, 0.202 kg
   CO2/kWh for gas (lower heating value) and 0.20 kg/kWh for electricity. Orders of magnitude for 2025;
   check them against your contract and the EPB factors, and set them in `.env`.
2. **trano version**: PyPI has 0.65.0 (llmtrano pins 0.56); FastSmoothBuildings became a default library
   since. Bumping needs the worker image rebuilt and the measurements redone.
3. **Condensing boiler**: constant efficiency 0.95 on the lower heating value at 60/50 degC (an assumption;
   trano's reference has only the non-condensing 0.85). A temperature-dependent curve is possible with
   `effCur`.
4. **Natural ventilation as 0.5 1/h in total**: the EN 12831 minimum trano's old house uses for sizing. The
   alternative is infiltration only (phase 1), which under-estimates most houses.
5. **Night zone at 18 degC from 6 to 23 h**: trano's bedrooms. The user cannot change it yet; a
   `night_zone_setpoint_c` field is a small addition if the eval shows people ask for it.
6. **Radiator oversizing by supply temperature** (1.0, 1.6, 3.9): the physics is right, the practice varies
   (many heat pump installs keep undersized radiators and run warmer). Keep, and say it in the explanation.
7. **Heat-loss split as a steady-state estimate**: honest and testable; the simulated per-element split needs
   the Buildings surface heat flows (trano follow-up T11). Decide whether to request it in trano.
8. **Ventilation default by period** (natural before 2006, extract after): a rule of thumb; TABULA or EPB
   statistics would be better. The TABULA Belgian tables could not be fetched here.
9. **trano follow-ups T9 to T12** in `docs/trano-changes.md`: file them or drop them.

## Candidates for phase 3 and later

Phase 3 (built in the same session) covers the eval set at scale, the A/B comparison, the LLM judge, the
grounded references, observability and hardening. Beyond it: a user-set night zone temperature, domestic hot
water, PV (trano has it), floor heating, costs of the works and payback, apartments.
