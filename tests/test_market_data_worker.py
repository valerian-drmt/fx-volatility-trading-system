import pytest

from services.market_data_worker import MarketDataWorker


class FakeMarketClient:
    def __init__(self, *, connection_state="connected", ticks=None):
        self.connection_state = connection_state
        self.ticks = ticks if ticks is not None else []
        self.process_messages_calls = 0
        self.open_orders_calls = 0
        self.fills_calls = 0
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

    def get_open_orders_snapshot(self):
        self.open_orders_calls += 1
        return ["open-order"]

    def get_fills_snapshot(self):
        self.fills_calls += 1
        return ["fill-1"]

    def get_portfolio_snapshot(self):
        self.portfolio_calls += 1
        return (["summary-1"], ["position-1"])


@pytest.mark.unit
def test_poll_once_connected_emits_full_payload(monkeypatch):
    client = FakeMarketClient(
        connection_state="connected",
        ticks=[{"bid": 1.1, "ask": 1.2}, "bad", {"last": 1.15}],
    )
    worker = MarketDataWorker(
        ib_client=client,
        interval_ms=50,
        snapshot_interval_ms=100,
    )

    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: 1.0)
    payload = worker.poll_once()

    assert payload["status"]["connection_state"] == "connected"
    assert payload["status"]["mode"] == "read-only"
    assert payload["ticks"] == [{"bid": 1.1, "ask": 1.2}, {"last": 1.15}]
    assert payload["orders_payload"] == {"open_orders": ["open-order"], "fills": ["fill-1"]}
    assert payload["portfolio_payload"] == {"summary": ["summary-1"], "positions": ["position-1"]}
    assert client.process_messages_calls == 1
    assert client.open_orders_calls == 1


@pytest.mark.unit
def test_poll_once_connected_uses_cached_snapshots_only(monkeypatch):
    """Worker must only use cached snapshot reads (no active IB requests)."""
    client = FakeMarketClient(
        connection_state="connected",
        ticks=[{"bid": 1.1, "ask": 1.2}],
    )
    worker = MarketDataWorker(
        ib_client=client,
        interval_ms=50,
        snapshot_interval_ms=100,
    )

    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: 1.0)
    payload = worker.poll_once()

    assert payload["orders_payload"] == {"open_orders": ["open-order"], "fills": ["fill-1"]}
    assert client.open_orders_calls == 1
    assert client.fills_calls == 1


@pytest.mark.unit
def test_poll_once_disconnected_skips_stream_and_snapshots(monkeypatch):
    client = FakeMarketClient(connection_state="disconnected", ticks=[{"bid": 1.1}])
    worker = MarketDataWorker(ib_client=client, snapshot_interval_ms=100)

    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: 1.0)
    payload = worker.poll_once()

    assert payload["status"]["connection_state"] == "disconnected"
    assert payload["ticks"] == []
    assert payload["orders_payload"] is None
    assert payload["portfolio_payload"] is None
    assert client.process_messages_calls == 0
    assert client.open_orders_calls == 0
    assert client.portfolio_calls == 0


@pytest.mark.unit
def test_poll_once_raises_when_exception_occurs():
    class BrokenClient(FakeMarketClient):
        def get_status_snapshot(self):
            raise RuntimeError("status failure")

    worker = MarketDataWorker(ib_client=BrokenClient())

    with pytest.raises(RuntimeError, match="status failure"):
        worker.poll_once()


@pytest.mark.unit
def test_poll_once_emits_warning_after_three_no_tick_checks(monkeypatch):
    client = FakeMarketClient(connection_state="connected", ticks=[])
    worker = MarketDataWorker(ib_client=client, snapshot_interval_ms=100)

    monotonic_values = iter([0.0, 2.1, 4.2, 6.3])
    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: next(monotonic_values))

    p0 = worker.poll_once()
    p1 = worker.poll_once()
    p2 = worker.poll_once()
    p3 = worker.poll_once()

    assert p0["messages"] == []
    assert p1["messages"] == [
        "[INFO][market_data] no ticks received (test 1/3)."
    ]
    assert p2["messages"] == [
        "[INFO][market_data] no ticks received (test 2/3)."
    ]
    assert p3["messages"] == [
        "[WARN][market_data] no ticks received (test 3/3); "
        "market may be closed or data is unavailable for this symbol."
    ]


@pytest.mark.unit
def test_poll_once_emits_info_when_tick_stream_resumes(monkeypatch):
    client = FakeMarketClient(connection_state="connected", ticks=[])
    worker = MarketDataWorker(ib_client=client, snapshot_interval_ms=100)

    monotonic_values = iter([0.0, 2.1, 4.2, 6.3, 6.4])
    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: next(monotonic_values))

    worker.poll_once()
    worker.poll_once()
    worker.poll_once()
    p3 = worker.poll_once()
    client.ticks = [{"bid": 1.1000, "ask": 1.1002}]
    p4 = worker.poll_once()

    assert p3["messages"] == [
        "[WARN][market_data] no ticks received (test 3/3); "
        "market may be closed or data is unavailable for this symbol."
    ]
    assert p4["messages"] == ["[INFO][market_data] tick stream resumed."]
    assert p4["ticks"] == [{"bid": 1.1000, "ask": 1.1002}]


@pytest.mark.unit
def test_poll_once_skips_no_tick_startup_checks_after_first_tick(monkeypatch):
    client = FakeMarketClient(connection_state="connected", ticks=[{"bid": 1.1000, "ask": 1.1002}])
    worker = MarketDataWorker(ib_client=client, snapshot_interval_ms=100)

    monotonic_values = iter([0.0, 2.1, 4.2, 6.3, 8.4])
    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: next(monotonic_values))

    p0 = worker.poll_once()  # first tick received -> startup checks disabled afterwards
    client.ticks = []
    p1 = worker.poll_once()
    p2 = worker.poll_once()
    p3 = worker.poll_once()
    p4 = worker.poll_once()

    assert p0["ticks"] == [{"bid": 1.1000, "ask": 1.1002}]
    assert p1["messages"] == []
    assert p2["messages"] == []
    assert p3["messages"] == []
    assert p4["messages"] == []
