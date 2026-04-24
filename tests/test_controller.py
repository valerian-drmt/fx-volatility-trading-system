"""Unit tests for Controller settings, routing, and engine pool."""
import pytest

from controller import Controller


@pytest.mark.unit
class TestValidateStatusSettings:
    def test_normalizes_values(self):
        r = Controller._validate_status_settings({
            "host": " 127.0.0.1 ", "port": "4002", "client_id": "7",
            "market_symbol": " eurusd ",
        })
        assert r["host"] == "127.0.0.1"
        assert r["port"] == 4002
        assert r["client_id"] == 1  # uses default from roles
        assert r["market_symbol"] == "EURUSD"

    def test_rejects_empty_host(self):
        with pytest.raises(ValueError, match="host"):
            Controller._validate_status_settings({
                "host": "", "port": 4002, "market_symbol": "EURUSD",
            })

    def test_rejects_empty_symbol(self):
        with pytest.raises(ValueError, match="market_symbol"):
            Controller._validate_status_settings({
                "host": "127.0.0.1", "port": 4002, "market_symbol": "",
            })

    def test_rejects_duplicate_role_ids(self):
        with pytest.raises(ValueError, match="distinct"):
            Controller._validate_status_settings({
                "host": "127.0.0.1", "port": 4002, "client_id": 1,
                "market_symbol": "EURUSD",
                "client_roles": {"market_data": 2, "vol_engine": 2, "risk_engine": 3},
            })

    def test_uses_default_roles(self):
        r = Controller._validate_status_settings({
            "host": "127.0.0.1", "port": 4002, "market_symbol": "EURUSD",
        })
        assert r["client_roles"] == {"market_data": 1, "vol_engine": 2, "risk_engine": 3}


@pytest.mark.unit
class TestValidateAppSettings:
    def test_supports_status_payload(self):
        r = Controller._validate_app_settings({
            "status": {"host": "127.0.0.1", "port": 4002, "market_symbol": "EURUSD"},
        })
        assert r["status"]["market_symbol"] == "EURUSD"
        assert r["runtime"]["tick_interval_ms"] == 100

    def test_supports_legacy_top_level(self):
        r = Controller._validate_app_settings({
            "host": "127.0.0.1", "port": 4002,
            "live_streaming": {"market_symbol": "GBPUSD"},
        })
        assert r["status"]["market_symbol"] == "GBPUSD"


@pytest.mark.unit
class TestExtractCashBalances:
    def _item(self, tag, currency, value):
        return type("S", (), {"tag": tag, "currency": currency, "value": value})()

    def test_prefers_total_cash_balance(self):
        summary = [
            self._item("AvailableFunds", "USD", "300"),
            self._item("CashBalance", "USD", "900"),
            self._item("TotalCashBalance", "USD", "1100"),
        ]
        balances = Controller._extract_cash_balances(summary)
        assert balances["USD"] == 1100.0

    def test_excludes_base_currency(self):
        summary = [
            self._item("TotalCashBalance", "BASE", "5000"),
            self._item("TotalCashBalance", "USD", "1000"),
        ]
        balances = Controller._extract_cash_balances(summary)
        assert "BASE" not in balances
        assert balances["USD"] == 1000.0

    def test_empty_summary(self):
        assert Controller._extract_cash_balances([]) == {}
        assert Controller._extract_cash_balances(None) == {}


@pytest.mark.unit
class TestEnginePoolIdempotent:
    def test_stop_empty_pool_no_error(self):
        c = Controller.__new__(Controller)
        c._engine_pool = []
        c._engine_poll_timer = None
        c._market_engine = None
        c._vol_engine = None
        c._risk_engine = None
        c._db_writer_thread = None
        c._stop_engine_pool()  # should not raise

    def test_stop_twice_no_error(self):
        c = Controller.__new__(Controller)
        c._engine_pool = []
        c._engine_poll_timer = None
        c._market_engine = None
        c._vol_engine = None
        c._risk_engine = None
        c._db_writer_thread = None
        c._stop_engine_pool()
        c._stop_engine_pool()  # idempotent


@pytest.mark.unit
class TestOnRiskResult:
    def test_routes_to_panels(self):
        calls = {"book": [], "positions": [], "pnl": []}

        class FakePanel:
            def __init__(self, key):
                self._key = key
            def update(self, payload):
                calls[self._key].append(payload)

        c = Controller.__new__(Controller)
        c._db_writer_thread = None
        c.window = type("W", (), {
            "open_positions_panel": FakePanel("positions"),
            "book_panel": FakePanel("book"),
            "pnl_spot_panel": FakePanel("pnl"),
        })()

        c._on_risk_result({
            "open_positions": [{"symbol": "6EM5"}],
            "summary": {"delta_net": 100},
            "pnl_curve": {"spots": [1.0], "pnls": [0.0], "spot": 1.0},
        })

        assert len(calls["positions"]) == 1
        assert len(calls["book"]) == 1
        assert len(calls["pnl"]) == 1
        assert calls["book"][0] == {"summary": {"delta_net": 100}}
