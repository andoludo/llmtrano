"""Smoke test of the simulation path: tutorial house, one day then one year, through db + queue + worker.

Run inside a container of the worker image, so it can read result files on the `runs` volume::

    docker compose run --rm -v ./tests/fixtures:/app/tests/fixtures:ro worker python -m llmtrano.jobs.smoke
"""

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from arq import create_pool
from arq.connections import RedisSettings
from arq.jobs import Job

from llmtrano.db.migrate import upgrade_head
from llmtrano.db.models import Run
from llmtrano.db.session import make_sessionmaker
from llmtrano.jobs.queue import enqueue_simulation
from llmtrano.settings import Settings

log = logging.getLogger("llmtrano.jobs.smoke")

DAY_S = 86_400
YEAR_S = 365 * DAY_S
# The record OpenModelica prints after simulate(): timeFrontend = 1.23, timeCompile = ..., ...
TIMING = re.compile(
    r"\btime(?P<phase>Frontend|Backend|SimCode|Templates|Compile|Simulation|Total)\s*=\s*(?P<s>[\d.eE+-]+)"
)


@dataclass(frozen=True)
class Outcome:
    label: str
    status: str
    duration_s: float | None
    result_bytes: int | None
    error: str | None
    phases: dict[str, float]


def phase_timings(log_text: str) -> dict[str, float]:
    """OpenModelica's time split (frontend, backend, compile, simulation, ...) from the simulate() record."""
    return {m.group("phase"): float(m.group("s")) for m in TIMING.finditer(log_text)}


async def run_case(label: str, yaml_text: str, end_s: float, settings: Settings) -> Outcome:
    sessions = make_sessionmaker(settings.database_url)
    with sessions() as session:
        run = Run(library=settings.simulation_library, start_s=0.0, end_s=end_s, yaml=yaml_text)
        session.add(run)
        session.commit()
        run_id = run.id
    redis = await create_pool(RedisSettings.from_dsn(settings.valkey_url))
    await enqueue_simulation(redis, run_id)
    log.info("enqueued case=%s run_id=%s end_s=%s", label, run_id, end_s)
    await Job(str(run_id), redis).result(timeout=settings.simulation_timeout_s + 120, poll_delay=2)
    await redis.aclose()

    with sessions() as session:
        run = session.get_one(Run, run_id)
        path = Path(run.result_path) if run.result_path else None
        return Outcome(
            label=label,
            status=run.status.value,
            duration_s=run.duration_s,
            result_bytes=path.stat().st_size if path and path.exists() else None,
            error=f"{run.error_kind}: {run.error_message}" if run.error_kind else None,
            phases=phase_timings(run.log_tail or ""),
        )


def report(outcomes: list[Outcome]) -> str:
    lines = [
        "| Case | Status | Duration s | Result file | OpenModelica split (s) | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for o in outcomes:
        size = f"{o.result_bytes / 1e6:.2f} MB" if o.result_bytes is not None else "-"
        split = ", ".join(f"{k} {v:.1f}" for k, v in o.phases.items()) or "-"
        duration = f"{o.duration_s:.1f}" if o.duration_s is not None else "-"
        lines.append(f"| {o.label} | {o.status} | {duration} | {size} | {split} | {o.error or '-'} |")
    return "\n".join(lines)


async def main_async(fixture: Path, cases: list[str]) -> list[Outcome]:
    settings = Settings()
    upgrade_head(settings.database_url)
    yaml_text = fixture.read_text()
    ends = {"day": float(DAY_S), "year": float(YEAR_S)}
    return [await run_case(case, yaml_text, ends[case], settings) for case in cases]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Simulate the tutorial house through the job queue.")
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/first_model.yaml"))
    parser.add_argument("--cases", nargs="+", choices=["day", "year"], default=["day", "year"])
    args = parser.parse_args()
    outcomes = asyncio.run(main_async(args.fixture, args.cases))
    sys.stdout.write(report(outcomes) + "\n")
    if any(o.status != "succeeded" for o in outcomes):
        sys.exit(1)


if __name__ == "__main__":
    main()
