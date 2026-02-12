import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))

from PyQt5.QtWidgets import QApplication

from ui.main_window import LiveTickWindow


class _SummaryItem:
    def __init__(self, tag, value, currency=""):
        self.tag = tag
        self.value = value
        self.currency = currency


class _FakeClient:
    readonly = True


class _FakeIB:
    def __init__(self):
        self.client = _FakeClient()
        self._connected = False

    def isConnected(self):
        return self._connected

    def managedAccounts(self):
        return []

    def accountSummary(self):
        return [
            _SummaryItem("NetLiquidation", "100000", "USD"),
            _SummaryItem("TotalCashValue", "50000", "USD"),
        ]

    def positions(self):
        return []

    def sleep(self, _secs=0):
        return True


def _get_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_builds_and_updates():
    _get_app()
    ib = _FakeIB()
    window = LiveTickWindow(ib, max_candles=10)

    window._update_status()
    assert window.status_panel.status_conn_label.text() == "Disconnected"

    ib._connected = True
    window._update_portfolio_value()
    assert "USD" in window.portfolio_panel.fields["NetLiquidation"].text()

    window._update_tick_series(1.0, 1.1)
    assert window.tick_index == 1
    assert len(window.tick_x) == 1

    window.close()
