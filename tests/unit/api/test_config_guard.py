"""Unit tests for the ENV=prod fail-fast boot guard in api.config.Settings."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import Settings

pytestmark = pytest.mark.unit

STRONG_SECRET = "x" * 32


def _settings(**kwargs) -> Settings:
    # _env_file=None isolates the test from any local .env on disk.
    return Settings(_env_file=None, **kwargs)


def test_prod_rejects_default_auth_secret():
    with pytest.raises(ValidationError, match="strong AUTH_SECRET"):
        _settings(ENV="prod", AUTH_SECRET="dev-insecure-change-me")


def test_prod_rejects_short_auth_secret():
    with pytest.raises(ValidationError, match="strong AUTH_SECRET"):
        _settings(ENV="prod", AUTH_SECRET="short-but-not-default")


def test_prod_rejects_insecure_cookie():
    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE"):
        _settings(ENV="prod", AUTH_SECRET=STRONG_SECRET, AUTH_COOKIE_SECURE=False)


def test_prod_accepts_strong_secret_and_secure_cookie():
    s = _settings(ENV="prod", AUTH_SECRET=STRONG_SECRET, AUTH_COOKIE_SECURE=True)
    assert s.ENV == "prod"
    assert s.auth_secret == STRONG_SECRET


def test_dev_default_still_constructs(monkeypatch):
    # The zero-setup local/CI path (e.g. dump_openapi imports the app with no
    # env at all) must keep working with the committed dev fallback.
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    s = _settings()
    assert s.ENV == "dev"
    assert s.AUTH_SECRET == "dev-insecure-change-me"
