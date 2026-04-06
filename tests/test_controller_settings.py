import pytest

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
        "client_id": 7,
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
    assert validated["status"]["client_id"] == 2
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
