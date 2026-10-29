# Changes to the trano package

These changes are made in the **trano repository** (github.com/andoludo/trano), on a branch
`feat/llmtrano-runtime`, as one pull request per change group below. The llmtrano repo
installs trano from that branch (pinned commit) until a release contains them.

Baseline read for this file: trano 0.52.0, commit `75eac5b` (15 Oct 2026). Re-check every
file and line reference before editing; trano moves fast.

**Status (19 Oct 2026, step 1.0)**: T1 to T8 are merged into trano `master` (PR #55) and released
from trano 0.54.0 on; llmtrano depends on `trano>=0.56.0,<0.57` from PyPI. Since 0.56.0 the
simulation code (T1 to T4, and the result types) lives in the separate package `trano-simulate`
(PR #57); `trano.api`, `trano.kpi` and the readable errors (T8) stay in trano, and the old
`trano.simulate.*` imports are kept as aliases. New llmtrano code imports `trano_simulate`.
Open items found while checking (see `docs/decisions.md`, step 1.0): the dict default package
name `model` is still a Modelica keyword, trano has no `py.typed`, and `trano.kpi` integrates the
heating power on the output grid, which reads 1.5% high on an hourly grid.

Why these changes: today `trano.simulate.simulate()` starts a sibling OpenModelica container
through the Docker socket, hard-codes the container path `/simulation`, reuses one container
name, installs Modelica libraries at every start and returns a raw Docker `ExecResult`. That is
fine for a CLI on a laptop and wrong for a service: a worker container would need
root-equivalent access to the host, parallel runs collide, and the caller has to parse logs.

| Id | Change | Needed in | Size |
| --- | --- | --- | --- |
| T1 | Runtime abstraction: `docker` (today) and `local` (run `omc` in-process) | Phase 0 | M |
| T2 | Unique container and work directory per run | Phase 0 | S |
| T3 | Typed `SimulationResult` instead of `ExecResult` | Phase 0 | S |
| T4 | Install Modelica libraries at image build time | Phase 0 | S |
| T5 | `trano.api.simulate_yaml()` one-call Python API | Phase 0 | S |
| T6 | Phase callback (`generating`, `compiling`, `simulating`, `done`) | Phase 1 | S |
| T7 | `trano.kpi`: annual heating KPIs from a result file | Phase 1 | M |
| T8 | Readable validation errors (YAML path + field description) | Phase 1 | S |
| later | JSON Schema / LLM slices export, archetype builder, overlay helper, default variable filter, heat-loss split | Phase 2+ | |

---

## T1. Runtime abstraction (Phase 0)

**Where**: `trano/simulate/simulate.py` (`simulate`, `container`, `create_mos_file`),
`trano/topology.py` (`Network.set_weather_path_to_container_path`), `trano/utils/utils.py`
(`is_success`).

**Problem**: the `.mos` script loads `"/simulation/<model>.mo"`, and local weather files are
rewritten to `"/simulation/<file>"`. Both only exist inside the sibling container.

**Change**:

1. Add `class SimulationRuntime(StrEnum): docker = "docker"; local = "local"`, selected by a
   `runtime` argument on `simulate()` that defaults to the environment variable
   `TRANO_SIMULATION_RUNTIME`, else `docker` (so the CLI behaves exactly as today).
2. Introduce the directory as seen by `omc`: `docker` -> `/simulation`, `local` ->
   `project_path.resolve()`. Pass it to `create_mos_file(..., model_dir=...)` and use it in
   `loadFile("{model_dir}/{model_file}")`.
3. Rename `set_weather_path_to_container_path(project_path)` to
   `set_weather_path(project_path, model_dir)` and keep the old name as a thin deprecated
   wrapper. Weather paths that start with `modelica://` stay untouched, as today.
4. `local` runtime: write the `.mo` and `.mos` files into `project_path`, create
   `project_path / "results"`, then
   `subprocess.run(["omc", mos_path], cwd=results_dir, capture_output=True, text=True, timeout=timeout_s)`.
   OpenModelica writes `<model>.building_res.mat` into its working directory, so the result
   lands in `results/` where `trano.main._simulation_result_path` already expects it. No
   `find / -name "*_res.mat"` copy is needed.
5. `local` runtime must not call `configure.mos` (that is T4's job at build time). If `omc`
   is not on `PATH`, raise `OpenModelicaNotFoundError` with a message naming the variable
   `TRANO_SIMULATION_RUNTIME`.
6. Keep `docker` behaviour byte-for-byte identical for existing users.

**Tests** (in trano):
- unit, no OpenModelica: the generated `.mos` text for `local` contains the absolute
  `project_path` and no `/simulation`; for `docker` it is unchanged (snapshot test).
- unit: `set_weather_path` copies a local weather file and rewrites the path for both runtimes.
- `@pytest.mark.simulation` (runs only where `omc` exists): `tests/tutorials/first_model.yaml`
  simulates one day with `runtime="local"` and the result file exists.

## T2. Unique run directory and container name (Phase 0)

**Where**: `simulate(..., container_name: str = "openmodelica")`.

**Change**: default `container_name` to `f"trano-{uuid4().hex[:8]}"`; in `local` runtime there
is no container, but every run still gets its own `project_path` (the caller passes one; the
API in T5 creates a temporary one when none is given). Document that two runs must not share
a `project_path`.

## T3. Typed result (Phase 0)

**Change**: add in `trano/simulate/result.py`:

```python
class SimulationErrorKind(StrEnum):
    validation = "validation"
    translation = "translation"
    solver = "solver"
    timeout = "timeout"
    runtime = "runtime"

class SimulationResult(BaseModel):
    success: bool
    result_path: Path | None
    log: str                       # omc stdout + stderr, truncated to the last 20 000 characters
    duration_s: float
    error_kind: SimulationErrorKind | None = None
    error_message: str | None = None   # one human-readable line
```

`simulate()` keeps returning `ExecResult` for the `docker` runtime in this release (no breaking
change); the new API (T5) always returns `SimulationResult`. Classify errors from the log with
the markers trano already uses in `is_success` ("The simulation finished successfully",
"completed successfully" for `check_only`) plus translation errors (`Error:` lines before
`simulate(`) and timeouts (`subprocess.TimeoutExpired`).

## T4. Libraries at build time (Phase 0)

**Where**: `ModelicaEnvironment.configure_script()` and `EXTRACT_SDF` in `simulate.py`.

**Change**: add a module entry point `python -m trano.simulate.install_libraries` that writes
`configure.mos`, runs `omc configure.mos`, runs the SDF extraction script, runs `omc
configure.mos` again (same sequence as `container()` today) and exits non-zero if any of
Modelica, Buildings, IDEAS or AixLib is missing afterwards. llmtrano's worker Dockerfile
calls it once in a `RUN` step, so containers start in seconds and need no network.
Document it in `docs/getting-started/installation.md` next to `TRANO_MODELICA_LIBRARIES`.

## T5. One-call Python API (Phase 0)

**Where**: new `trano/api.py`, exported from `trano/__init__.py`.

```python
def simulate_yaml(
    model: Path | dict[str, Any],          # YAML path or an already-parsed model
    *,
    library: str = "Buildings",
    options: SimulationOptions | None = None,
    project_path: Path | None = None,      # temporary directory when None
    runtime: SimulationRuntime | None = None,
    on_phase: Callable[[str], None] | None = None,   # T6
) -> SimulationResult: ...

def check_yaml(model: Path | dict[str, Any], *, library: str = "Buildings",
               runtime: SimulationRuntime | None = None) -> SimulationResult: ...
```

Internally reuse `trano.main._create_network` logic (`Library.from_configuration` +
`convert_network`) and the simulate path from T1. A dict input is written to
`project_path/<name>.yaml` first so `convert_network` keeps one code path. Validation errors
from `convert_network` become `SimulationResult(success=False, error_kind=validation, ...)`
instead of exceptions (T8 makes their message readable).

## T6. Phase callback (Phase 1)

`on_phase` is called with `"generating"` (YAML to network and Modelica text), `"compiling"`
(before `omc`), `"simulating"` (omc started) and `"done"`. In the `local` runtime, also parse
the omc output line by line and call `on_phase("simulating")` only once; real percentage
progress is a later change (it needs OpenModelica's `-lv=LOG_STATS` or the solver time).

## T7. `trano.kpi` (Phase 1)

**Where**: new `trano/kpi.py`, using `buildingspy.io.outputfile.Reader` (already a trano dependency).

```python
class ZoneKpi(BaseModel):
    zone_id: str
    heating_energy_kwh: float
    peak_heating_w: float
    hours_below_setpoint: float | None  # only when the setpoint variable is available, else None

class BuildingKpi(BaseModel):
    period_start_s: float
    period_end_s: float
    heated_floor_area_m2: float
    heating_energy_kwh: float
    heating_energy_kwh_per_m2: float
    peak_heating_w: float
    zones: list[ZoneKpi]
    source_variables: dict[str, str]  # KPI name -> Modelica variable read (traceability)

def building_kpis(result_path: Path, network: Network) -> BuildingKpi: ...
```

Rules:
- **Do not guess variable names.** Simulate `tests/tutorials/first_model.yaml` for a few days
  with Buildings and IDEAS, list the variables with `Reader(path, "openmodelica").varNames(".*")`,
  find the heat flow of the ideal heating component and the zone air temperature, and write
  the mapping per library into `trano/kpi.py` with a comment showing how it was found.
- Energy = time integral of the heating heat flow (trapezoid on the result time grid); kWh =
  J / 3.6e6. Peak = maximum of the heat flow. Store the variable names used.
- Phase 1 supports `ideal_heating_cooling` emissions only; any other emission raises
  `NotImplementedError("kpi: only ideal heating in phase 1")`.
- Test with a stored small `.mat` fixture (one simulated day, under 1 MB).

## T8. Readable validation errors (Phase 1)

When the LinkML/Pydantic validation in `convert_network` fails, produce one line per error:
`spaces[0].parameters.floor_area: must be >= 0 (Floor area of the space [m2])`, using the slot
description from `trano_final.yaml`. llmtrano shows these lines to the LLM repair step
and, if still failing, to the user.

## Proposed after phase 2 (not started; Ando decides, see docs/phase-2-review.md)

Found while building phase 2 of llmtrano (2026-10-28). Nothing in the trano repository was changed.

| Id | Change | Why |
| --- | --- | --- |
| T9 | `trano.kpi`: energy from the ideal heater's `EHea` meter instead of the trapezoid | reads 1.5% high on an hourly grid, 5 to 19 times with spiky heaters (docs/measurements.md) |
| T10 | `trano.kpi` for radiators and generators: heat per zone from `radiator.Q_flow`, fuel and electricity from the `EFue`, `EEle`, `EHea` meters of the boiler templates, pumps' `P` | llmtrano does it in `llmtrano.kpi.compute`; it belongs next to the templates that define the variables |
| T11 | Heat-loss split per element from the result file (Buildings: `conExt[i].opa_b.Q_flow`, `conExtWin[i].glaUns_b.Q_flow`, `conBou[i].opa_b.Q_flow`, infiltration from the air ports) with the surface order documented | llmtrano gives a steady-state estimate; the simulated split would name the drivers with real numbers |
| T12 | Small fixes: `py.typed`, a non-keyword default package name for dict inputs (`model` is a Modelica keyword), the numpy `float32` serializer warnings in `convert_network`, `validate_schema` accepting an empty control mapping (`collector_control:` with no id, as the reference YAML writes it) | found in phase 1 and 2 |

## Later (not in phase 0 to 2)

- `trano schema --format json-schema` and per-class "LLM slices" with a schema hash.
- Archetype builder: `examples/residential_cases.py` generalised into `trano.archetypes`.
- Overlay helper: apply a renovation patch to a model dict.
- Default `variable_filter` for KPI variables.
