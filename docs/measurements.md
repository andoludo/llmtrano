# Measurements

Real numbers from this laptop. Every table records the date and how it was measured.

## Host (step 0.1)

Measured 2026-10-18.

| Item | Value | Source |
| --- | --- | --- |
| Laptop | HP Victus 16, Ryzen 7 5800H, 32 GB RAM (30,907 MiB visible) | `free -m` |
| GPU | NVIDIA GeForce RTX 3060 Laptop (GA106M), 6,144 MiB VRAM | `nvidia-smi`, `lspci` |
| iGPU | AMD Cezanne (Radeon Vega) | `lspci` |
| PRIME mode | on-demand | `prime-select query` |
| NVIDIA driver | 595.99.02 | `nvidia-smi` |
| Max CUDA supported by driver | 13.2 (so llama-swap tag `unified-cuda13` in step 0.4) | `nvidia-smi` |
| Docker Engine | 29.9.1 | `docker version` |
| Docker Compose | v5.6.0 (Compose v2 line, plugin) | `docker compose version` |
| NVIDIA Container Toolkit | 1.20.1 (apt repo nvidia.github.io/libnvidia-container), Docker runtime configured, CDI spec in `/etc/cdi/nvidia.yaml` | `nvidia-ctk --version`, apt |
| GPU visible in Docker | Yes: RTX 3060 Laptop, 6,144 MiB, driver 595.99.02, CUDA 13.2 | `docker run --rm --gpus all ubuntu:24.04 nvidia-smi` |
| Free disk on `/` (holds `/var/lib/docker`) | 351 GB of 468 GB | `df -h` |
| Host RAM available at idle (desktop session open) | 10,916 MiB, swap in use 1,545 MiB | `free -m` |

## Benchmark (step 0.5)

Written by `uv run python -m llmtrano.bench`. One subsection per run, newest last.

### Run 2026-10-18 13:00

Baseline flags. Prompts were longer than intended here (83 / 767 / 3,092 tokens); fixed before the next runs.

Median of 3 runs, streaming, max_tokens 256, thinking off. Cold = unique nonce at prompt start (nothing cached); warm = same prompt again.

| Model | Flags | Load + first reply s | VRAM MiB | llm container mem | Host available MiB |
| --- | --- | --- | --- | --- | --- |
| qwen3.5-9b | `-ngl 28 -c 32768 -np 2 -fa on -ctk q8_0 -ctv q8_0 --jinja` | 7.7 | 5269 | 9.523GiB | 17126 |
| qwen3.6-35b-a3b | `-ngl 99 --n-cpu-moe 36 -c 16384 -np 1 -fa on -ctk q8_0 -ctv q8_0 --jinja` | 20.0 | 4385 | 14.02GiB | 17878 |

| Model | Prompt | Mode | Prompt tok | Cached tok | TTFT s | TTFT range s | Decode tok/s | Server prefill tok/s | Server decode tok/s | Gen tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.5-9b | short (~50 tok) | cold | 83 | 0 | 0.35 | 0.34-0.43 | 24.1 | 309 | 24.1 | 256 |
| qwen3.5-9b | short (~50 tok) | warm | 83 | 80 | 0.18 | 0.17-0.18 | 24.1 | 39 | 24.1 | 256 |
| qwen3.5-9b | medium (~500 tok) | cold | 767 | 0 | 0.96 | 0.93-0.97 | 24.0 | 897 | 24.0 | 256 |
| qwen3.5-9b | medium (~500 tok) | warm | 767 | 763 | 0.11 | 0.11-0.12 | 24.0 | 37 | 24.0 | 256 |
| qwen3.5-9b | 2k system prefix | cold | 3092 | 0 | 3.42 | 3.36-3.42 | 23.3 | 950 | 23.3 | 256 |
| qwen3.5-9b | 2k system prefix | warm | 3092 | 3087 | 0.12 | 0.12-0.12 | 23.2 | 37 | 23.2 | 256 |
| qwen3.6-35b-a3b | short (~50 tok) | cold | 84 | 0 | 1.87 | 1.82-5.28 | 39.2 | 46 | 39.2 | 256 |
| qwen3.6-35b-a3b | short (~50 tok) | warm | 84 | 80 | 0.21 | 0.20-0.22 | 37.7 | 35 | 37.7 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | cold | 767 | 0 | 4.79 | 4.75-5.18 | 39.6 | 165 | 39.6 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | warm | 767 | 763 | 0.12 | 0.12-0.12 | 39.1 | 34 | 39.1 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | cold | 3093 | 0 | 13.77 | 13.62-13.79 | 39.0 | 230 | 38.9 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | warm | 3093 | 3088 | 0.13 | 0.13-0.16 | 38.9 | 33 | 38.9 | 256 |

