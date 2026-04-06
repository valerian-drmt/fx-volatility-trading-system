from threading import RLock

import pytest

from client_roles import default_client_roles
from controller import Controller


@pytest.mark.unit
def test_validate_status_settings_normalizes_values():
    normalized = Controller._validate_status_settings(
        {
            "host": " 127.0.0.1 ",
            "port": "4002",
            "client_id": "7",
            "market_symbol": " eurusd ",
        }
    )

    assert normalized == {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 3,
        "client_roles": {
            "order_worker": 1,
            "market_data": 2,
            "dashboard": 3,
        },
        "readonly": False,
        "market_symbol": "EURUSD",
    }


@pytest.mark.unit
def test_validate_status_settings_rejects_missing_keys():
    with pytest.raises(ValueError, match="Missing settings keys"):
        Controller._validate_status_settings({"host": "127.0.0.1"})


@pytest.mark.unit
def test_validate_status_settings_rejects_empty_host():
    with pytest.raises(ValueError, match="host"):
        Controller._validate_status_settings(
            {
                "host": "   ",
                "port": 4002,
                "client_id": 1,
                "market_symbol": "EURUSD",
            }
        )


@pytest.mark.unit
def test_validate_status_settings_rejects_empty_market_symbol():
    with pytest.raises(ValueError, match="market_symbol"):
        Controller._validate_status_settings(
            {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 1,
                "market_symbol": "   ",
            }
        )


@pytest.mark.unit
def test_validate_status_settings_forces_readonly_false_for_legacy_payloads():
    normalized = Controller._validate_status_settings(
        {
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 1,
            "readonly": True,
            "market_symbol": "EURUSD",
        }
    )

    assert normalized["readonly"] is False


@pytest.mark.unit
def test_validate_status_settings_rejects_duplicate_role_client_ids():
    with pytest.raises(ValueError, match="must be distinct"):
        Controller._validate_status_settings(
            {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 3,
                "client_roles": {
                    "order_worker": 2,
                    "market_data": 2,
                    "dashboard": 3,
                },
                "market_symbol": "EURUSD",
            }
        )


@pytest.mark.unit
def test_validate_app_settings_supports_status_payload():
    validated = Controller._validate_app_settings(
        {
            "status": {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 2,
                "market_symbol": "EURUSD",
            }
        }
    )

    assert validated["status"]["market_symbol"] == "EURUSD"
    assert validated["status"]["client_id"] == 3
    assert validated["status"]["client_roles"] == default_client_roles()
    assert validated["runtime"] == {
        "tick_interval_ms": 100,
        "snapshot_interval_ms": 2000,
    }


@pytest.mark.unit
def test_validate_app_settings_supports_legacy_top_level_payload():
    validated = Controller._validate_app_settings(
        {
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 3,
            "live_streaming": {"market_symbol": "GBPUSD"},
        }
    )

    assert validated["status"]["host"] == "127.0.0.1"
    assert validated["status"]["market_symbol"] == "GBPUSD"
    assert validated["runtime"] == {
        "tick_interval_ms": 100,
        "snapshot_interval_ms": 2000,
    }


@pytest.mark.unit
def test_validate_app_settings_runtime_override_is_supported():
    validated = Controller._validate_app_settings(
        {
            "status": {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 2,
                "market_symbol": "EURUSD",
            },
            "runtime": {
                "tick_interval_ms": 150,
                "snapshot_interval_ms": 2500,
            },
        }
    )
    assert validated["runtime"] == {
        "tick_interval_ms": 150,
        "snapshot_interval_ms": 2500,
    }


@pytest.mark.unit
def test_validate_app_settings_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        Controller._validate_app_settings([])


@pytest.mark.unit
def test_validate_runtime_settings_rejects_too_fast_snapshot():
    with pytest.raises(ValueError, match="snapshot_interval_ms"):
        Controller._validate_runtime_settings({"tick_interval_ms": 100, "snapshot_interval_ms": 50})


