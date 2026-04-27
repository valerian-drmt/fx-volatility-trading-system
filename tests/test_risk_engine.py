"""Unit tests for RiskEngine computation logic."""
import asyncio
import queue
import threading
from unittest.mock import AsyncMock

import numpy as np
import pytest
from redis import exceptions as redis_exc

from services.risk_engine import PNL_CHART_POINTS, RiskEngine


def _make_engine():
    """Create a RiskEngine without starting the thread."""
    e = RiskEngine.__new__(RiskEngine)
    e._output_queue = queue.Queue()
    e._host = "127.0.0.1"
    e._port = 4002
    e._client_id = 3
    e._stop_event = __import__("threading").Event()
    e.spot = 0.0
    e.iv_surface = {}
    e._positions = []
    return e


def _fut_position(qty=1, cost=1.09):
    return {
        "symbol": "6EM5", "sec_type": "FUT", "side": "BUY" if qty > 0 else "SELL",
        "qty": qty, "abs_qty": abs(qty), "strike": 0, "right": "",
        "tenor": "—", "T": 0, "multiplier": 125000, "avg_cost": cost * 125000,
        "cost_per_unit": cost,
    }


def _fop_position(qty=1, strike=1.10, right="C", tenor="1M", T=0.083, cost=0.005):
    return {
        "symbol": f"EUU {right}{strike}", "sec_type": "FOP",
        "side": "BUY" if qty > 0 else "SELL",
        "qty": qty, "abs_qty": abs(qty), "strike": strike, "right": right,
        "tenor": tenor, "T": T, "multiplier": 125000, "avg_cost": cost * 125000,
        "cost_per_unit": cost,
    }


@pytest.mark.unit
class TestCompute:
    def test_fut_delta(self):
        e = _make_engine()
        e._positions = [_fut_position(qty=2, cost=1.09)]
        result = e._compute(F=1.10)
        row = result["open_positions"][0]
        expected_delta = 2 * 1.10 * 125000
        assert abs(row["delta"] - expected_delta) < 1

    def test_fut_pnl(self):
        e = _make_engine()
        e._positions = [_fut_position(qty=1, cost=1.09)]
        result = e._compute(F=1.10)
        row = result["open_positions"][0]
        expected_pnl = (1.10 - 1.09) * 1 * 125000
        assert abs(row["pnl"] - expected_pnl) < 1

    def test_fop_greeks_present(self):
        e = _make_engine()
        e._positions = [_fop_position(qty=1, strike=1.10, right="C")]
        result = e._compute(F=1.10)
        row = result["open_positions"][0]
        assert row["delta"] is not None
        assert row["vega"] is not None
        assert row["gamma"] is not None
        assert row["theta"] is not None
        assert row["pnl"] is not None

    def test_summary_sums(self):
        e = _make_engine()
        e._positions = [_fut_position(qty=1, cost=1.09), _fut_position(qty=-1, cost=1.11)]
        result = e._compute(F=1.10)
        s = result["summary"]
        rows = result["open_positions"]
        assert abs(s["delta_net"] - sum(r["delta"] for r in rows)) < 1
        assert abs(s["pnl_total"] - sum(r["pnl"] for r in rows)) < 1

    def test_fop_uses_fallback_iv(self):
        """Without IV surface, uses 7% fallback."""
        e = _make_engine()
        e.iv_surface = {}
        e._positions = [_fop_position()]
        result = e._compute(F=1.10)
        row = result["open_positions"][0]
        assert row["iv_now_pct"] == 7.0

    def test_unknown_sec_type(self):
        e = _make_engine()
        e._positions = [{"sec_type": "BOND", "qty": 1, "abs_qty": 1, "strike": 0,
                         "right": "", "tenor": "—", "T": 0, "multiplier": 1000,
                         "cost_per_unit": 100, "symbol": "X", "side": "BUY"}]
        result = e._compute(F=1.10)
        row = result["open_positions"][0]
        assert row["delta"] is None


