import pytest

from services.market_data_engine import MarketDataEngine


class FakeMarketClient:
    def __init__(self, *, connection_state="connected", ticks=None):
        self.connection_state = connection_state
        self.ticks = ticks if ticks is not None else []
        self.process_messages_calls = 0
        self.portfolio_calls = 0

    def get_status_snapshot(self):
        return {
            "mode": "read-only",
            "env": "paper",
            "client_id": "7",
            "account": "DU12345",
        }

    def get_connection_state(self):
        return self.connection_state

    def process_messages(self):
        self.process_messages_calls += 1
        return self.ticks

    def get_portfolio_snapshot(self):
        self.portfolio_calls += 1
        return (["summary-1"], ["position-1"])


def _make_engine(client, **kwargs):
    """Create a MarketDataEngine without starting the thread."""
    engine = MarketDataEngine.__new__(MarketDataEngine)
    engine._ib = client
    engine._post_ui = lambda cb: None
    engine._interval_s = kwargs.get("interval_s", 0.1)
    engine._snapshot_interval_s = kwargs.get("snapshot_interval_s", 0.1)
    engine._stop_event = __import__("threading").Event()
    engine.latest_bid = None
    engine.latest_ask = None
    engine._risk_engine = None
    engine._no_tick_check_started_at = None
    engine._no_tick_check_count = 0
    engine._no_tick_warning_emitted = False
    engine._has_received_stream_ticks = False
    return engine


@pytest.mark.unit
def test_poll_once_connected_emits_full_payload(monkeypatch):
    client = FakeMarketClient(
        connection_state="connected",
        ticks=[{"bid": 1.1, "ask": 1.2}, "bad", {"last": 1.15}],
    )
    engine = _make_engine(client, snapshot_interval_s=0.1)

    monkeypatch.setattr("services.market_data_engine.time.monotonic", lambda: 1.0)
    payload = engine._poll_once(now=1.0, last_snapshot=0.0)

    assert payload["status"]["connection_state"] == "connected"
    assert payload["status"]["mode"] == "read-only"
    assert payload["ticks"] == [{"bid": 1.1, "ask": 1.2}, {"last": 1.15}]
    assert payload["portfolio_payload"] == {"summary": ["summary-1"], "positions": ["position-1"]}
    assert client.process_messages_calls == 1


@pytest.mark.unit
def test_poll_once_disconnected_skips_stream_and_snapshots(monkeypatch):
    client = FakeMarketClient(connection_state="disconnected", ticks=[{"bid": 1.1}])
    engine = _make_engine(client, snapshot_interval_s=0.1)

    monkeypatch.setattr("services.market_data_engine.time.monotonic", lambda: 1.0)
    payload = engine._poll_once(now=1.0, last_snapshot=0.0)

    assert payload["status"]["connection_state"] == "disconnected"
    assert payload["ticks"] == []
    assert payload["portfolio_payload"] is None
    assert client.process_messages_calls == 0
    assert client.portfolio_calls == 0


@pytest.mark.unit
def test_poll_once_updates_latest_bid_ask():
    client = FakeMarketClient(
        connection_state="connected",
        ticks=[{"bid": 1.1000, "ask": 1.1002}],
    )
    engine = _make_engine(client)
    engine._poll_once(now=1.0, last_snapshot=0.0)

    assert engine.latest_bid == 1.1000
    assert engine.latest_ask == 1.1002


@pytest.mark.unit
def test_poll_once_emits_warning_after_three_no_tick_checks():
    client = FakeMarketClient(connection_state="connected", ticks=[])
    engine = _make_engine(client)

    p0 = engine._poll_once(now=0.0, last_snapshot=0.0)
    p1 = engine._poll_once(now=2.1, last_snapshot=0.0)
    p2 = engine._poll_once(now=4.2, last_snapshot=0.0)
    p3 = engine._poll_once(now=6.3, last_snapshot=0.0)

    assert p0["messages"] == []
    assert "[INFO][market_data] no ticks received (test 1/3)." in p1["messages"]
    assert "[INFO][market_data] no ticks received (test 2/3)." in p2["messages"]
    assert any("WARN" in m for m in p3["messages"])
