"""The canonical payloads under tests/fixtures/payloads/ that `bus.publisher`
writes are checked against the publisher itself (#435). vol_update.json used to
carry a `forward` key the publisher never sent, so a consumer written against
the fixture passed its tests and failed on prod (#433 -> #434). Each test feeds
the fixture's own values through the real publisher and expects the exact
fixture back, on the channel and in the last-value key."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from tests.fixtures.payloads import payload

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.set_keys: dict[str, dict[str, Any]] = {}

    async def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, json.loads(data)))

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_keys[key] = json.loads(value)


def _ts(p: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))


async def test_ticks_fixture_is_what_publish_tick_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    from bus import channels, publisher
    monkeypatch.setattr(publisher, "_tick_last_publish_ms", {})
    p = payload("ticks")
    r = _Redis()
    assert await publisher.publish_tick(r, p["symbol"], p["bid"], p["ask"], p["mid"], timestamp=_ts(p))
    assert r.published == [(channels.CH_TICKS, p)]


async def test_vol_update_fixture_is_what_publish_vol_update_sends() -> None:
    from bus import channels, keys, publisher
    p = payload("vol_update")
    r = _Redis()
    await publisher.publish_vol_update(r, p["symbol"], p["surface"], {}, timestamp=_ts(p), spot=p["spot"])
    assert r.published == [(channels.CH_VOL_UPDATE, p)]
    assert r.set_keys[keys.LATEST_VOL_SURFACE.format(symbol=p["symbol"])] == p


async def test_risk_update_fixture_is_what_publish_risk_update_sends() -> None:
    from bus import channels, keys, publisher
    p = payload("risk_update")
    r = _Redis()
    await publisher.publish_risk_update(r, p["greeks"], timestamp=_ts(p))
    assert r.published == [(channels.CH_RISK_UPDATE, p)]
    assert r.set_keys[keys.LATEST_GREEKS_PORTFOLIO] == p
