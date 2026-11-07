"""Redis contract (#327, audit 05#11 / 05#9 / 05#13).

Every key and channel name is spelled once, in ``bus/keys.py`` /
``bus/channels.py``: this test greps ``src/`` for literal spellings anywhere
else. The spot keys carry a ``{value, ts}`` envelope the one parser reads
alongside the older shapes; the mark hashes are pruned to the open set; the
vol_update payload carries the ``spot`` the position monitor reads; the
market-data engine writes ``market_status``.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3] / "src"

# A literal key template (name + ':') or a complete standalone name, quoted,
# outside bus/. Table / column words that happen to share a prefix do not
# match because they are followed by '_' or by the closing quote only.
_LITERAL = re.compile(
    r"""["'](?:f["'])?("""
    r"""(?:latest_spot|latest_bid|latest_ask|latest_vol_surface|latest_signals|latest_greeks|heartbeat|bars|"""
    r"""market_status|contract_marks|option_marks|unrealized_pnl|cycle_progress|analytics|orders):"""
    r"""|(?:latest_pnl_curve|account_snapshot|trading:halted|config:changed|db_events|vol_update|risk_update|"""
    r"""system_alerts|exit_alerts|spot_orders)["']"""
    r""")"""
)
# Lines that spell a name for another reason: stage labels, task names, comments.
_ALLOW_LINES = re.compile(r"""(#|\"\"\"|_publish_progress\(|name="db_events_subscriber"|channel: Literal|\.labels\()""")
_SKIP_DIRS = ("persistence/migrations",)


def _offenders() -> list[str]:
    out: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("bus/") or any(rel.startswith(d) for d in _SKIP_DIRS):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not _LITERAL.search(line) or _ALLOW_LINES.search(line):
                continue
            out.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    return out


def test_no_literal_redis_names_outside_bus() -> None:
    offenders = _offenders()
    assert not offenders, "use bus.keys / bus.channels:\n" + "\n".join(offenders)


def test_parse_spot_reads_every_shape_the_key_ever_had() -> None:
    from bus.spot_cache import parse_spot

    assert parse_spot(json.dumps({"value": 1.12345, "ts": "2026-11-07T10:00:00Z"})) == (1.12345, "2026-11-07T10:00:00Z")
    assert parse_spot(b'{"value": 1.1, "ts": "t"}') == (1.1, "t")
    assert parse_spot("1.0857") == (1.0857, None)
    assert parse_spot(json.dumps({"mid": 1.09, "bid": 1.08})) == (1.09, None)
    assert parse_spot(json.dumps({"value": -1.0, "ts": "t"})) == (None, "t"), "IB's -1 no-quote sentinel"
    assert parse_spot(None) == (None, None) and parse_spot("garbage") == (None, None)


class _Redis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.ttl: dict[str, int] = {}
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.kv[k] = v
        if ex:
            self.ttl[k] = ex

    async def get(self, k: str) -> Any:
        return self.kv.get(k)

    async def publish(self, ch: str, data: str) -> None:
        self.published.append((ch, json.loads(data)))

    async def hkeys(self, k: str) -> list[str]:
        return list(self.hashes.get(k, {}))

    async def hset(self, k: str, mapping: dict[str, Any]) -> None:
        self.hashes.setdefault(k, {}).update(mapping)

    async def hdel(self, k: str, *fields: str) -> None:
        for f in fields:
            self.hashes.get(k, {}).pop(f, None)

    async def expire(self, k: str, ttl: int) -> None:
        self.ttl[k] = ttl


@pytest.mark.asyncio
async def test_publish_tick_writes_timestamped_envelopes_and_vol_update_carries_spot() -> None:
    from bus import keys, publisher
    from bus.spot_cache import read_spot

    r = _Redis()
    publisher.reset_throttle_for_tests()
    ts = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    await publisher.publish_tick(r, "EURUSD", bid=1.1, ask=1.1002, mid=1.1001, timestamp=ts)
    env = json.loads(r.kv[keys.LATEST_SPOT.format(symbol="EURUSD")])
    assert env == {"value": 1.1001, "ts": "2026-11-07T10:00:00Z"}
    assert r.ttl[keys.LATEST_SPOT.format(symbol="EURUSD")] == keys.TTL_SPOT
    assert await read_spot(r, "EURUSD") == 1.1001

    await publisher.publish_vol_update(r, symbol="EURUSD", surface_data={"3M": {}}, signals_data=[], spot=1.1001, timestamp=ts)
    msg = next(m for ch, m in r.published if ch == "vol_update")
    assert msg["spot"] == 1.1001 and msg["timestamp"] == "2026-11-07T10:00:00Z"

    await publisher.set_market_status(r, "EURUSD", False, timestamp=ts)
    assert json.loads(r.kv[keys.MARKET_STATUS.format(symbol="EURUSD")]) == {"open": False, "ts": "2026-11-07T10:00:00Z"}


@pytest.mark.asyncio
async def test_mark_hashes_are_pruned_to_the_open_set() -> None:
    from bus import keys
    from engines.execution.position_sync import _replace_hash

    r = _Redis()
    key = keys.CONTRACT_MARKS.format(symbol="EUR")
    r.hashes[key] = {"51": "1.10", "52": "0.0123"}
    await _replace_hash(r, key, {"52": "0.0131", "53": "0.0040"})
    assert r.hashes[key] == {"52": "0.0131", "53": "0.0040"}, "closed position 51 left the hash"
    assert r.ttl[key] == keys.TTL_MARKS
    await _replace_hash(r, key, {})
    assert r.hashes[key] == {}


def test_db_events_channel_name_lives_in_bus_channels() -> None:
    from bus.channels import CH_DB_EVENTS
    from shared.db_events import DB_EVENTS_CHANNEL

    assert CH_DB_EVENTS == DB_EVENTS_CHANNEL == "db_events"
