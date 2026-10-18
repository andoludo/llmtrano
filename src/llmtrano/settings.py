"""Application settings, read from environment variables only.

Every field maps to an upper-case env var of the same name (``llm_base_url`` -> ``LLM_BASE_URL``).
Defaults are safe local values that match ``.env.example``. No ``.env`` file is read here:
Docker Compose injects the variables, and on the host you export them yourself.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    llm_base_url: str = "http://localhost:8080/v1"
    llm_default_model: str = "qwen3.5-9b"
    llm_interpret_model: str = "qwen3.6-35b-a3b"
    database_url: str = "postgresql+psycopg://llmtrano:llmtrano@localhost:5432/llmtrano"
    valkey_url: str = "redis://localhost:6379/0"
    runs_dir: Path = Path("runs")
    simulation_library: str = "Buildings"
    simulation_timeout_s: int = 1200
