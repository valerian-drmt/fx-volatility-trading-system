"""Unit tests for OrderExecutor validation and order building."""
import pytest

from services.order_executor import OrderExecutor


@pytest.mark.unit
class TestNormalizeRequest:
    def test_strips_and_uppercases(self):
        r = OrderExecutor._normalize_request({
            "symbol": " eurusd ", "side": " buy ", "order_type": " mkt ",
            "volume": "10000", "limit_price": "0",
        })
        assert r["symbol"] == "EURUSD"
        assert r["side"] == "BUY"
        assert r["order_type"] == "MKT"
        assert r["quantity"] == 10000

    def test_returns_none_for_non_dict(self):
        assert OrderExecutor._normalize_request("bad") is None
        assert OrderExecutor._normalize_request(None) is None

    def test_uses_quantity_field(self):
        r = OrderExecutor._normalize_request({
            "symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "quantity": 5000,
        })
        assert r["quantity"] == 5000


@pytest.mark.unit
class TestValidateRequest:
    def _req(self, **overrides):
        base = {
            "symbol": "EURUSD", "side": "BUY", "order_type": "MKT",
            "quantity": 10000, "limit_price": 0, "use_bracket": False,
            "take_profit_pct": None, "stop_loss_pct": None,
        }
        base.update(overrides)
        return base

    def test_valid_mkt_passes(self):
        assert OrderExecutor._validate_request(self._req()) is None

    def test_rejects_short_symbol(self):
        err = OrderExecutor._validate_request(self._req(symbol="EUR"))
        assert err is not None

    def test_rejects_bad_side(self):
        err = OrderExecutor._validate_request(self._req(side="HOLD"))
        assert err is not None

    def test_rejects_bad_order_type(self):
        err = OrderExecutor._validate_request(self._req(order_type="STOP"))
        assert err is not None

    def test_rejects_zero_quantity(self):
        err = OrderExecutor._validate_request(self._req(quantity=0))
        assert err is not None

    def test_rejects_lmt_zero_price(self):
        err = OrderExecutor._validate_request(self._req(order_type="LMT", limit_price=0))
        assert err is not None

    def test_rejects_bracket_without_tp_sl(self):
        err = OrderExecutor._validate_request(self._req(use_bracket=True))
        assert err is not None

    def test_valid_lmt_passes(self):
        err = OrderExecutor._validate_request(self._req(order_type="LMT", limit_price=1.10))
        assert err is None


@pytest.mark.unit
class TestBuildOrder:
    def test_mkt_order(self):
        o = OrderExecutor._build_order("BUY", "MKT", 10000, 0)
        assert o.action == "BUY"
        assert o.totalQuantity == 10000
        assert o.orderType == "MKT"
        assert o.tif == "GTC"

    def test_lmt_order(self):
        o = OrderExecutor._build_order("SELL", "LMT", 5000, 1.1050)
        assert o.action == "SELL"
        assert o.totalQuantity == 5000
        assert o.orderType == "LMT"
        assert o.lmtPrice == 1.1050
        assert o.tif == "DAY"


@pytest.mark.unit
class TestBracketPrices:
    def test_buy_tp_above_sl_below(self):
        tp, sl = OrderExecutor._derive_bracket_prices("BUY", 1.10, 2.0, 1.0)
        assert tp > 1.10
        assert sl < 1.10

    def test_sell_tp_below_sl_above(self):
        tp, sl = OrderExecutor._derive_bracket_prices("SELL", 1.10, 2.0, 1.0)
        assert tp < 1.10
        assert sl > 1.10

    def test_raises_on_negative_result(self):
        with pytest.raises(ValueError):
            OrderExecutor._derive_bracket_prices("BUY", 0.01, 1.0, 200.0)


@pytest.mark.unit
class TestPlaceOrderGuards:
    def test_not_running(self):
        e = OrderExecutor.__new__(OrderExecutor)
        e._running = False
        r = e.place_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "quantity": 1})
        assert r["ok"] is False
        assert "stopped" in r["message"]

    def test_preview_not_running(self):
        e = OrderExecutor.__new__(OrderExecutor)
        e._running = False
        r = e.preview_order({"symbol": "EURUSD", "side": "BUY", "order_type": "MKT", "quantity": 1})
        assert r["ok"] is False
