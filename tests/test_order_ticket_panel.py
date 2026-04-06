import pytest

from ui.panels.order_ticket_panel import OrderTicketPanel


@pytest.mark.unit
def test_get_order_request_defaults_to_non_bracket_mode(qapp):
    panel = OrderTicketPanel()
    request = panel.get_order_request()

    assert request["use_bracket"] is False
    assert request["take_profit"] is None
    assert request["stop_loss"] is None
    assert request["take_profit_pct"] is None
    assert request["stop_loss_pct"] is None
    assert request["rr_ratio"] is None
    assert request["volume"] == request["quantity"] == panel.qty_input.value()


@pytest.mark.unit
def test_order_type_switch_to_market_disables_limit_only_and_keeps_bracket_state(qapp):
    panel = OrderTicketPanel()
    panel.set_limit_price_update_available(True)
    panel.order_type_combo.setCurrentText("LMT")
    panel.bracket_checkbox.setChecked(True)
    assert panel.limit_price_update_button.isEnabled() is True
    assert panel.take_profit_pct_input.isEnabled() is True
    assert panel.stop_loss_pct_input.isEnabled() is True

    panel.order_type_combo.setCurrentText("MKT")

    assert panel.limit_price_input.isEnabled() is False
    assert panel.limit_price_update_button.isEnabled() is False
    assert panel.bracket_checkbox.isEnabled() is True
    assert panel.bracket_checkbox.isChecked() is True
    assert panel.take_profit_pct_input.isEnabled() is True
    assert panel.stop_loss_pct_input.isEnabled() is True
    request = panel.get_order_request()
    assert request["order_type"] == "MKT"
    assert request["limit_price"] == 0.0
    assert request["use_bracket"] is True


@pytest.mark.unit
def test_bracket_toggle_enables_percentage_inputs_for_limit_orders(qapp):
    panel = OrderTicketPanel()
    panel.order_type_combo.setCurrentText("LMT")

    panel.bracket_checkbox.setChecked(True)
    request = panel.get_order_request()

    assert panel.take_profit_pct_input.isEnabled() is True
    assert panel.stop_loss_pct_input.isEnabled() is True
    assert request["use_bracket"] is True
    assert request["take_profit_pct"] == pytest.approx(panel.take_profit_pct_input.value())
    assert request["stop_loss_pct"] == pytest.approx(panel.stop_loss_pct_input.value())
    assert request["rr_ratio"] == pytest.approx(panel.take_profit_pct_input.value() / panel.stop_loss_pct_input.value())


@pytest.mark.unit
def test_order_ticket_symbol_uses_fx_pair_combo_box(qapp):
    panel = OrderTicketPanel()

    assert panel.symbol_input.isEditable() is False
    assert panel.symbol_input.isEnabled() is False
    assert panel.symbol_input.findText("EURUSD") >= 0
    panel.set_symbol("GBPUSD")

    request = panel.get_order_request()
    assert request["symbol"] == "GBPUSD"


@pytest.mark.unit
def test_set_limit_price_updates_limit_spinbox(qapp):
    panel = OrderTicketPanel()

    panel.set_limit_price(1.23456)

    assert panel.limit_price_input.value() == pytest.approx(1.23456)


@pytest.mark.unit
def test_order_ticket_panel_updates_market_quote_and_mkt_reference_price(qapp):
    panel = OrderTicketPanel()
    panel.order_type_combo.setCurrentText("MKT")
    panel.side_combo.setCurrentText("BUY")
    panel.update({"bid": 1.10123, "ask": 1.10140, "max_buy_qty": 12345, "max_sell_qty": 6789})
    request = panel.get_order_request()

    assert panel.market_quote_value.text() == "1.10123 / 1.1014"
    assert request["reference_price"] == pytest.approx(1.10140)


@pytest.mark.unit
def test_order_ticket_panel_updates_required_and_requested_volume_rows(qapp):
    panel = OrderTicketPanel()
    panel.update(
        {
            "requested_volume": 20000,
            "requested_currency": "EUR",
            "required_volume": 23101.2,
            "required_currency": "USD",
            "available_required_volume": 10000.0,
            "funds_ok": False,
        }
    )

    assert panel.requested_volume_value.text() == "20,000.00 EUR"
    assert panel.required_volume_value.text() == "23,101.20 USD (available: 10,000.00 USD)"
    assert "e74c3c" in panel.required_volume_value.styleSheet()


@pytest.mark.unit
def test_cancel_all_button_removed_from_order_ticket(qapp):
    panel = OrderTicketPanel()
    assert not hasattr(panel, "cancel_all_button")
