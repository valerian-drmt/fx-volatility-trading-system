"""The e2e market seed (#341) writes through the real bus code: the surface
under the vol engine's key with a fresh stamp, ticks through publish_tick."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("seed_market", ROOT / "scripts" / "e2e" / "seed_market.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Redis:
    def __init__(self) -> None:
        self.sets: dict[str, tuple[str, int | None]] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets[key] = (value, ex)

    async def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, data))


def test_surface_seed_stamps_now_and_uses_the_vol_engine_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    fake = _Redis()
    monkeypatch.setattr(mod, "get_async_redis", lambda: fake)
    fixture = (ROOT / "tests/fixtures/payloads/vol_update.json").read_text(encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(fixture))
    asyncio.run(mod.seed_surface("EURUSD"))
    value, ttl = fake.sets["latest_vol_surface:EURUSD"]
    payload = json.loads(value)
    assert payload["surface"]["1M"]["atm"]["iv"] == 0.0628  # what the e2e spec asserts as 6.3
    assert payload["timestamp"] != json.loads(fixture)["timestamp"] and payload["timestamp"].endswith("Z")
    assert ttl == 600


@pytest.fixture(autouse=True)
def _isolated_tick_throttle() -> Any:
    """publish_tick keeps a per-symbol throttle at module level; reset it on
    both sides so these ticks never throttle another test's publish."""
    from bus.publisher import reset_throttle_for_tests

    reset_throttle_for_tests()
    yield
    reset_throttle_for_tests()


def test_tick_seed_goes_through_publish_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    fake = _Redis()
    monkeypatch.setattr(mod, "get_async_redis", lambda: fake)
    asyncio.run(mod.seed_ticks("EURUSD", 1.12345, 1.12347, seconds=0.05, every=0.01))
    assert "latest_spot:EURUSD" in fake.sets
    channel, data = fake.published[0]
    msg = json.loads(data)
    assert channel == "ticks" and msg["bid"] == 1.12345 and msg["ask"] == 1.12347 and msg["mid"] == 1.12346
