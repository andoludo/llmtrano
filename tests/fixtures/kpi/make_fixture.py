"""Make ``first_model_buildings_2days.mat``: two simulated days of trano's tutorial house, reduced to its
heater.

Run where OpenModelica and the libraries are installed (the worker image)::

    python tests/fixtures/kpi/make_fixture.py tests/fixtures/kpi

It simulates ``llmtrano.data.tutorial_house_yaml()`` with Buildings for 2 days, one output point per hour, and
keeps the heater's power, meter, air temperature and set point on the simulation's time grid (the reduction is
the one of trano's ``tests/fixtures/result_files.py``).
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import scipy.io  # type: ignore[import-untyped]
from buildingspy.io.outputfile import Reader  # type: ignore[import-untyped]
from trano.api import simulate_yaml
from trano_simulate.simulate import SimulationOptions

from llmtrano.data import tutorial_house_yaml

PREFIX = "building.emission1.heating_001."
VARIABLES = [f"{PREFIX}{name}" for name in ("QHea_flow", "EHea", "TAir.T", "heaSch.y[1]")]


def reduce_result(source: Path, target: Path, variables: list[str]) -> None:
    reader = Reader(str(source), "openmodelica")
    time = reader.values(variables[0])[0]
    names = ["time", *variables]
    values = np.vstack([time, *(reader.values(name)[1] for name in variables)])
    width = max(map(len, names))
    scipy.io.savemat(
        str(target),
        {
            "Aclass": np.array(["Atrajectory", "1.1        ", "           ", "binTrans   "]),
            "name": np.array(["".join(name.ljust(width)[row] for name in names) for row in range(width)]),
            "description": np.array([" " * len(names)]),
            "dataInfo": np.array(
                [[0, 1, 0, -1]] + [[2, row, 0, -1] for row in range(2, len(names) + 1)], dtype=np.int32
            ).T,
            "data_1": np.array([[time[0]], [time[-1]]]),
            "data_2": values,
        },
        format="4",
    )


def main(output: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        model = Path(directory) / "first_model.yaml"
        model.write_text(tutorial_house_yaml())
        result = simulate_yaml(
            model,
            options=SimulationOptions(end_time=2 * 86400, number_of_intervals=48),
            project_path=Path(directory) / "run",
            runtime="local",
        )
        if not result.success or result.result_path is None:
            raise SystemExit(f"simulation failed: {result.error_kind} {result.error_message}")
        reduce_result(result.result_path, output / "first_model_buildings_2days.mat", VARIABLES)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
