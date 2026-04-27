"""Unit tests for MarketDataEngine poll logic."""
import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from redis import exceptions as redis_exc

from bus.publisher import reset_throttle_for_tests
from services.market_data_engine import MarketDataEngine


class _FakeIB:
    def __init__(self, *, state="connected", ticks=None):
        self._state = state
        self._ticks = ticks or []
        self.portfolio_calls = 0

    def get_connection_state(self):
        return self._state

    def get_status_snapshot(self):
        return {"mode": "paper", "env": "paper", "client_id": "1", "account": "DU123"}

    def process_messages(self):
        return self._ticks

    def get_portfolio_snapshot(self):
        self.portfolio_calls += 1
        return (["summary"], ["pos1"])


class _FakeRisk:
    def __init__(self):
        self.spot = 0.0


def _make_engine(client, **kw):
    e = MarketDataEngine.__new__(MarketDataEngine)
    e._ib = client
    e._post_ui = lambda cb: None
    e._interval_s = 0.1
    e._snapshot_interval_s = kw.get("snapshot_s", 10.0)
    e._stop_event = threading.Event()
    e.latest_bid = None
    e.latest_ask = None
    e._risk_engine = kw.get("risk_engine")
    e._no_tick_check_started_at = None
    e._no_tick_check_count = 0
    e._no_tick_warning_emitted = False
    e._has_received_stream_ticks = False
    e._symbol = kw.get("symbol", "EURUSD")
    e._redis_url = None
    e._loop = None
    e._redis_client = None
    return e


@pytest.mark.unit
class TestPollOnce:
    def test_connected_returns_ticks(self):
        client = _FakeIB(ticks=[{"bid": 1.10, "ask": 1.12}])
        e = _make_engine(client)
        p = e._poll_once(now=1.0, last_snapshot=0.0)
        assert p["ticks"] == [{"bid": 1.10, "ask": 1.12}]
        assert p["status"]["connection_state"] == "connected"

    def test_disconnected_empty_ticks(self):
        client = _FakeIB(state="disconnected", ticks=[{"bid": 1.0}])
        e = _make_engine(client)
        p = e._poll_once(now=1.0, last_snapshot=0.0)
        assert p["ticks"] == []
        assert p["portfolio_payload"] is None

    def test_updates_latest_bid_ask(self):
        client = _FakeIB(ticks=[{"bid": 1.08, "ask": 1.09}])
        e = _make_engine(client)
        e._poll_once(now=1.0, last_snapshot=0.0)
        assert e.latest_bid == 1.08
        assert e.latest_ask == 1.09

    def test_writes_spot_to_risk_engine(self):
        risk = _FakeRisk()
        client = _FakeIB(ticks=[{"bid": 1.10, "ask": 1.12}])
        e = _make_engine(client, risk_engine=risk)
        e._poll_once(now=1.0, last_snapshot=0.0)
        assert risk.spot == pytest.approx(1.11)

    def test_no_spot_write_without_risk_engine(self):
        client = _FakeIB(ticks=[{"bid": 1.10, "ask": 1.12}])
        e = _make_engine(client, risk_engine=None)
        e._poll_once(now=1.0, last_snapshot=0.0)
        # Should not crash

    def test_portfolio_snapshot_on_interval(self):
        client = _FakeIB()
        e = _make_engine(client, snapshot_s=5.0)
        p = e._poll_once(now=10.0, last_snapshot=4.0)
        assert p["portfolio_payload"] is not None
        assert client.portfolio_calls == 1

    def test_no_snapshot_before_interval(self):
        client = _FakeIB()
        e = _make_engine(client, snapshot_s=10.0)
        p = e._poll_once(now=5.0, last_snapshot=1.0)
        assert p["portfolio_payload"] is None