### Run 2026-10-18 13:15

Tuned flags. The MoE still used mmap here: two requests right after load returned 502 EOF and were retried.

Median of 3 runs, streaming, max_tokens 256, thinking off. Cold = unique nonce at prompt start (nothing cached); warm = same prompt again.

| Model | Flags | Load + first reply s | VRAM MiB | llm container mem | Host available MiB |
| --- | --- | --- | --- | --- | --- |
| qwen3.5-9b | `-ngl 28 -c 32768 -np 2 -fa on -ctk q8_0 -ctv q8_0 --jinja` | 8.6 | 5269 | 4.332GiB | 17614 |
| qwen3.6-35b-a3b | `-ngl 99 --n-cpu-moe 35 -b 2048 -ub 2048 -c 16384 -np 1 -fa on -ctk q8_0 -ctv q8_0 --jinja` | 23.0 | 5031 | 16.14GiB | 18141 |

| Model | Prompt | Mode | Prompt tok | Cached tok | TTFT s | TTFT range s | Decode tok/s | Server prefill tok/s | Server decode tok/s | Gen tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.5-9b | short (~50 tok) | cold | 46 | 0 | 0.33 | 0.32-0.44 | 24.0 | 178 | 24.0 | 256 |
| qwen3.5-9b | short (~50 tok) | warm | 46 | 41 | 0.18 | 0.18-0.18 | 23.9 | 37 | 23.9 | 256 |
| qwen3.5-9b | medium (~500 tok) | cold | 502 | 0 | 0.62 | 0.61-0.63 | 23.8 | 929 | 23.8 | 256 |
| qwen3.5-9b | medium (~500 tok) | warm | 502 | 499 | 0.12 | 0.12-0.12 | 23.9 | 36 | 23.9 | 256 |
| qwen3.5-9b | 2k system prefix | cold | 2067 | 0 | 2.44 | 2.38-2.47 | 23.5 | 901 | 23.5 | 256 |
| qwen3.5-9b | 2k system prefix | warm | 2067 | 2062 | 0.12 | 0.12-0.12 | 23.4 | 37 | 23.4 | 256 |
| qwen3.6-35b-a3b | short (~50 tok) | cold | 46 | 0 | 1.62 | 1.55-3.13 | 38.6 | 32 | 38.6 | 256 |
| qwen3.6-35b-a3b | short (~50 tok) | warm | 46 | 42 | 0.85 | 0.25-0.88 | 37.3 | 30 | 37.3 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | cold | 501 | 0 | 2.66 | 2.62-3.89 | 40.2 | 196 | 40.2 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | warm | 501 | 497 | 0.12 | 0.12-0.12 | 39.7 | 34 | 39.6 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | cold | 2067 | 0 | 3.74 | 3.72-3.87 | 40.0 | 581 | 40.0 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | warm | 2067 | 2063 | 0.13 | 0.13-0.13 | 39.9 | 34 | 39.9 | 256 |

### Run 2026-10-18 13:20

Final MoE flags (`--load-mode none`). The 9B final numbers are in the 13:15 run (its flags did not change).

Median of 3 runs, streaming, max_tokens 256, thinking off. Cold = unique nonce at prompt start (nothing cached); warm = same prompt again.

| Model | Flags | Load + first reply s | VRAM MiB | llm container mem | Host available MiB |
| --- | --- | --- | --- | --- | --- |
| qwen3.6-35b-a3b | `-ngl 99 --n-cpu-moe 35 -b 2048 -ub 2048 -c 16384 -np 1 --load-mode none -fa on -ctk q8_0 -ctv q8_0 --jinja` | 33.4 | 5061 | 18.38GiB | 1681 |

