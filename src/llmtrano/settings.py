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
    api_url: str = "http://localhost:8000"  # used by the ui; the ui talks only to the api
    # Energy prices [EUR/kWh] and emission factors [kg CO2/kWh] for the cost and CO2 KPIs (phase 2). The
    # defaults are orders of magnitude for a Belgian household in 2025; Ando checks them (docs/decisions.md).
    energy_price_gas_eur_per_kwh: float = 0.10
    energy_price_electricity_eur_per_kwh: float = 0.35
    co2_gas_kg_per_kwh: float = 0.202  # natural gas, lower heating value (Flemish EPB factor)
    co2_electricity_kg_per_kwh: float = 0.20  # Belgian grid mix, order of magnitude