class _DummyLogsPanel:
    def __init__(self):
        self.calls = []

    def update(self, payload):
        self.calls.append(payload)


class _DummyButton:
    def __init__(self):
        self.click_calls = 0

    def click(self):
        self.click_calls += 1


class _DummyChartPanel:
    def __init__(self):
        self.calls = []

    def update(self, payload):
        self.calls.append(payload)


class _DummySymbolInput:
    def __init__(self, value: str):
        self._value = value

    def currentText(self):
        return self._value


class _DummyOrderTicketPanel:
    def __init__(self, symbol: str = "EURUSD"):
        self.symbol_input = _DummySymbolInput(symbol)
        self.calls = []

    def update(self, payload):
        self.calls.append(payload)


@pytest.mark.unit
def test_on_market_data_payload_auto_clicks_stop_button_on_no_tick_test_3_warning():
    stop_button = _DummyButton()
    logs_panel = _DummyLogsPanel()
    controller = Controller.__new__(Controller)
    controller.window = type(
        "DummyWindow",
        (),
        {
            "status_panel": type("DummyStatusPanel", (), {"stop_live_stream_button": stop_button})(),
            "logs_panel": logs_panel,
            "chart_panel": type("DummyChartPanel", (), {"update": lambda self, payload: None})(),
            "orders_panel": type("DummyOrdersPanel", (), {"update": lambda self, payload: None})(),
            "portfolio_panel": type("DummyPortfolioPanel", (), {"update": lambda self, payload: None})(),
        },
    )()
    controller._refresh_status = lambda *args, **kwargs: None
    controller._stop_live_streaming = lambda: None

    warning_message = (
        "[WARN][market_data] no ticks received (test 3/3); "
        "market may be closed or data is unavailable for this symbol."
    )
    controller._on_market_data_payload({"messages": [warning_message]})

    assert logs_panel.calls == [{"messages": [warning_message]}]
    assert stop_button.click_calls == 1


@pytest.mark.unit
def test_on_market_data_payload_does_not_click_stop_button_before_test_3():
    stop_button = _DummyButton()
    logs_panel = _DummyLogsPanel()
    controller = Controller.__new__(Controller)
    controller.window = type(
        "DummyWindow",
        (),
        {
            "status_panel": type("DummyStatusPanel", (), {"stop_live_stream_button": stop_button})(),
            "logs_panel": logs_panel,
            "chart_panel": type("DummyChartPanel", (), {"update": lambda self, payload: None})(),
            "orders_panel": type("DummyOrdersPanel", (), {"update": lambda self, payload: None})(),
            "portfolio_panel": type("DummyPortfolioPanel", (), {"update": lambda self, payload: None})(),
        },
    )()
    controller._refresh_status = lambda *args, **kwargs: None
    controller._stop_live_streaming = lambda: None

    info_message = "[INFO][market_data] no ticks received (test 2/3)."
    controller._on_market_data_payload({"messages": [info_message]})

    assert logs_panel.calls == [{"messages": [info_message]}]
    assert stop_button.click_calls == 0


@pytest.mark.unit
def test_pump_ib_network_calls_pump_when_connected():
    class _FakeClient:
        def __init__(self):
            self.connected_calls = 0
            self.pump_calls = 0

        def is_connected(self):
            self.connected_calls += 1
            return True

        def pump_network(self):
            self.pump_calls += 1

    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()

    controller._pump_ib_network()

    assert controller.ib_client.connected_calls == 1
    assert controller.ib_client.pump_calls == 1


@pytest.mark.unit
def test_pump_ib_network_skips_when_disconnected():
    class _FakeClient:
        def __init__(self):
            self.connected_calls = 0
            self.pump_calls = 0

        def is_connected(self):
            self.connected_calls += 1
            return False

        def pump_network(self):
            self.pump_calls += 1

    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()

    controller._pump_ib_network()

    assert controller.ib_client.connected_calls == 1
    assert controller.ib_client.pump_calls == 0


