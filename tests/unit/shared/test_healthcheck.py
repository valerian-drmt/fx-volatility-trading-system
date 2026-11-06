"""shared.healthcheck: the one engine healthcheck (#344, audit OPS-13 / OPS-14).

The bug it replaces: image HEALTHCHECKs did ``float(ts)`` on an ISO-8601
heartbeat and could only ever fail. The first test is that exact input.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shared.healthcheck import evaluate, parse_heartbeat

NOW = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)


def _iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat().replace("+00:00", "Z")


def test_iso_heartbeat_as_written_by_set_heartbeat_is_parsed() -> None:
    raw = b"2026-11-06T13:59:30Z"  # bytes, as redis-py returns them
    assert parse_heartbeat(raw) == NOW - timedelta(seconds=30)
    ok, reason = evaluate(heartbeat_raw=raw, now=NOW, max_age_s=60)
    assert ok, reason


def test_epoch_float_heartbeat_still_accepted() -> None:
    raw = str((NOW - timedelta(seconds=5)).timestamp())
    ok, _ = evaluate(heartbeat_raw=raw, now=NOW, max_age_s=60)
    assert ok


def test_missing_or_garbage_heartbeat_is_unhealthy() -> None:
    assert evaluate(heartbeat_raw=None, now=NOW, max_age_s=60) == (False, "no heartbeat")
    ok, reason = evaluate(heartbeat_raw="not-a-date", now=NOW, max_age_s=60)
    assert not ok and reason == "no heartbeat"


def test_stale_heartbeat_is_unhealthy_with_the_age_in_the_reason() -> None:
    ok, reason = evaluate(heartbeat_raw=_iso(timedelta(seconds=90)), now=NOW, max_age_s=60)
    assert not ok
    assert "90s" in reason and "60s" in reason


def test_tick_required_only_during_market_hours() -> None:
    hb = _iso(timedelta(seconds=5))
    # weekend / closed: heartbeat alone is enough
    ok, _ = evaluate(heartbeat_raw=hb, now=NOW, max_age_s=60,
                     tick_required=True, market_open=False, tick_present=False)
    assert ok
    # open and no tick: the engine is alive but not doing its job
    ok, reason = evaluate(heartbeat_raw=hb, now=NOW, max_age_s=60,
                          tick_required=True, market_open=True, tick_present=False)
    assert not ok and "no tick" in reason
    # open with a tick
    ok, _ = evaluate(heartbeat_raw=hb, now=NOW, max_age_s=60,
                     tick_required=True, market_open=True, tick_present=True)
    assert ok


def test_tick_age_from_remaining_ttl() -> None:
    hb = _iso(timedelta(seconds=5))
    # TTL 30 s, 10 s remaining -> written 20 s ago; max 15 s -> stale
    ok, reason = evaluate(heartbeat_raw=hb, now=NOW, max_age_s=60,
                          tick_required=True, market_open=True, tick_present=True,
                          tick_ttl_s=30, tick_key_ttl_s=10, tick_max_age_s=15)
    assert not ok and "tick stale" in reason
    # 25 s remaining -> 5 s old -> fine
    ok, _ = evaluate(heartbeat_raw=hb, now=NOW, max_age_s=60,
                     tick_required=True, market_open=True, tick_present=True,
                     tick_ttl_s=30, tick_key_ttl_s=25, tick_max_age_s=15)
    assert ok