| Model | Prompt | Mode | Prompt tok | Cached tok | TTFT s | TTFT range s | Decode tok/s | Server prefill tok/s | Server decode tok/s | Gen tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3.6-35b-a3b | short (~50 tok) | cold | 43 | 0 | 1.30 | 1.30-1.34 | 35.6 | 37 | 35.6 | 256 |
| qwen3.6-35b-a3b | short (~50 tok) | warm | 43 | 42 | 0.21 | 0.21-0.24 | 38.2 | 33 | 38.2 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | cold | 502 | 0 | 2.53 | 2.53-2.56 | 37.5 | 208 | 37.5 | 256 |
| qwen3.6-35b-a3b | medium (~500 tok) | warm | 502 | 498 | 0.16 | 0.15-0.77 | 36.3 | 28 | 36.3 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | cold | 2065 | 0 | 3.63 | 3.62-3.67 | 39.0 | 595 | 39.0 | 256 |
| qwen3.6-35b-a3b | 2k system prefix | warm | 2065 | 2061 | 0.13 | 0.13-0.15 | 38.3 | 33 | 38.3 | 256 |

### Reading (2026-10-18)

SLOs from the plan: warm TTFT under 1.5 s, decode 20 tok/s or more.

| Model | Warm TTFT | Decode | Cold TTFT, 2k prefix | SLOs |
| --- | --- | --- | --- | --- |
| qwen3.5-9b, `-ngl 28` | 0.12 to 0.18 s | 23.4 to 24.0 tok/s | 2.44 s | both met, decode with little margin |
| qwen3.6-35b-a3b, `--n-cpu-moe 35` | 0.13 to 0.21 s | 36 to 39 tok/s | 3.63 s | both met |