@pytest.mark.unit
def test_on_market_data_payload_does_not_push_ticks_to_logs_panel():
    logs_panel = _DummyLogsPanel()
    chart_calls = []

    class _DummyChartPanel:
        def update(self, payload):
            chart_calls.append(payload)

    controller = Controller.__new__(Controller)
    controller.window = type(
        "DummyWindow",
        (),
        {
            "status_panel": type("DummyStatusPanel", (), {"stop_live_stream_button": _DummyButton()})(),
            "logs_panel": logs_panel,
            "chart_panel": _DummyChartPanel(),
            "orders_panel": type("DummyOrdersPanel", (), {"update": lambda self, payload: None})(),
            "portfolio_panel": type("DummyPortfolioPanel", (), {"update": lambda self, payload: None})(),
        },
    )()
    controller._refresh_status = lambda *args, **kwargs: None
    controller._stop_live_streaming = lambda: None

    controller._on_market_data_payload({"ticks": [{"bid": 1.1, "ask": 1.2}]})

    assert chart_calls == [{"ticks": [{"bid": 1.1, "ask": 1.2}]}]
    assert logs_panel.calls == []


@pytest.mark.unit
def test_stop_live_streaming_clears_chart_and_logs_message():
    class _FakeClient:
        def __init__(self):
            self.stop_calls = 0

        def stop_live_streaming(self):
            self.stop_calls += 1

    refresh_calls = []
    stop_worker_calls = []
    chart_panel = _DummyChartPanel()
    logs_panel = _DummyLogsPanel()

    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()
    controller.window = type("DummyWindow", (), {"chart_panel": chart_panel, "logs_panel": logs_panel})()
    controller._stop_market_data_worker = lambda: stop_worker_calls.append(True)
    controller._refresh_status = lambda *args, **kwargs: refresh_calls.append((args, kwargs))

    controller._stop_live_streaming()

    assert stop_worker_calls == [True]
    assert controller.ib_client.stop_calls == 1
    assert chart_panel.calls == [{"clear": True}]
    assert logs_panel.calls == [{"message": "[INFO][market_data] live stream stopped"}]
    assert refresh_calls and refresh_calls[-1][1].get("force") is True


@pytest.mark.unit
def test_update_limit_price_from_market_uses_ask_for_buy():
    class _FakeClient:
        def is_connected(self):
            return True

        def get_latest_bid_ask(self):
            return 1.1001, 1.1003

    class _DummyOrderTicketPanel:
        def __init__(self):
            self.limit_prices = []
            self.calls = []

        def get_order_request(self):
            return {
                "symbol": "EURUSD",
                "side": "BUY",
                "order_type": "LMT",
            }

        def set_limit_price(self, price):
            self.limit_prices.append(price)

        def update(self, payload):
            self.calls.append(payload)

    logs_panel = _DummyLogsPanel()
    order_ticket_panel = _DummyOrderTicketPanel()
    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()
    controller.market_symbol = "EURUSD"
    controller.window = type(
        "DummyWindow",
        (),
        {"order_ticket_panel": order_ticket_panel, "logs_panel": logs_panel},
    )()

    controller._update_limit_price_from_market()

    assert order_ticket_panel.limit_prices == [1.1003]
    assert any("updated" in str(call.get("message", "")).lower() for call in order_ticket_panel.calls)
    assert any("limit price refreshed" in str(call.get("message", "")).lower() for call in logs_panel.calls)


