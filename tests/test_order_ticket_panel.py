import pytest

from ui.panels.order_ticket_panel import OrderTicketPanel


@pytest.mark.unit
def test_get_order_request_returns_none_for_optional_tp_sl(qapp):
    panel = OrderTicketPanel()
    request = panel.get_order_request()

    assert request["take_profit"] is None
    assert request["stop_loss"] is None


@pytest.mark.unit
def test_get_order_request_includes_tp_sl_when_set(qapp):
    panel = OrderTicketPanel()
    panel.order_type_combo.setCurrentText("LMT")
    panel.take_profit_input.setValue(1.125)
    panel.stop_loss_input.setValue(1.095)

    request = panel.get_order_request()

    assert request["take_profit"] == 1.125
    assert request["stop_loss"] == 1.095


@pytest.mark.unit
def test_order_type_switch_to_market_disables_lmt_fields_and_clears_tp_sl(qapp):
    panel = OrderTicketPanel()
    panel.order_type_combo.setCurrentText("LMT")
    panel.take_profit_input.setValue(1.2)
    panel.stop_loss_input.setValue(1.1)

    panel.order_type_combo.setCurrentText("MKT")

    assert panel.limit_price_input.isEnabled() is False
    assert panel.take_profit_input.isEnabled() is False
    assert panel.stop_loss_input.isEnabled() is False
    request = panel.get_order_request()
    assert request["order_type"] == "MKT"
    assert request["limit_price"] == 0.0
    assert request["take_profit"] is None
    assert request["stop_loss"] is None
