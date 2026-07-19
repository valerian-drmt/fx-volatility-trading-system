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
from typing import Literal

from pydantic import Field, model_validator

from shared.config import Settings as _BaseSettings

# The committed dev fallback for AUTH_SECRET. Usable locally, rejected in prod
# by the boot guard below.
_INSECURE_SECRET = "dev-insecure-change-me"


class Settings(_BaseSettings):
    """API Settings — engines never instantiate this class."""

    # Deployment environment. "prod" arms the fail-fast boot guard below;
    # anything else keeps the zero-setup local/CI behaviour.
    ENV: Literal["dev", "prod"] = Field(default="dev")

    # HTTP-layer fields (no engine equivalent).
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"]
    )
    rate_limit_per_minute: int = Field(default=100)

    # Auth boundary (single-trader). Reads stay public; writes require a valid
    # HMAC cookie (see api/auth.py). Prod sets these from SSM; locally the empty
    # password hash keeps login closed until AUTH_PASSWORD_HASH is provided.
    # UPPERCASE = env-var name (case_sensitive); lowercase views are below.
    AUTH_SECRET: str = Field(default="dev-insecure-change-me")   # HMAC key for the cookie token
    AUTH_USERNAME: str = Field(default="trader")
    AUTH_SALT: str = Field(default="fxvol")
    AUTH_PASSWORD_HASH: str = Field(default="")                  # pbkdf2_hmac(sha256) hex
    AUTH_COOKIE_SECURE: bool = Field(default=True)               # set false only for local HTTP dev
    AUTH_TTL_SECONDS: int = Field(default=43200)                 # 12 h

    @model_validator(mode="after")
    def _prod_boot_guard(self) -> Settings:
        # Fires inside get_settings() during create_app() import: a prod
        # container with a forgeable HMAC key never serves a single request.
        if self.ENV == "prod":
            if self.AUTH_SECRET == _INSECURE_SECRET or len(self.AUTH_SECRET) < 32:
                raise ValueError(
                    "ENV=prod requires a strong AUTH_SECRET (>=32 chars, not the "
                    "repo default). Provision /fxvol/prod/AUTH_SECRET in SSM."
                )
            if not self.AUTH_COOKIE_SECURE:
                raise ValueError("ENV=prod requires AUTH_COOKIE_SECURE=true")
        return self

    # Lowercase aliases — read-only views of the inherited UPPERCASE fields.
    @property
    def auth_secret(self) -> str:
        return self.AUTH_SECRET

    @property
    def auth_username(self) -> str:
        return self.AUTH_USERNAME

    @property
    def auth_salt(self) -> str:
        return self.AUTH_SALT

    @property
    def auth_password_hash(self) -> str:
        return self.AUTH_PASSWORD_HASH

    @property
    def auth_cookie_secure(self) -> bool:
        return self.AUTH_COOKIE_SECURE

    @property
    def auth_ttl_seconds(self) -> int:
        return self.AUTH_TTL_SECONDS

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
