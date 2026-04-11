"""UI tests for all panels. Requires qapp fixture (Qt offscreen)."""
import pytest

# ── RuntimeStatusPanel ──

@pytest.mark.ui
class TestRuntimeStatusPanel:
    def test_requires_defaults(self, qapp):
        from ui.panels.runtime_status_panel import StatusPanel
        with pytest.raises(ValueError):
            StatusPanel(None, None, None, None, connection_defaults={})

    def test_connected_state(self, qapp):
        from ui.panels.runtime_status_panel import StatusPanel
        p = StatusPanel(None, None, None, None,
                        connection_defaults={"host": "h", "port": 4002, "client_id": 1, "market_symbol": "EURUSD"})
        p.update({"connection_state": "connected", "pipeline_running": False})
        assert p.status_conn_label.text() == "Connected"
        assert not p.connect_button.isEnabled()
        assert p.disconnect_button.isEnabled()

    def test_disconnected_state(self, qapp):
        from ui.panels.runtime_status_panel import StatusPanel
        p = StatusPanel(None, None, None, None,
                        connection_defaults={"host": "h", "port": 4002, "client_id": 1, "market_symbol": "EURUSD"})
        p.update({"connection_state": "disconnected"})
        assert p.status_conn_label.text() == "Disconnected"
        assert p.connect_button.isEnabled()

    def test_engine_running(self, qapp):
        from ui.panels.runtime_status_panel import StatusPanel
        p = StatusPanel(None, None, None, None,
                        connection_defaults={"host": "h", "port": 4002, "client_id": 1, "market_symbol": "EURUSD"})
        p.update({"connection_state": "connected", "pipeline_running": True})
        assert p.engine_status_label.text() == "Running"
        assert p.stop_engine_button.isEnabled()


# ── AccountSummaryPanel ──

@pytest.mark.ui
class TestAccountSummaryPanel:
    def _item(self, tag, currency, value):
        return type("S", (), {"tag": tag, "currency": currency, "value": value})()

    def test_renders_balances_in_k(self, qapp):
        from ui.panels.account_summary_panel import PortfolioPanel
        p = PortfolioPanel()
        p.update({"summary": [
            self._item("TotalCashBalance", "USD", "750000"),
            self._item("TotalCashBalance", "EUR", "1000000"),
        ], "positions": []})
        assert "750.0k" in p.usd_balance_label.text()
        assert "1,000.0k" in p.eur_balance_label.text()

    def test_placeholder_when_empty(self, qapp):
        from ui.panels.account_summary_panel import PortfolioPanel
        p = PortfolioPanel()
        p.update({"summary": [], "positions": []})
        assert p.usd_balance_label.text() == "--"

    def test_reset_clears(self, qapp):
        from ui.panels.account_summary_panel import PortfolioPanel
        p = PortfolioPanel()
        p.update({"summary": [self._item("TotalCashBalance", "USD", "500")], "positions": []})
        p.reset()
        assert p.usd_balance_label.text() == "--"


# ── BookPanel (Greeks Summary) ──

@pytest.mark.ui
class TestBookPanel:
    def test_renders_greeks(self, qapp):
        from ui.panels.book_panel import BookPanel
        p = BookPanel()
        p.update({"summary": {
            "delta_net": 15000, "vega_net": -3200,
            "gamma_net": 42.1, "theta_net": -180.5, "pnl_total": 8500,
        }})
        assert p._labels["delta_net"].text() == "+15.0k"
        assert p._labels["vega_net"].text() == "-3.2k"
        assert p._labels["pnl_total"].text() == "+8,500.00"

    def test_pnl_bold(self, qapp):
        from ui.panels.book_panel import BookPanel
        p = BookPanel()
        assert p._labels["pnl_total"].font().bold()

    def test_empty_summary(self, qapp):
        from ui.panels.book_panel import BookPanel
        p = BookPanel()
        p.update({"summary": {}})
        for lbl in p._labels.values():
            assert lbl.text() == "--"


# ── OrderTicketPanel ──

@pytest.mark.ui
class TestOrderTicketPanel:
    def test_futures_order_dict(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p.qty_input.setValue(2)
        order = p._get_futures_order()
        assert order["instrument"] == "Future"
        assert order["quantity"] == 2
        assert order["order_type"] == "MKT"

    def test_spot_order_dict(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p.spot_qty_input.setValue(25000)
        order = p._get_spot_order()
        assert order["instrument"] == "Spot"
        assert order["quantity"] == 25000

    def test_spot_notional_sell(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p._current_bid = 1.10
        p._current_ask = 1.12
        p.spot_side_combo.setCurrentText("SELL")
        p.spot_qty_input.setValue(25000)
        p._update_spot_notional()
        text = p.spot_notional_label.text()
        assert "EUR" in text and "USD" in text

    def test_spot_notional_buy(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p._current_bid = 1.10
        p._current_ask = 1.12
        p.spot_side_combo.setCurrentText("BUY")
        p.spot_qty_input.setValue(25000)
        p._update_spot_notional()
        text = p.spot_notional_label.text()
        assert "USD" in text and "EUR" in text

    def test_market_closed_blocks_book(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p._current_bid = -1.0
        p._current_ask = -1.0
        assert not p._is_market_open()

    def test_set_symbol_updates_all(self, qapp):
        from ui.panels.order_ticket_panel import OrderTicketPanel
        p = OrderTicketPanel()
        p.set_symbol("GBPUSD")
        assert p._spot_symbol_label.text() == "GBPUSD"
        assert p._fut_symbol_label.text() == "GBPUSD"


# ── VolScannerPanel ──

@pytest.mark.ui
class TestVolScannerPanel:
    def test_renders_rows(self, qapp):
        from ui.panels.vol_scanner_panel import VolScannerPanel
        p = VolScannerPanel()
        p.update({"scanner_rows": [
            {"tenor": "1M", "dte": 30, "sigma_mid_pct": 7.0, "sigma_fair_pct": 8.0,
             "ecart_pct": 1.0, "signal": "CHEAP", "RV_pct": 6.5, "RR25_pct": 0.5, "BF25_pct": -0.3},
        ]})
        assert p.table.rowCount() == 1

    def test_none_payload_no_crash(self, qapp):
        from ui.panels.vol_scanner_panel import VolScannerPanel
        p = VolScannerPanel()
        p.update(None)  # should not crash


# ── PnlSpotPanel ──

@pytest.mark.ui
class TestPnlSpotPanel:
    def test_renders_data(self, qapp):
        from ui.panels.pnl_chart_panel import PnlSpotPanel
        p = PnlSpotPanel()
        p.update({"spots": [1.08, 1.10, 1.12], "pnls": [-100, 0, 100], "spot": 1.10})
        # Should not crash, curve data set

    def test_missing_data_no_crash(self, qapp):
        from ui.panels.pnl_chart_panel import PnlSpotPanel
        p = PnlSpotPanel()
        p.update(None)
        p.update({})
        p.update({"spot": 0})
