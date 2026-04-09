import pytest

from ui.panels.order_ticket import OrderTicketPanel


@pytest.mark.unit
def test_get_futures_order_defaults(qapp):
    panel = OrderTicketPanel()
    request = panel.get_order_request()

    assert request["instrument"] == "Future"
    assert request["order_type"] == "MKT"
    assert request["use_bracket"] is False
    assert request["symbol"] == "EURUSD"
    assert request["quantity"] == panel.qty_input.value()


@pytest.mark.unit
def test_futures_delta_updates_on_side_change(qapp):
    panel = OrderTicketPanel()
    panel.set_market_quote(1.10000, 1.10020)
    panel.qty_input.setValue(10)
    panel.side_combo.setCurrentText("BUY")
    # delta = price * qty * sign = 1.1 * 10 * +1 = +11.0
    assert "+" in panel.fut_delta_label.text()
    assert panel.fut_delta_label.text() != "--"

    panel.side_combo.setCurrentText("SELL")
    assert "-" in panel.fut_delta_label.text()


@pytest.mark.unit
def test_option_order_fields(qapp):
    panel = OrderTicketPanel()
    # Simulate chain discovery
    panel.set_option_chains({"3M": [1.08, 1.09, 1.10]})
    panel.opt_side_combo.setCurrentText("BUY")
    panel.opt_right_combo.setCurrentText("CALL")
    panel.opt_expiry_combo.setCurrentText("3M")
    panel.opt_strike_combo.setCurrentText("1.09000")
    panel.opt_qty_input.setValue(5)

    order = panel._get_option_order()

    assert order["instrument"] == "Option"
    assert order["side"] == "BUY"
    assert order["right"] == "CALL"
    assert order["tenor"] == "3M"
    assert order["strike"] == pytest.approx(1.09)
    assert order["quantity"] == 5
    assert order["order_type"] == "MKT"


@pytest.mark.unit
def test_set_symbol_updates_both_labels(qapp):
    panel = OrderTicketPanel()
    panel.set_symbol("GBPUSD")

    assert panel._fut_symbol_label.text() == "GBPUSD"
    assert panel._opt_symbol_label.text() == "GBPUSD"



@pytest.mark.unit
def test_market_quote_update(qapp):
    panel = OrderTicketPanel()
    panel.update({"bid": 1.10123, "ask": 1.10140})

    assert "1.10123" in panel.market_quote_value.text()
    assert "1.1014" in panel.market_quote_value.text()
