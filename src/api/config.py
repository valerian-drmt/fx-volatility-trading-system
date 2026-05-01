"""API-specific Settings — extends ``shared.config.Settings``.

Single source of truth for the env-var schema is ``shared/config.py``
(REDIS_URL, DATABASE_URL, LOG_LEVEL, IB_*, MARKET_SYMBOL, …). This
module adds HTTP-layer fields (CORS origins, rate limits) that engines
have no need for.

Naming convention :
* Engine-side code reads ``settings.REDIS_URL`` (UPPERCASE) — the
  inherited Pydantic fields, matching env-var names verbatim.
* FastAPI handlers and middleware read ``settings.redis_url``
  (lowercase) — small read-only properties below that delegate to the
  inherited UPPERCASE fields. This keeps router code idiomatic
  (``Depends`` ergonomics, lower_snake_case) without duplicating the
  env-var declaration.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from shared.config import Settings as _BaseSettings


class Settings(_BaseSettings):
    """API Settings — engines never instantiate this class."""

    # HTTP-layer fields (no engine equivalent).
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"]
    )
    rate_limit_per_minute: int = Field(default=100)

    # Lowercase aliases — read-only views of the inherited UPPERCASE fields.
    @property
    def redis_url(self) -> str:
        return self.REDIS_URL

    @property
    def database_url(self) -> str:
        # Engine default for DATABASE_URL is "" (fail-fast). For the API
        # we accept a dev fallback so http://localhost works without
        # secrets loaded — production sets DATABASE_URL via SSM.
        return self.DATABASE_URL or (
            "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
        )

    @property
    def log_level(self) -> str:
        return self.LOG_LEVEL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached factory — FastAPI injects this via ``Depends()``."""
    return Settings()
