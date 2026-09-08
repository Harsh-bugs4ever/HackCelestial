"""Runtime configuration.

Every external dependency degrades to a local fallback so the stack always boots:
DATABASE_URL unset -> SQLite; REDIS_URL unreachable -> in-process cache and
Celery eager mode. The architecture is unchanged either way.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_name: str = "Smart Resort 360"
    resort_name: str = "Celestial Bay Resort & Spa"
    currency: str = "INR"

    # Layer 1 - data spine
    database_url: str = f"sqlite:///{(ROOT / 'resort360.db').as_posix()}"
    timescale_enabled: bool = False  # set true when DATABASE_URL points at TimescaleDB

    # Cache / broker
    redis_url: str = "redis://localhost:6379/0"

    # LLM (concierge + review summarisation). Unset -> deterministic offline stub.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"

    # Engine tuning
    forecast_horizon_days: int = 30
    total_rooms: int = 120
    rate_floor_pct: float = 0.75   # guardrails from slide 5: floor/ceiling optimizer
    rate_ceiling_pct: float = 1.60

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
