import asyncio
import queue
import threading
from unittest.mock import AsyncMock

import pytest
from redis import exceptions as redis_exc

from bus.publisher import reset_throttle_for_tests
from services.vol_engine import (
    PILLAR_TARGETS,
    VolEngine,
    _safe,
    _tenor_label,
    pillars_to_scanner_rows,
)


@pytest.mark.unit
def test_safe_handles_nan():
    assert _safe(None) is None
    assert _safe(float("nan")) is None
    assert _safe(1.5) == 1.5


@pytest.mark.unit
def test_tenor_label():
    assert _tenor_label(30) == "1M"
    assert _tenor_label(60) == "2M"
    assert _tenor_label(90) == "3M"
    assert _tenor_label(120) == "4M"
    assert _tenor_label(150) == "5M"
    assert _tenor_label(180) == "6M"


@pytest.mark.unit
def test_pillar_targets_has_5_entries():
    assert len(PILLAR_TARGETS) == 5
    assert "atm" in PILLAR_TARGETS
    assert PILLAR_TARGETS["atm"] == 0.50


@pytest.mark.unit
def test_scanner_rows_from_pillar():
    pillar = {
        "tenor_label": "3M", "expiry": "20260702", "dte": 90, "F": 1.17,
        "sigma_ATM_pct": 7.50, "RR25_pct": -0.38, "BF25_pct": 0.28,
        "sigma_fair_pct": 7.84, "ecart_pct": 0.34, "signal": "CHEAP", "RV_pct": 7.87,
    }
    rows = pillars_to_scanner_rows([pillar])
    assert len(rows) == 1
    r = rows[0]
    assert r["tenor"] == "3M"
    assert r["sigma_mid_pct"] == 7.50
    assert r["signal"] == "CHEAP"
    assert r["RR25_pct"] == -0.38


@pytest.mark.integration
def test_vol_engine_runs_scan():
    """Integration test — requires IB Gateway running on port 4002."""
    out_q = queue.Queue()
    engine = VolEngine(output_queue=out_q, client_id=14)
    engine.start()

    result = out_q.get(timeout=120)
    while result.get("type") == "vol_status":
        result = out_q.get(timeout=120)
    engine.stop()
    engine.join(timeout=5)

    assert result["type"] == "vol_result"
    if result.get("error"):
        pytest.skip(f"Vol engine error: {result['error']}")
    assert len(result["scanner_rows"]) > 0


# --- R3 PR #5 : Redis bus wiring --------------------------------------------



def _vol_engine_with_mock_redis():
    reset_throttle_for_tests()
    e = VolEngine.__new__(VolEngine)
    e._output_queue = queue.Queue()
    e._host = "127.0.0.1"
    e._port = 4002
    e._client_id = 2
    e._stop_event = threading.Event()
    e._risk_engine = None
    e._symbol = "EURUSD"
    e._redis_url = None
    e._loop = asyncio.new_event_loop()
    e._redis_client = AsyncMock()
    e._redis_client.set = AsyncMock(return_value=True)
    e._redis_client.publish = AsyncMock(return_value=1)
    return e


def _vol_result_sample():
    return {
        "type": "vol_result",
        "spot": 1.0857,
        "pillar_rows": [
            {
                "tenor_label": "1M", "dte": 30,
                "sigma_ATM_pct": 7.5, "sigma_fair_pct": 7.4,
                "ecart_pct": 0.1, "signal": "CHEAP", "RV_pct": 7.6,
            },
            {
                "tenor_label": "3M", "dte": 90,
                "sigma_ATM_pct": 8.0, "sigma_fair_pct": 7.9,
                "ecart_pct": 0.1, "signal": "FAIR", "RV_pct": 8.1,
            },
        ],
    }


@pytest.mark.unit
class TestVolEngineRedisWiring:
    def test_vol_engine_writes_surface_and_signals(self):
        e = _vol_engine_with_mock_redis()
        try:
            e._publish_vol_to_redis(_vol_result_sample())
        finally:
            e._loop.close()

        # 2 SET : latest_vol_surface + latest_signals, both for EURUSD.
        assert e._redis_client.set.call_count == 2
        keys_written = [args[0] for args, _ in e._redis_client.set.call_args_list]
        assert "latest_vol_surface:EURUSD" in keys_written
        assert "latest_signals:EURUSD" in keys_written
        # 1 PUBLISH on vol_update.
        assert e._redis_client.publish.call_count == 1
        assert e._redis_client.publish.call_args[0][0] == "vol_update"

    def test_vol_engine_redis_unavailable_does_not_crash(self):
        e = _vol_engine_with_mock_redis()
        e._redis_client.set = AsyncMock(
            side_effect=redis_exc.ConnectionError("down")
        )
        try:
            # ConnectionError must be swallowed by the helper.
            e._publish_vol_to_redis(_vol_result_sample())
        finally:
            e._loop.close()

    def test_vol_engine_skips_on_error_result(self):
        e = _vol_engine_with_mock_redis()
        try:
            e._publish_vol_to_redis({"error": "Market closed"})
        finally:
            e._loop.close()
        assert e._redis_client.set.call_count == 0

    def test_vol_engine_heartbeat_writes_canonical_key(self):
        e = _vol_engine_with_mock_redis()
        try:
            e._set_heartbeat_to_redis()
        finally:
            e._loop.close()
        assert e._redis_client.set.call_args[0][0] == "heartbeat:vol_engine"
