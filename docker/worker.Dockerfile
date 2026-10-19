# Simulation worker: OpenModelica + Modelica libraries + trano + llmtrano, running trano's local runtime.
# No Docker socket: omc runs inside this container.
# The OpenModelica version is the one trano pins in ModelicaEnvironment.openmodelica_image.
FROM openmodelica/openmodelica:v1.26.9-ompython@sha256:91314c8bf3f26b11fbf8f3f3a8405be547ce3819d7eae915d988c34b7bd63a89

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The image ships Python 3.10; trano needs 3.11+, so uv provides Python 3.12.
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Libraries at build time (trano T4, now in trano-simulate): containers start in seconds and need no network.
# Installed under /root/.openmodelica, which is why the worker runs as root in phase 0.
RUN python -m trano_simulate.install_libraries

COPY alembic.ini README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV TRANO_SIMULATION_RUNTIME=local
CMD ["arq", "llmtrano.jobs.worker.WorkerSettings"]
