from threading import RLock

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
def test_poll_once_connected_emits_full_payload(monkeypatch, qapp):
    client = FakeMarketClient(
        connection_state="connected",
        ticks=[{"bid": 1.1, "ask": 1.2}, "bad", {"last": 1.15}],
    )
    worker = MarketDataWorker(
        ib_client=client,
        io_lock=RLock(),
        interval_ms=50,
        snapshot_interval_ms=100,
    )
    worker._running = True
    payloads = []
    errors = []
    worker.payload_ready.connect(payloads.append)
    worker.failed.connect(errors.append)

    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: 1.0)
    worker._poll_once()

    assert errors == []
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["status"]["connection_state"] == "connected"
    assert payload["status"]["mode"] == "read-only"
    assert payload["ticks"] == [{"bid": 1.1, "ask": 1.2}, {"last": 1.15}]
    assert payload["orders_payload"] == {"open_orders": ["open-order"], "fills": ["fill-1"]}
    assert payload["portfolio_payload"] == {"summary": ["summary-1"], "positions": ["position-1"]}
    assert client.process_messages_calls == 1
    assert client.open_orders_calls == 1


@pytest.mark.unit
def test_poll_once_disconnected_skips_stream_and_snapshots(monkeypatch, qapp):
    client = FakeMarketClient(connection_state="disconnected", ticks=[{"bid": 1.1}])
    worker = MarketDataWorker(ib_client=client, io_lock=RLock(), snapshot_interval_ms=100)
    worker._running = True
    payloads = []
    worker.payload_ready.connect(payloads.append)

    monkeypatch.setattr("services.market_data_worker.time.monotonic", lambda: 1.0)
    worker._poll_once()

    payload = payloads[0]
    assert payload["status"]["connection_state"] == "disconnected"
    assert payload["ticks"] == []
    assert payload["orders_payload"] is None
    assert payload["portfolio_payload"] is None
    assert client.process_messages_calls == 0
    assert client.open_orders_calls == 0
    assert client.portfolio_calls == 0


@pytest.mark.unit
def test_poll_once_emits_failed_when_exception_occurs(qapp):
    class BrokenClient(FakeMarketClient):
        def get_status_snapshot(self):
            raise RuntimeError("status failure")

    worker = MarketDataWorker(ib_client=BrokenClient(), io_lock=RLock())
    worker._running = True
    errors = []
    payloads = []
    worker.failed.connect(errors.append)
    worker.payload_ready.connect(payloads.append)

    worker._poll_once()

    assert payloads == []
    assert errors
    assert "status failure" in errors[0]