@pytest.mark.unit
class TestNoTickWarnings:
    def test_warning_after_three_checks(self):
        client = _FakeIB(ticks=[])
        e = _make_engine(client)
        p0 = e._poll_once(now=0.0, last_snapshot=0.0)
        p1 = e._poll_once(now=2.1, last_snapshot=0.0)
        p2 = e._poll_once(now=4.2, last_snapshot=0.0)
        p3 = e._poll_once(now=6.3, last_snapshot=0.0)
        assert p0["messages"] == []
        assert any("1/3" in m for m in p1["messages"])
        assert any("2/3" in m for m in p2["messages"])
        assert any("WARN" in m for m in p3["messages"])

    def test_no_warning_after_first_tick(self):
        client = _FakeIB(ticks=[{"bid": 1.0, "ask": 1.1}])
        e = _make_engine(client)
        e._poll_once(now=0.0, last_snapshot=0.0)
        client._ticks = []
        p = e._poll_once(now=2.1, last_snapshot=0.0)
        assert p["messages"] == []


# --- R3 PR #4 : Redis bus wiring -------------------------------------------


def _engine_with_mock_redis():
    """Return an engine with a mocked aioredis client and a real event loop."""
    reset_throttle_for_tests()
    e = _make_engine(_FakeIB(ticks=[{"bid": 1.0856, "ask": 1.0858}]))
    e._redis_client = AsyncMock()
    e._redis_client.set = AsyncMock(return_value=True)
    e._redis_client.publish = AsyncMock(return_value=1)
    e._loop = asyncio.new_event_loop()
    e._symbol = "EURUSD"
    return e


@pytest.mark.unit
class TestRedisBusWiring:
    def test_market_data_engine_writes_to_redis(self):
        """A single tick payload triggers SET×3 + PUBLISH×1 on the mock Redis."""
        e = _engine_with_mock_redis()
        try:
            e._publish_ticks_to_redis({"ticks": [{"bid": 1.0856, "ask": 1.0858}]})
        finally:
            e._loop.close()

        # 3 SET calls : latest_spot, latest_bid, latest_ask.
        assert e._redis_client.set.call_count == 3
        keys_written = [args[0] for args, _ in e._redis_client.set.call_args_list]
        assert "latest_spot:EURUSD" in keys_written
        assert "latest_bid:EURUSD" in keys_written
        assert "latest_ask:EURUSD" in keys_written
        # 1 PUBLISH on the ticks channel (throttle was reset, first call wins).
        assert e._redis_client.publish.call_count == 1
        channel = e._redis_client.publish.call_args[0][0]
        assert channel == "ticks"

    def test_market_data_engine_redis_unavailable_does_not_crash(self):
        """A redis ConnectionError in publish_tick is swallowed by the helper."""
        e = _engine_with_mock_redis()
        e._redis_client.set = AsyncMock(
            side_effect=redis_exc.ConnectionError("Connection reset by peer")
        )
        try:
            # Must NOT raise even though every SET throws.
            e._publish_ticks_to_redis({"ticks": [{"bid": 1.0856, "ask": 1.0858}]})
        finally:
            e._loop.close()

    def test_publish_ticks_skips_when_no_redis_client(self):
        """If the engine was started without REDIS_URL, the method is a no-op."""
        e = _make_engine(_FakeIB())  # _redis_client stays None
        # Should not raise, should not try to build anything.
        e._publish_ticks_to_redis({"ticks": [{"bid": 1.0, "ask": 1.1}]})

    def test_publish_account_writes_snapshot_and_publishes(self):
        e = _engine_with_mock_redis()
        payload = {
            "portfolio_payload": {
                "summary": [1, 2, 3, 4, 5],
                "positions": ["pos_a", "pos_b"],
            }
        }
        try:
            e._publish_account_to_redis(payload)
        finally:
            e._loop.close()
        assert e._redis_client.set.call_count == 1
        assert e._redis_client.set.call_args[0][0] == "account_snapshot"
        assert e._redis_client.publish.call_count == 1
        assert e._redis_client.publish.call_args[0][0] == "account"

    def test_heartbeat_writes_canonical_key(self):
        e = _engine_with_mock_redis()
        try:
            e._set_heartbeat_to_redis()
        finally:
            e._loop.close()
        assert e._redis_client.set.call_count == 1
        assert e._redis_client.set.call_args[0][0] == "heartbeat:market_data"
