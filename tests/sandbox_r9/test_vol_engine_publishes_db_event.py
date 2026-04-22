"""Tests for VolEngine.run_cycle — must also publish on db_events.

After each successful publish_vol_update + heartbeat, the engine must
fan the same surface onto the db_events channel so the db-writer can
persist it into vol_surfaces. Without this hop, /api/v1/vol/smile
returns 404 because Postgres is empty.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")


@pytest.mark.asyncio
async def test_run_cycle_publishes_db_event_with_vol_surfaces_table() -> None:
    from services.vol.engine import VolEngine

    # Capture what redis.publish was called with.
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"1.17")

    async def async_fetch(_F):
        # Engine expects iterable of (delta, iv, strike) triples per tenor ;
        # _compute_surface routes through interpolate_delta_pillars to build
        # the final {label: {iv, strike}} nested shape stored in Redis.
        return {
            "1M": [
                (0.10, 0.075, 1.22),
                (0.25, 0.068, 1.19),
                (0.50, 0.065, 1.17),
                (0.75, 0.067, 1.15),
                (0.90, 0.073, 1.13),
            ]
        }

    engine = VolEngine(
        ib=MagicMock(),
        redis=redis,
        symbol="EURUSD",
        ib_host="ib-gateway", ib_port=4002, client_id=2,
        fetch_fop_chain=async_fetch,
        fetch_ohlc=lambda: None,
    )

    ok = await engine.run_cycle()
    assert ok is True

    # redis.publish was called several times (for the pub/sub fanout and for
    # the db_events frame). Find the db_events one.
    publish_calls = [call.args for call in redis.publish.await_args_list]
    db_calls = [args for args in publish_calls if args and args[0] == "db_events"]
    assert len(db_calls) == 1, f"expected 1 db_events publish, got {db_calls}"
    channel, frame = db_calls[0]
    payload = json.loads(frame)
    assert payload["table"] == "vol_surfaces"
    row = payload["payload"]
    assert row["underlying"] == "EURUSD"
    assert row["spot"] == pytest.approx(1.17)
    assert "surface_data" in row
    assert "1M" in row["surface_data"]
