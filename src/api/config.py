"""Pydantic Settings — env-driven config, shared by main + deps + middleware."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded once at startup from env + .env. All fields have safe defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
    redis_url: str = "redis://localhost:6380/0"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    rate_limit_per_minute: int = 100


def get_settings() -> Settings:
    """Cached factory — FastAPI injects this via Depends()."""
    return Settings()
