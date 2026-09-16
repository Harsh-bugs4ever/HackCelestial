"""Runtime configuration.

Every external dependency degrades to a local fallback so the stack always boots:
DATABASE_URL unset -> SQLite; REDIS_URL unreachable -> in-process cache and
Celery eager mode. The architecture is unchanged either way.
"""
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

# ROOT is the backend package root - it anchors the default SQLite file, which
# lives alongside the app (backend/resort360.db). REPO_ROOT is one level up and
# is where .env actually sits. Both are checked for .env, repo root last so it
# wins, so the stack picks up configuration from either location.
ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT / ".env"), str(REPO_ROOT / ".env")),
        extra="ignore",
    )

    app_name: str = "Smart Resort 360"
    resort_name: str = "Celestial Bay Resort & Spa"
    currency: str = "INR"
    # Operating day is asked in resort local time, never the server's - see
    # app/core/clock.py for why this is separate from stored UTC timestamps.
    resort_timezone: str = "Asia/Kolkata"

    # Layer 1 - data spine
    database_url: str = f"sqlite:///{(ROOT / 'resort360.db').as_posix()}"
    timescale_enabled: bool = False  # set true when DATABASE_URL points at TimescaleDB

    # Cache / broker
    redis_url: str = "redis://localhost:6379/0"

    # LLM (concierge + review summarisation). Unset -> deterministic offline stub.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"

    # Keep-alive. A free Render instance sleeps after ~15 min idle and takes
    # ~50s to wake; a self-ping on a shorter interval keeps it warm. Render
    # injects RENDER_EXTERNAL_URL, so deployments need no configuration.
    keepalive_enabled: bool = True
    keepalive_url: str = ""
    render_external_url: str = ""
    keepalive_interval_seconds: int = 600

    # Identity. "token:Display Name:role" triples, comma-separated. Empty means
    # open demo mode; app/core/auth.py logs an error if that happens in public.
    auth_users: str = ""

    # Trust ramp. Shadow mode records approvals and their intended effect but
    # writes no artifacts, so a property can watch the system for a month
    # before letting it touch the roster or the rate card.
    shadow_mode: bool = False
    # How long an executed action stays reversible from the UI.
    undo_window_minutes: int = 30

    # Notifications. Providers are tried in order; "console" always works and
    # keeps the outbox honest in development.
    notify_channels: str = "console"
    notify_from_email: str = "ops@smart-resort-360.local"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    notify_webhook_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""          # SMS sender
    twilio_whatsapp_from: str = ""        # e.g. "whatsapp:+14155238886"
    # Where critical cards go when no specific person owns them.
    duty_manager_email: str = ""
    duty_manager_phone: str = ""

    # Cold start. Below this much history the engines publish, but the UI warns
    # that the models have not seen a full seasonal cycle yet.
    min_history_days_trusted: int = 365

    # Engine tuning
    forecast_horizon_days: int = 30
    total_rooms: int = 120
    rate_floor_pct: float = 0.75   # guardrails from slide 5: floor/ceiling optimizer
    rate_ceiling_pct: float = 1.60

    @property
    def notify_channel_list(self) -> list[str]:
        return [c.strip() for c in self.notify_channels.split(",") if c.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def keepalive_target(self) -> str:
        """The URL to self-ping, or "" when the loop should stay dormant."""
        if not self.keepalive_enabled:
            return ""
        base = (self.keepalive_url or self.render_external_url).strip().rstrip("/")
        if not base:
            return ""
        # A bare origin gets the health endpoint; an explicit path is honoured.
        return base if urlsplit(base).path else f"{base}/health"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
