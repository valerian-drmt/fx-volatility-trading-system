import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.append(str(Path(__file__).resolve().parents[2] / "src"))

from PyQt5.QtWidgets import QApplication

from ui.panels.chart_panel import ChartPanel
from ui.panels.performance_panel import PerformancePanel
from ui.panels.portfolio_panel import PortfolioPanel
from ui.panels.status_panel import StatusPanel
from ui.panels.robots_panel import RobotsPanel
from ui.panels.orders_panel import OrdersPanel
from ui.panels.risk_panel import RiskPanel
from ui.panels.logs_panel import LogsPanel


def _get_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_panels_create():
    _get_app()

    chart = ChartPanel()
    performance = PerformancePanel()
    portfolio = PortfolioPanel()
    status = StatusPanel(lambda: None)
    robots = RobotsPanel()
    orders = OrdersPanel()
    risk = RiskPanel()
    logs = LogsPanel()

    assert len(chart.plots) == 4
    assert chart.main_plot is not None
    assert performance.plot is not None
    assert portfolio.fields["NetLiquidation"].text() == "--"
    assert status.status_conn_label.text() == "Disconnected"
    assert robots.table.columnCount() == 5
    assert orders.orders_table.columnCount() == 6
    assert orders.fills_table.columnCount() == 5
    assert risk.risk_slider.minimum() == 0
    assert logs.level_combo.count() == 4
