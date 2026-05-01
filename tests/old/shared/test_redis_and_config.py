"""Smoke tests for ``shared.config`` and ``shared.db_events``.

Redis client tests previously here covered the ``shared.redis_client``
shim that delegated to ``bus.redis_client``. The shim was removed when
``bus`` became the single source of truth (see ``src/bus/client.py``).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from shared.config import Settings, get_settings, reset_settings_cache
from shared.db_events import DB_EVENTS_CHANNEL, publish_db_event


# ── config / settings ─────────────────────────────────────────────────

@pytest.mark.unit
def test_settings_reads_env_overrides(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "vol_engine")
    monkeypatch.setenv("IB_CLIENT_ID", "2")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    reset_settings_cache()

    s = get_settings()
    assert s.SERVICE_NAME == "vol_engine"
    assert s.IB_CLIENT_ID == 2
    assert s.LOG_LEVEL == "WARNING"


@pytest.mark.unit
def test_settings_defaults_when_env_unset(monkeypatch):
    for var in ("SERVICE_NAME", "IB_CLIENT_ID", "LOG_LEVEL", "IB_HOST", "IB_PORT"):
        monkeypatch.delenv(var, raising=False)
    reset_settings_cache()

    s = Settings()
    assert s.IB_HOST == "127.0.0.1"
    assert s.IB_PORT == 4002
    assert s.IB_CLIENT_ID == 1
    assert s.LOG_LEVEL == "INFO"


# ── db_events publisher ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_db_event_frames_json_with_table_and_payload():
    redis = AsyncMock()
    redis.publish = AsyncMock(return_value=2)  # pretend 2 subscribers

    subscribers = await publish_db_event(
        redis, table="account_snaps", payload={"net_liquidation": 42.0}
    )

    assert subscribers == 2
    redis.publish.assert_awaited_once()
    channel, body = redis.publish.call_args.args
    assert channel == DB_EVENTS_CHANNEL
    parsed = json.loads(body)
    assert parsed == {"table": "account_snaps", "payload": {"net_liquidation": 42.0}}
