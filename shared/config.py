"""Pydantic Settings shared by every R7 service container.

Each service reads the same env-var schema (REDIS_URL, IB_HOST, IB_PORT,
IB_CLIENT_ID, LOG_LEVEL, DATABASE_URL, SERVICE_NAME). Missing required
vars fail fast at startup rather than surfacing as obscure connection
errors later.

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

    # IB Gateway. Defaults mirror the host-side PyQt config so the same env
    # file works in both modes — containers override IB_HOST=ib-gateway.
    IB_HOST: str = Field(default="127.0.0.1")
    IB_PORT: int = Field(default=4002)
    IB_CLIENT_ID: int = Field(default=1)

    # Database (only db_writer needs this — other services keep the default).
    DATABASE_URL: str = Field(default="")

    # Log level (structlog filtering).
    LOG_LEVEL: LogLevel = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — one Settings instance per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the LRU cache so a test can monkeypatch env vars mid-run."""
    get_settings.cache_clear()
