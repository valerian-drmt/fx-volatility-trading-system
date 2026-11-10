"""Single source of truth for the project's env-var schema.

Every service container reads the same env-var schema (REDIS_URL,
IB_HOST, IB_PORT, IB_CLIENT_ID, LOG_LEVEL, DATABASE_URL, SERVICE_NAME,
MARKET_SYMBOL). Missing required vars fail fast at startup rather than
surfacing as obscure connection errors later.

Field names are UPPERCASE — they match the env-var names verbatim and
follow the "Python module-level constant" convention used by engine
code (``settings.IB_HOST``, ``settings.REDIS_URL``).

The API (``src/api/config.py``) extends this class with HTTP-only
fields (CORS, rate limits) and exposes lowercase aliases for FastAPI
ergonomics. Engines never inherit ; they instantiate ``Settings``
directly via ``get_settings()``.

Usage :
    from shared.config import get_settings
    settings = get_settings()
    client = IBClient(host=settings.IB_HOST, port=settings.IB_PORT)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-driven settings. Values flow from env vars (see ``.env.example``)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # Service identity — stamped into every log line + Redis heartbeat key.
    SERVICE_NAME: str = Field(default="unknown")

    # Redis : shared bus + heartbeat + db-writer queue.
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # IB Gateway. Defaults target a host-side gateway ; containers
    # override ``IB_HOST=ib-gateway`` via docker-compose env.
    IB_HOST: str = Field(default="127.0.0.1")
    IB_PORT: int = Field(default=4002)
    IB_CLIENT_ID: int = Field(default=1)

    # Market data symbol — spot FX pair that market-data subscribes to via
    # reqMktData(Forex(...)). EURUSD matches the vol-engine's GARCH target.
    MARKET_SYMBOL: str = Field(default="EURUSD")

    # Database (only db_writer needs this — other services keep the default).
    DATABASE_URL: str = Field(default="")

    # Log level (structlog filtering).
    LOG_LEVEL: LogLevel = Field(default="INFO")

    # ---- cross-cutting (#333): one name each, read through a Settings -----------
    # Deployment label stamped on traces (``deployment.environment``).
    ENVIRONMENT: str = Field(default="dev")
    # Build identity, from the image build arg; /health and the deploy smoke read it.
    GIT_SHA: str = Field(default="unknown")
    # OpenTelemetry: ``1``/``true`` disables the SDK; the collector endpoint.
    OTEL_SDK_DISABLED: str = Field(default="")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(default="otel-collector:4317")
    # Server-side statement cap in ms; empty / 0 = no cap (the api sets it).
    DB_STATEMENT_TIMEOUT_MS: str = Field(default="")
    # ``1`` lets an engine run outside FX market hours (replay / debug).
    FORCE_RUN_MARKET_HOURS: str = Field(default="")
    # Shared secret on the execution engine's /internal/* routes (#300).
    EXECUTION_INTERNAL_TOKEN: str = Field(default="")
    # paper | live: what the IB session must be, and which gates arm.
    TRADING_MODE: str = Field(default="paper")
    # Comma-separated symbols the pre-trade gate and live_submit accept.
    ALLOWED_SYMBOLS: str = Field(default="EUR,M6E")
    # Price points through the touch for a marketable option limit (entry and close).
    MARKETABLE_LIMIT_BUFFER: float = Field(default=0.25)

    @property
    def otel_sdk_disabled(self) -> bool:
        return truthy(self.OTEL_SDK_DISABLED)

    @property
    def force_run_market_hours(self) -> bool:
        return truthy(self.FORCE_RUN_MARKET_HOURS)

    @property
    def db_statement_timeout_ms(self) -> int:
        raw = self.DB_STATEMENT_TIMEOUT_MS.strip()
        try:
            return max(0, int(raw)) if raw else 0
        except ValueError:
            return 0

    @property
    def allowed_symbols(self) -> tuple[str, ...]:
        return tuple(s.strip().upper() for s in self.ALLOWED_SYMBOLS.split(",") if s.strip())

    @property
    def trading_mode(self) -> str:
        return "paper" if self.TRADING_MODE.strip().lower() == "paper" else "live"


def truthy(raw: str | bool | None) -> bool:
    """``1`` / ``true`` / ``yes`` (any case) are on; empty and anything else off."""
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — one Settings instance per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the LRU cache so a test can monkeypatch env vars mid-run."""
    get_settings.cache_clear()