@pytest.mark.unit
def test_update_limit_price_from_market_rejects_symbol_mismatch():
    class _FakeClient:
        def is_connected(self):
            return True

        def get_latest_bid_ask(self):
            return 1.1001, 1.1003

    class _DummyOrderTicketPanel:
        def __init__(self):
            self.limit_prices = []
            self.calls = []

        def get_order_request(self):
            return {
                "symbol": "GBPUSD",
                "side": "SELL",
                "order_type": "LMT",
            }

        def set_limit_price(self, price):
            self.limit_prices.append(price)

        def update(self, payload):
            self.calls.append(payload)

    logs_panel = _DummyLogsPanel()
    order_ticket_panel = _DummyOrderTicketPanel()
    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()
    controller.market_symbol = "EURUSD"
    controller.window = type(
        "DummyWindow",
        (),
        {"order_ticket_panel": order_ticket_panel, "logs_panel": logs_panel},
    )()

    controller._update_limit_price_from_market()

    assert order_ticket_panel.limit_prices == []
    assert any("same symbol" in str(call.get("message", "")).lower() for call in order_ticket_panel.calls)


@pytest.mark.unit
def test_on_market_data_payload_updates_order_ticket_quote_and_max_quantities():
    chart_calls = []
    order_ticket_panel = _DummyOrderTicketPanel(symbol="EURUSD")
    controller = Controller.__new__(Controller)
    controller.window = type(
        "DummyWindow",
        (),
        {
            "status_panel": type("DummyStatusPanel", (), {"stop_live_stream_button": _DummyButton()})(),
            "logs_panel": _DummyLogsPanel(),
            "chart_panel": type("DummyChartPanel", (), {"update": lambda self, payload: chart_calls.append(payload)})(),
            "order_ticket_panel": order_ticket_panel,
            "orders_panel": type("DummyOrdersPanel", (), {"update": lambda self, payload: None})(),
            "portfolio_panel": type("DummyPortfolioPanel", (), {"update": lambda self, payload: None})(),
        },
    )()
    controller._refresh_status = lambda *args, **kwargs: None
    controller._stop_live_streaming = lambda: None
    controller.market_symbol = "EURUSD"
    controller._latest_bid = None
    controller._latest_ask = None
    controller._cash_balances_by_currency = {}

    summary = [
        type("SummaryItem", (), {"tag": "CashBalance", "currency": "USD", "value": "1100.2"})(),
        type("SummaryItem", (), {"tag": "CashBalance", "currency": "EUR", "value": "1000"})(),
    ]
    controller._on_market_data_payload(
        {
            "ticks": [{"bid": 1.1000, "ask": 1.1002}],
            "portfolio_payload": {"summary": summary, "positions": []},
        }
    )

    assert chart_calls == [{"ticks": [{"bid": 1.1000, "ask": 1.1002}]}]
    assert order_ticket_panel.calls
    latest_payload = order_ticket_panel.calls[-1]
    assert latest_payload["bid"] == 1.1
    assert latest_payload["ask"] == 1.1002
    assert latest_payload["max_buy_qty"] == 1000
    assert latest_payload["max_sell_qty"] == 1000
    assert latest_payload["requested_currency"] == "EUR"
    assert latest_payload["required_currency"] is None
    assert latest_payload["requested_volume"] is None
    assert latest_payload["required_volume"] is None


@pytest.mark.unit
def test_build_ticket_funding_snapshot_switches_requested_currency_for_sell():
    controller = Controller.__new__(Controller)
    controller.market_symbol = "EURUSD"
    controller._latest_bid = 1.1012
    controller._latest_ask = 1.1014
    controller._cash_balances_by_currency = {
        "EUR": 25000.0,
        "USD": 5000.0,
    }

    snapshot = controller._build_ticket_funding_snapshot(
        {
            "symbol": "EURUSD",
            "side": "SELL",
            "order_type": "MKT",
            "quantity": 20000,
        }
    )

    assert snapshot["requested_currency"] == "USD"
    assert snapshot["requested_volume"] == pytest.approx(22024.0)
    assert snapshot["required_currency"] == "EUR"
    assert snapshot["required_volume"] == pytest.approx(20000.0)
    assert snapshot["available_required_volume"] == pytest.approx(25000.0)
    assert snapshot["funds_ok"] is True