- Prefix caching (#16): a cached 2k prefix cuts TTFT from 2.44 s to 0.12 s (9B) and from 3.63 s to 0.13 s (MoE).
  "Warm" here repeats the exact prompt, so only about 4 new tokens are prefilled.
- The MoE pays about 1.3 s for any uncached batch of 32 tokens or more (short cold prompt: 43 tokens, 1.30 s),
  while the 9B pays 0.33 s. A real chat turn adds new user tokens, so expect the MoE's realistic warm TTFT
  near 1.3 s plus, close to the SLO. Likely cause: expert weights in RAM are streamed to the GPU for large
  batches (`--op-offload`, default on); not yet tested.
- The MoE decodes faster than the 9B (about 38 vs 24 tok/s): only about 3B parameters are active per token (#2),
  while 5 of the 9B's 33 layers run on the CPU because the full model does not fit in 6 GB.
- RAM: with the MoE loaded (17.8 GB resident), host available RAM dropped to 1.7 GB with the desktop session
  open (about 11 GB in use before the stack). With the 9B loaded, 17.6 GB is available. A simulation next to
  the MoE will not fit unless the desktop frees memory: step 0.7 measures this.

## Simulation and RAM under load (step 0.7)

Tutorial house (`tests/fixtures/first_model.yaml`, trano `0.52.0+4220d3c`, Buildings, OpenModelica 1.26.9),
run through db + queue + worker with `python -m llmtrano.jobs.smoke`. Hourly output points.
Duration is end to end in trano (`simulate_yaml`); the split is OpenModelica's own `simulate()` record.
Peak RAM is sampled about every 3 s with `docker stats` and `free -m`.

| Date | Run | Status | Duration (s) | Result file | OpenModelica split (s) | Peak worker RAM (MiB) | Min host available (MiB) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-18 | 1 day | succeeded | 26.1 | 2.89 MB | frontend 1.5, backend 5.5, simcode 0.8, templates 2.1, compile 4.5, simulation 0.4, total 14.7 | - | - | |
| 2026-10-18 | 1 year (8,760 points) | succeeded | 40.1 | 50.42 MB | frontend 1.5, backend 5.4, simcode 0.7, templates 1.0, compile 4.2, simulation 20.3, total 33.0 | 3,684 | 12,772 | no model loaded |
| 2026-10-18 | 1 year, MoE load attempted | succeeded | 46.1 | 50.42 MB | total 35.0 | 3,695 | 14,474 | invalid as a "MoE loaded" test: the MoE load was OOM-killed before the run (see below) |
| 2026-10-18 | 1 year, MoE loaded | succeeded | 39.9 | 50.42 MB | frontend 1.4, backend 5.3, simcode 0.8, templates 0.9, compile 4.0, simulation 18.9, total 31.3 | 3,954 | 2,895 | VM closed; llm peak 17,562 MiB; peak host used 28,012 MiB of 30,907; no OOM kill; MoE answered in 0.23 s afterwards |

- About 11 s of each run is outside OpenModelica's record: trano's YAML to Modelica generation and `omc`
  loading Modelica, IDEAS and Buildings. Translation plus C compilation is about 10 to 14 s; simulating a
  day takes 0.4 s and a year 20 s.
- **MoE plus a simulation fits only with a light desktop.** With the MoE loaded (17.6 GB) and a year
  simulation running (3.95 GB peak), host RAM in use peaked at 28.0 GB of 30.9 GB, leaving 2.9 GB. The
  simulation was as fast as without the model (39.9 s vs 40.1 s).
- With a VirtualBox VM open (6.3 GB) the kernel's global OOM killer killed `llama-server` while it loaded
  the MoE (15:01), and swap (8 GB) filled up. Budget: desktop about 7 GB + MoE 17.6 GB + worker 4 GB = about
  29 GB of 30.9 GB. The 9B model needs about 5 GB of host RAM, so 9B plus a simulation leaves about 15 GB free.
- Worker `mem_limit` lowered from 8 GB to 6 GB (peak 3.95 GB, about 50% headroom).

## Phase 0 gate (step 0.10)

Measured 2026-10-18, commit `6f4003a`.

- Fresh `git clone`, `cp .env.example .env`, `docker compose --profile gpu up -d --build`: every service
  healthy, `model-fetch` exited 0. 3 min 49 s with Docker's build cache and the existing `models` volume
  (a first build on a new machine adds about 10 minutes for the worker image and the 28 GB download).
- From the ui: `qwen3.5-9b` answered; a one-year run of the tutorial house succeeded (`end_s` 31,536,000,
  43.4 s, 50.4 MB result file, trano `0.52.0+4220d3c`); `qwen3.6-35b-a3b` answered a follow-up with the
  conversation history (3 messages).

## Summary for the phase 1 default model

| | `qwen3.5-9b` (`-ngl 28`) | `qwen3.6-35b-a3b` (`--n-cpu-moe 35`) |
| --- | --- | --- |
| Decode | 23 to 24 tok/s | 36 to 40 tok/s |
| Warm TTFT (same prompt) | 0.12 to 0.18 s | 0.13 to 0.21 s |
| Uncached 43-token input | 0.33 s | 1.30 s |
| Cold 2k prefix | 2.44 s | 3.63 s |
| Load (swap to it) | 8.6 s | 33 s |
| Host RAM | about 4 to 5 GB | 17.6 GB |
| Host RAM left during a year simulation | about 15 GB | 2.9 GB (desktop light, no VM) |
| JSON schema extraction test (step 0.4) | valid, but `insulation: none` for "roof insulated, walls bare" | valid, `insulation: partial` (correct) |


## trano 0.56.0 check (step 1.0)

Tutorial house (`first_model.yaml`, unchanged in trano 0.56.0), OpenModelica 1.26.9, local runtime, one output
point per hour. Measured 2026-10-19 in a 4 vCPU cloud sandbox, not the laptop: durations are about 2 to 3 times the
step 0.7 numbers and are not comparable with them.

| Library | Period | Status | Duration (s) | Phases (T6, seconds from start) |
| --- | --- | --- | --- | --- |
| Buildings 13.0.0 | 1 day | succeeded | 75.8 | generating 0.0, compiling 3.8, simulating 63.6, done 75.8 |
| IDEAS 3.0.0 | 1 day | succeeded | 62.8 | generating 0.0, compiling 0.5, simulating 56.3, done 62.8 |
| Buildings 13.0.0 | 1 year | succeeded | 115.2 | generating 0.0, compiling 0.5, simulating 60.3, done 115.2 |
| IDEAS 3.0.0 | 1 year | succeeded | 79.3 | generating 0.0, compiling 0.4, simulating 50.7, done 79.3 |

KPIs for one year (`trano.kpi.building_kpis`, 100 m2):

| Library | Heating (kWh) | kWh/m2 | Peak (W) | Hours below set point | `EHea` at the end (kWh) |
| --- | --- | --- | --- | --- | --- |
| Buildings | 6,085.5 | 60.9 | 3,862 | 274.0 | 5,995.7 |
| IDEAS | 5,271.6 | 52.7 | 3,804 | 224.7 | 5,193.5 |

- The trapezoid on the hourly grid reads 1.5% above the heater's own energy meter `EHea`. For one winter week
  (Buildings): hourly grid 238.15 vs 236.54 kWh (+0.68%), 1-minute grid 236.44 vs 236.48 kWh (-0.02%). Peak
  power for that week: 3,355 W hourly, 3,384 W on the 1-minute grid.
- Buildings and IDEAS differ by 13% on the same YAML (different zone, wall and window models). Phase 1 uses
  Buildings throughout, so its numbers are only compared with Buildings runs.

## Heater sizing and the KPI method (step 1.4 and 1.10)

One-year runs of the three compiler fixtures (`tests/test_compile.py`), Buildings, hourly output grid, cloud
sandbox (4 vCPU). "trapezoid" is `trano.kpi` (power integrated on the output grid), "meter" is the heater's
own `EHea` integrator (what llmtrano reports).

With trano's default capacity (1 MW):

| Fixture | trapezoid (kWh) | meter (kWh) | Peak on the grid (W) |
| --- | --- | --- | --- |
| detached, before 1946, 140 m2 | 103,105 | 21,696 | 506,062 |
| semi-detached, 1980s, 120 m2 | 40,202 | 6,462 | 302,315 |
| terraced, renovated, 110 m2 | 31,365 | 1,681 | 478,422 |

With the heater sized at 1.4 times the design heat loss (step 1.4):

| Fixture | Capacity (W) | meter (kWh) | kWh/m2 | trapezoid vs meter | Peak, hourly mean (W) | Peak on the grid (W) |
| --- | --- | --- | --- | --- | --- | --- |
| detached, before 1946 | 22,500 | 21,691 | 154.9 | +4.5% | 10,825 | 17,318 |
| semi-detached, 1980s | 8,000 | 6,469 | 53.9 | +0.4% | 4,335 | 4,715 |
| terraced, renovated | 3,000 | 1,678 | 15.3 | -2.8% | 2,138 | 1,741 |

- The 1 MW heater spikes to hundreds of kW for minutes after each set point step; the hourly grid catches some
  spikes and misses others, so the trapezoid is 5 to 19 times the meter. Sizing the heater does not change the
  annual energy (meter within 0.2%) and makes the peak meaningful.
- Even sized, the trapezoid stays off by -2.8% to +4.5%: the KPI reads the meter, and the peak is the highest
  hourly mean power from the meter, which does not depend on the output grid.

## Phase 1 gate comparison (step 1.14)

The gate case's Brief, as a perfect extraction would fill it (`evals/cases/tutorial_house.yaml`), compiled and
simulated for one year, against `first_model.yaml` simulated directly; then one difference at a time put back
to the tutorial's value. Buildings, same Uccle weather file, KPIs from the heater's meter.

| Run | Heating (kWh) | kWh/m2 | Peak, hourly mean (W) | Change vs the chat Brief (kWh) |
| --- | --- | --- | --- | --- |
| `first_model.yaml` directly | 5,996 | 60.0 | 3,113 | |
| tutorial house through the chat Brief | 3,417 | 34.2 | 2,782 | |
| + the tutorial's glazing (argon, no low-e coating) | 5,222 | 52.2 | 3,371 | +1,805 |
| + the tutorial's infiltration (0.5 ach instead of n50 6, 0.3 ach) | 4,478 | 44.8 | 3,191 | +1,061 |
| + the tutorial's walls, roof, floor and glazing | 5,603 | 56.0 | 3,039 | +2,186 |
| + the tutorial's windows (12 m2 south, 4 m2 north) | 2,929 | 29.3 | 2,646 | -488 |
| + the tutorial's occupancy (7-9 h and 18-23 h, 4.2 people) | 3,463 | 34.6 | 2,635 | +46 |
| + the tutorial's 8 kW heater | 3,418 | 34.2 | 2,836 | +1 |

The chat Brief needs 43% less heat than the tutorial model. The one-at-a-time effects add up to 6,222 kWh,
close to the tutorial's 5,996 (the rest is interaction). In order:

1. **Glazing interpretation (+1.8 MWh)**: "double glazing with argon" maps to `double_low_e` (Uw 1.17). The
   tutorial's glass has no low-e coating (infrared absorptivity 0.84 on both faces), so its windows lose about
   twice as much.
2. **Ventilation input (+1.1 MWh)**: the user did not state the airtightness, so the default `average`
   (n50 6, 0.3 ach) applies; the tutorial has 0.5 ach. Neither model has a ventilation system.
3. **Constructions (+0.4 MWh beyond the glazing)**: 16 cm of roof insulation parses as `medium` (12 cm of
   mineral wool, U 0.25), the tutorial's flat roof is about U 0.2; the floor and wall build-ups differ too.
4. **Window placement (-0.5 MWh)**: the heuristic spreads 20.4 m2 of windows over four facades (20%), the
   tutorial has 16 m2, 12 of them south. Less glass loses less heat here than the lost south gains.
5. Occupancy and heater capacity: negligible.

The same Brief run through the ui (stub LLM, real stack) gave 3,417 kWh, identical to the direct run.
