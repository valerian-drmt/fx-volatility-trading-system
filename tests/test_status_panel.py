import pytest

from ui.panels.runtime_status_panel import StatusPanel


def _build_panel():
    defaults = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 1,
        "market_symbol": "EURUSD",
    }
    return StatusPanel(
        on_connect=lambda: None,
        on_start_engine=lambda: None,
        on_stop_engine=lambda: None,
        on_save_settings=lambda: None,
        connection_defaults=defaults,
    )


@pytest.mark.unit
def test_status_panel_requires_all_default_keys(qapp):
    with pytest.raises(ValueError, match="Missing connection defaults keys"):
        StatusPanel(
            on_connect=lambda: None,
            on_start_engine=lambda: None,
            on_stop_engine=lambda: None,
            on_save_settings=lambda: None,
            connection_defaults={"host": "127.0.0.1"},
        )


@pytest.mark.unit
def test_status_panel_connected_state_updates_buttons(qapp):
    panel = _build_panel()

    panel.update(
        {
            "connection_state": "connected",
            "mode": "read-only",
            "env": "paper",
            "client_id": "2",
            "account": "DU123",
            "latency": "10 ms",
            "server_time": "09:30:00",
            "connecting": False,
            "pipeline_running": False,
        }
    )

    assert panel.status_conn_label.text() == "Connected"
    assert panel.status_mode_label.text() == "read-only"
    assert panel.status_env_label.text() == "paper"
    assert panel.status_client_label.text() == "2"
    assert panel.status_account_label.text() == "DU123"
    assert panel.connect_button.isEnabled() is False
    assert panel.start_engine_button.isEnabled() is True
    assert panel.stop_engine_button.isEnabled() is False


@pytest.mark.unit
def test_status_panel_connecting_state_disables_controls(qapp):
    panel = _build_panel()

    panel.update(
        {
            "connection_state": "connecting",
            "connecting": True,
            "pipeline_running": False,
        }
    )

    assert panel.status_conn_label.text() == "Connecting"
    assert panel.connect_button.isEnabled() is False
    assert panel.start_engine_button.isEnabled() is False
    assert panel.stop_engine_button.isEnabled() is False


@pytest.mark.unit
def test_status_panel_engine_running_enables_stop(qapp):
    panel = _build_panel()

    panel.update(
        {
            "connection_state": "connected",
            "connecting": False,
            "pipeline_running": True,
        }
    )

    assert panel.start_engine_button.isEnabled() is False
    assert panel.stop_engine_button.isEnabled() is True
    assert panel.engine_status_label.text() == "Running"
