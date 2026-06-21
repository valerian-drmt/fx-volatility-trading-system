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

    # ── Auth (single-trader write boundary) ──────────────────────────────
    # Reads stay public; writes depend on require_write (valid auth cookie).
    # Prod sets these from SSM. The empty password hash default means login
    # always fails until real credentials are provisioned (no default-open).
    auth_secret: str = "dev-insecure-change-me"  # HMAC key for the cookie token
    auth_username: str = "trader"
    auth_salt: str = "fxvol"
    auth_password_hash: str = ""  # pbkdf2_hmac(sha256) hex of the password
    auth_cookie_secure: bool = True  # set false only for local HTTP dev
    auth_ttl_seconds: int = 43200  # 12 h


def get_settings() -> Settings:
    """Cached factory — FastAPI injects this via Depends()."""
    return Settings()
