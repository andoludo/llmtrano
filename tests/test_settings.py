from pathlib import Path

import pytest
from pydantic import ValidationError

from llmtrano.settings import Settings

ENV_VARS = [
    "LLM_BASE_URL",
    "LLM_DEFAULT_MODEL",
    "LLM_INTERPRET_MODEL",
    "DATABASE_URL",
    "VALKEY_URL",
    "RUNS_DIR",
    "SIMULATION_LIBRARY",
    "SIMULATION_TIMEOUT_S",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = Settings()
    assert settings.llm_base_url == "http://localhost:8080/v1"
    assert settings.simulation_library == "Buildings"
    assert settings.simulation_timeout_s == 1200
    assert settings.runs_dir == Path("runs")


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm:8080/v1")
    monkeypatch.setenv("RUNS_DIR", "/runs")
    monkeypatch.setenv("SIMULATION_TIMEOUT_S", "60")
    settings = Settings()
    assert settings.llm_base_url == "http://llm:8080/v1"
    assert settings.runs_dir == Path("/runs")
    assert settings.simulation_timeout_s == 60


def test_invalid_timeout_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMULATION_TIMEOUT_S", "not-a-number")
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_lists_every_field() -> None:
    env_example = Path(__file__).parent.parent / ".env.example"
    keys = {
        line.split("=", 1)[0]
        for line in env_example.read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert {name.upper() for name in Settings.model_fields} <= keys
