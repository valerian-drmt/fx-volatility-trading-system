"""Desk latency timers (#370, hot/cold T6): submit start to IB Submitted,
fill to the orders publish, fill to the positions publish; TTL pruning;
the request start travels api -> execution in a header."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engines.execution import latency
from shared.trace import (
    REQUEST_START_HEADER,
    bind_request_start,
    bind_trace_id,
    clear_request_start,
    trace_headers,
)


@pytest.fixture(autouse=True)
def _clean() -> None:
    latency.reset_for_tests()
    clear_request_start()


def _hist_count(name: str, **labels: str) -> float:
    from shared import observability

    h = getattr(observability, name).labels(**labels)
    return float(h._sum.get())


def test_submit_latency_is_measured_from_the_bound_request_start(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(latency.time, "time", lambda: now)
    bind_request_start(str(now - 0.8))  # the api received the click 800 ms ago
    before = _hist_count("order_submit_latency_seconds", engine="execution_engine", path="structure")

    latency.note_submit_start(("trade_order", 7))
    monkeypatch.setattr(latency.time, "time", lambda: now + 0.4)
    dt = latency.observe_submitted(("trade_order", 7))

    assert dt == pytest.approx(1.2)
    assert _hist_count("order_submit_latency_seconds", engine="execution_engine", path="structure") == pytest.approx(before + 1.2)
    assert latency.observe_submitted(("trade_order", 7)) is None, "observed once"


def test_submit_without_a_bound_start_uses_now_and_an_unknown_key_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(latency.time, "time", lambda: now)
    latency.note_submit_start(("spot_order", 3))
    monkeypatch.setattr(latency.time, "time", lambda: now + 0.25)
    assert latency.observe_submitted(("spot_order", 3), path="spot") == pytest.approx(0.25)
    assert latency.observe_submitted(("spot_order", 99), path="spot") is None


def test_fill_to_publish_on_both_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    exec_time = datetime(2026, 11, 7, 10, 0, 0, tzinfo=UTC)
    now = exec_time.timestamp() + 0.3
    monkeypatch.setattr(latency.time, "time", lambda: now)
    before_o = _hist_count("fill_to_publish_seconds", engine="execution_engine", channel="orders")
    before_p = _hist_count("fill_to_publish_seconds", engine="execution_engine", channel="positions")

    assert latency.observe_orders_publish(exec_time) == pytest.approx(0.3)
    latency.note_fill("EUUZ6 C1130", exec_time)
    latency.note_fill(None, exec_time)  # no symbol: ignored
    matched = latency.observe_positions_publish(["6EZ6", "EUUZ6 C1130", None], now=now + 0.9)

    assert matched == 1
    assert _hist_count("fill_to_publish_seconds", engine="execution_engine", channel="orders") == pytest.approx(before_o + 0.3)
    assert _hist_count("fill_to_publish_seconds", engine="execution_engine", channel="positions") == pytest.approx(before_p + 1.2)
    assert latency.observe_positions_publish(["EUUZ6 C1130"], now=now + 2) == 0, "a fill is matched once"


def test_notes_are_pruned_after_their_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = 1_700_000_000.0
    monkeypatch.setattr(latency.time, "time", lambda: t0)
    latency.note_submit_start(("trade_order", 1))
    latency.note_fill("6EZ6", t0)
    monkeypatch.setattr(latency.time, "time", lambda: t0 + latency.SUBMIT_TTL_S + 1)
    latency.note_submit_start(("trade_order", 2))  # any note prunes
    assert ("trade_order", 1) not in latency._submits and "6EZ6" not in latency._fills
    assert ("trade_order", 2) in latency._submits


def test_request_start_travels_in_the_trace_headers_and_rejects_garbage() -> None:
    bind_trace_id("abc123")
    start = bind_request_start("1700000000.250")
    assert start == pytest.approx(1_700_000_000.25)
    h = trace_headers()
    assert h["X-Trace-ID"] == "abc123" and h[REQUEST_START_HEADER] == "1700000000.250"

    bogus = bind_request_start("not-a-number")
    future = bind_request_start(str((datetime.now(UTC) + timedelta(days=1)).timestamp()))
    now = datetime.now(UTC).timestamp() + 0.05  # clock resolution slack
    assert bogus <= now and future <= now, "garbage and future stamps fall back to now"
    clear_request_start()
    assert REQUEST_START_HEADER not in trace_headers()


def test_beacon_records_only_known_channels_and_clamped_values() -> None:
    from pydantic import ValidationError

    from api.routers.metrics_beacon import BeaconBody, record_beacon

    out = record_beacon(BeaconBody(channel="positions", publish_to_screen_ms=850, receive_to_screen_ms=120))
    assert out == {"recorded": 2}
    assert record_beacon(BeaconBody(channel="account", publish_to_screen_ms=None, receive_to_screen_ms=50)) == {"recorded": 1}
    with pytest.raises(ValidationError):
        BeaconBody(channel="bogus", publish_to_screen_ms=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BeaconBody(channel="positions", publish_to_screen_ms=120_000)