@pytest.mark.unit
def test_extract_cash_balances_prefers_best_tag_and_ignores_base_total_rows():
    summary = [
        type("SummaryItem", (), {"tag": "AvailableFunds", "currency": "USD", "value": "800"})(),
        type("SummaryItem", (), {"tag": "CashBalance", "currency": "USD", "value": "900"})(),
        type("SummaryItem", (), {"tag": "TotalCashBalance", "currency": "USD", "value": "1000"})(),
        type("SummaryItem", (), {"tag": "CashBalance", "currency": "EUR", "value": "250"})(),
        type("SummaryItem", (), {"tag": "CashBalance", "currency": "BASE", "value": "999999"})(),
        type("SummaryItem", (), {"tag": "TotalCashBalance", "currency": "TOTAL", "value": "999999"})(),
    ]

    balances = Controller._extract_cash_balances(summary)

    assert balances == {
        "USD": 1000.0,
        "EUR": 250.0,
    }


@pytest.mark.unit
def test_validate_order_funds_rejects_buy_when_quote_balance_is_insufficient():
    controller = Controller.__new__(Controller)
    controller.market_symbol = "EURUSD"
    controller._latest_ask = 1.2
    controller._cash_balances_by_currency = {"USD": 100.0}
    controller._io_lock = RLock()

    ok, message = controller._validate_order_funds(
        {
            "symbol": "EURUSD",
            "side": "BUY",
            "order_type": "MKT",
            "quantity": 200,
        }
    )

    assert ok is False
    assert "Insufficient USD funds" in message


@pytest.mark.unit
def test_queue_order_from_ticket_blocks_when_funds_are_insufficient():
    class _FakeClient:
        def is_connected(self):
            return True

    class _DummyEmitter:
        def __init__(self):
            self.payloads = []

        def emit(self, payload):
            self.payloads.append(payload)

    class _DummyOrderWorker:
        def __init__(self):
            self.enqueue_order = _DummyEmitter()

    class _DummyThread:
        @staticmethod
        def isRunning():
            return True

    class _OrderTicketPanel:
        def __init__(self):
            self.calls = []

        @staticmethod
        def get_order_request():
            return {
                "symbol": "EURUSD",
                "side": "BUY",
                "order_type": "MKT",
                "quantity": 200,
            }

        def update(self, payload):
            self.calls.append(payload)

    logs_panel = _DummyLogsPanel()
    order_ticket_panel = _OrderTicketPanel()
    order_worker = _DummyOrderWorker()

    controller = Controller.__new__(Controller)
    controller._io_lock = RLock()
    controller.ib_client = _FakeClient()
    controller.market_symbol = "EURUSD"
    controller._latest_ask = 1.2
    controller._cash_balances_by_currency = {"USD": 100.0}
    controller._order_thread = _DummyThread()
    controller._order_worker = order_worker
    controller.window = type(
        "DummyWindow",
        (),
        {
            "order_ticket_panel": order_ticket_panel,
            "logs_panel": logs_panel,
        },
    )()

    controller._queue_order_from_ticket()

    assert order_worker.enqueue_order.payloads == []
    assert any("insufficient usd funds" in str(call.get("message", "")).lower() for call in order_ticket_panel.calls)
    assert any("[WARN][execution]" in str(call.get("message", "")) for call in logs_panel.calls)


@pytest.mark.unit
def test_sync_order_ticket_action_buttons_disables_place_when_funds_insufficient():
    class _ToggleButton:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = bool(value)

    order_ticket_panel = type(
        "DummyOrderTicketPanel",
        (),
        {
            "preview_button": _ToggleButton(),
            "place_button": _ToggleButton(),
        },
    )()

    controller = Controller.__new__(Controller)
    controller.window = type("DummyWindow", (), {"order_ticket_panel": order_ticket_panel})()
    controller._connecting = False
    controller._ticket_funds_ok = False

    controller._sync_order_ticket_action_buttons(connected=True, order_thread_running=True)

    assert order_ticket_panel.preview_button.enabled is True
    assert order_ticket_panel.place_button.enabled is False