@pytest.mark.unit
class TestStaticPositions:
    def test_greeks_are_none(self):
        e = _make_engine()
        e._positions = [_fut_position(), _fop_position()]
        result = e._static_positions()
        for row in result["open_positions"]:
            assert row["delta"] is None
            assert row["vega"] is None
            assert row["pnl"] is None

    def test_summary_empty(self):
        e = _make_engine()
        e._positions = [_fut_position()]
        result = e._static_positions()
        assert result["summary"] == {}

    def test_basic_fields_present(self):
        e = _make_engine()
        e._positions = [_fop_position(strike=1.12, right="P", tenor="3M")]
        result = e._static_positions()
        row = result["open_positions"][0]
        assert row["qty"] == 1
        assert "1.12" in row["strike"]
        assert row["right"] == "P"
        assert row["tenor"] == "3M"


@pytest.mark.unit
class TestPnlChart:
    def test_output_shape(self):
        e = _make_engine()
        e._positions = [_fut_position()]
        result = e._compute_pnl_chart(F=1.10, iv_surface={})
        assert len(result["spots"]) == PNL_CHART_POINTS
        assert len(result["pnls"]) == PNL_CHART_POINTS

    def test_fut_only_linear(self):
        e = _make_engine()
        e._positions = [_fut_position(qty=1, cost=1.10)]
        result = e._compute_pnl_chart(F=1.10, iv_surface={})
        pnls = np.array(result["pnls"])
        # At spot=cost, PnL should be ~0
        mid_idx = PNL_CHART_POINTS // 2
        assert abs(pnls[mid_idx]) < 100  # near zero at current spot

    def test_bs_price_vec_matches_scalar(self):
        from services.bs_pricer import bs_price
        spots = np.linspace(1.05, 1.15, 11)
        vec = RiskEngine._bs_price_vec(spots, K=1.10, T=0.25, sigma=0.08, right="C")
        for i, s in enumerate(spots):
            scalar = bs_price(s, 1.10, 0.25, 0.08, "C")
            assert abs(vec[i] - scalar) < 1e-10


# --- R3 PR #5 : Redis bus wiring --------------------------------------------

def _risk_engine_with_mock_redis():
    e = RiskEngine.__new__(RiskEngine)
    e._output_queue = queue.Queue()
    e._host = "127.0.0.1"
    e._port = 4002
    e._client_id = 3
    e._stop_event = threading.Event()
    e._refresh_event = threading.Event()
    e.spot = 0.0
    e.iv_surface = {}
    e._positions = []
    e._redis_url = None
    e._loop = asyncio.new_event_loop()
    e._redis_client = AsyncMock()
    e._redis_client.set = AsyncMock(return_value=True)
    e._redis_client.publish = AsyncMock(return_value=1)
    return e


def _risk_result_sample():
    return {
        "open_positions": [{"symbol": "6EM5"}],
        "summary": {"delta_net": 1200, "vega_net": 500, "pnl_total": 250},
        "pnl_curve": {"spots": [1.08, 1.09], "pnls": [0, 100]},
        "spot": 1.0857,
    }


class TestRiskEngineRedisWiring:
    def test_risk_engine_writes_greeks_and_pnl_and_publishes(self):
        e = _risk_engine_with_mock_redis()
        try:
            e._publish_risk_to_redis(_risk_result_sample())
        finally:
            e._loop.close()

        # 2 SET : latest_greeks:portfolio + latest_pnl_curve.
        assert e._redis_client.set.call_count == 2
        keys_written = [args[0] for args, _ in e._redis_client.set.call_args_list]
        assert "latest_greeks:portfolio" in keys_written
        assert "latest_pnl_curve" in keys_written
        # 1 PUBLISH on risk_update.
        assert e._redis_client.publish.call_count == 1
        assert e._redis_client.publish.call_args[0][0] == "risk_update"

    def test_risk_engine_redis_unavailable_does_not_crash(self):
        e = _risk_engine_with_mock_redis()
        e._redis_client.set = AsyncMock(
            side_effect=redis_exc.ConnectionError("down")
        )
        try:
            e._publish_risk_to_redis(_risk_result_sample())
        finally:
            e._loop.close()

    def test_risk_engine_skips_empty_summary(self):
        e = _risk_engine_with_mock_redis()
        try:
            e._publish_risk_to_redis({"summary": {}, "pnl_curve": None})
        finally:
            e._loop.close()
        assert e._redis_client.set.call_count == 0

    def test_risk_engine_heartbeat_writes_canonical_key(self):
        e = _risk_engine_with_mock_redis()
        try:
            e._set_heartbeat_to_redis()
        finally:
            e._loop.close()
        assert e._redis_client.set.call_args[0][0] == "heartbeat:risk_engine"
