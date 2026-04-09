from typing import Callable

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout, QMainWindow,
    QVBoxLayout, QWidget,
)

from ui.panels.tick_chart import ChartPanel
from ui.panels.logs import LogsPanel
from ui.panels.order_ticket import OrderTicketPanel
from ui.panels.orders import OrdersPanel
from ui.panels.vol_scanner import VolScannerPanel
from ui.panels.term_structure import TermStructurePanel
from ui.panels.book import BookPanel
from ui.panels.smile_chart import SmileChartPanel
from ui.panels.portfolio import PortfolioPanel
from ui.panels.status import StatusPanel


class MainWindow(QMainWindow):
    window_closed = pyqtSignal()

    def closeEvent(self, event):  # noqa: N802
        self.window_closed.emit()
        super().closeEvent(event)

    @classmethod
    def create_main_window(
        cls,
        on_connect: Callable[[], None] | None,
        on_disconnect: Callable[[], None] | None = None,
        on_start_engine: Callable[[], None] | None = None,
        on_stop_engine: Callable[[], None] | None = None,
        on_save_settings: Callable[[], None] | None = None,
        status_defaults: dict[str, object] | None = None,
    ) -> "MainWindow":
        return cls(
            on_connect=on_connect,
            on_disconnect=on_disconnect,
            on_start_engine=on_start_engine,
            on_stop_engine=on_stop_engine,
            on_save_settings=on_save_settings,
            status_defaults=status_defaults or {},
        )

    def __init__(
        self,
        on_connect: Callable[[], None] | None,
        on_disconnect: Callable[[], None] | None = None,
        on_start_engine: Callable[[], None] | None = None,
        on_stop_engine: Callable[[], None] | None = None,
        on_save_settings: Callable[[], None] | None = None,
        status_defaults: dict[str, object] | None = None,
    ) -> None:
        super().__init__()

        self.setObjectName("main_window")
        self.setWindowTitle("FX Options Trading Dashboard")

        self.chart_panel = self.create_chart_panel()
        self.logs_panel = self.create_logs_panel()
        self.orders_panel = self.create_orders_panel()
        self.order_ticket_panel = self.create_order_ticket_panel()
        self.portfolio_panel = self.create_portfolio_panel()
        self.vol_scanner_panel = VolScannerPanel()
        self.term_structure_panel = TermStructurePanel()
        self.smile_chart_panel = SmileChartPanel()
        self.book_panel = BookPanel()
        self.status_panel = self.create_status_panel(
            on_connect,
            on_start_engine,
            on_stop_engine,
            on_save_settings,
            on_disconnect=on_disconnect,
            connection_defaults=status_defaults,
        )
        # Backward compat aliases for controller
        self.start_vol_button = self.status_panel.start_engine_button
        self.stop_vol_button = self.status_panel.stop_engine_button

        default_symbol = str(status_defaults.get("market_symbol", "EURUSD")).strip().upper()
        self.chart_panel.set_symbol(default_symbol)
        self.chart_panel.market_symbol_input.setCurrentText(default_symbol)
        self.order_ticket_panel.set_symbol(default_symbol)
        self.chart_panel.market_symbol_input.currentTextChanged.connect(self.order_ticket_panel.set_symbol)
        self.chart_panel.set_bid_offer_label(self.order_ticket_panel.bid_offer_label)
        self.chart_panel.set_on_price_update(self.order_ticket_panel.set_market_quote)

        # ── 3 horizontal boxes ──
        container = QWidget(self)
        main_layout = QHBoxLayout(container)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        # ── Box 1 (left): Status + Account Summary + Logs ──
        col1 = QVBoxLayout()
        col1.setContentsMargins(0, 0, 0, 0)
        col1.setSpacing(0)
        col1.addWidget(self.status_panel)
        col1.addWidget(self.portfolio_panel)
        col1.addWidget(self.logs_panel)
        col1.addStretch(1)
        col1_widget = QWidget()
        col1_widget.setLayout(col1)

        # ── Box 2 (center): Charts row on top + Vol Scanner below ──
        col2 = QVBoxLayout()
        col2.setContentsMargins(0, 0, 0, 0)
        col2.setSpacing(6)

        charts_row = QHBoxLayout()
        charts_row.setContentsMargins(0, 0, 0, 0)
        charts_row.setSpacing(6)
        chart_side = 340
        for panel in (self.chart_panel, self.term_structure_panel, self.smile_chart_panel):
            panel.setFixedSize(chart_side, chart_side)
            charts_row.addWidget(panel)
        charts_row.addStretch(1)
        col2.addLayout(charts_row, 1)

        col2.addWidget(self.vol_scanner_panel, 2)
        col2_widget = QWidget()
        col2_widget.setLayout(col2)

        # ── Box 3 (right): Order Ticket + Book ──
        col3 = QVBoxLayout()
        col3.setContentsMargins(0, 0, 0, 0)
        col3.setSpacing(6)
        col3.addWidget(self.order_ticket_panel, 0)
        col3.addWidget(self.book_panel, 1)
        col3_widget = QWidget()
        col3_widget.setLayout(col3)

        col1_widget.setMaximumWidth(345)
        main_layout.addWidget(col1_widget, 0)
        main_layout.addWidget(col2_widget, 2)
        main_layout.addWidget(col3_widget, 3)

        self.setCentralWidget(container)
        self.resize(1920, 1080)
        self.setFixedSize(self.size())
        self._load_demo_data()

    def _load_demo_data(self) -> None:
        """No demo data — panels are populated by live IB data."""



    # Create the chart panel instance.
    def create_chart_panel(self) -> ChartPanel:
        return ChartPanel(max_points=150)

    # Create the log panel instance.
    def create_logs_panel(self) -> LogsPanel:
        return LogsPanel()

    # Create the orders panel instance.
    def create_orders_panel(self) -> OrdersPanel:
        return OrdersPanel()

    # Create the order ticket panel instance.
    def create_order_ticket_panel(self) -> OrderTicketPanel:
        return OrderTicketPanel()

    # Create the portfolio panel instance.
    def create_portfolio_panel(self) -> PortfolioPanel:
        return PortfolioPanel()

    def create_status_panel(
        self,
        on_connect: Callable[[], None] | None,
        on_start_engine: Callable[[], None] | None,
        on_stop_engine: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        on_disconnect: Callable[[], None] | None = None,
        connection_defaults: dict[str, object] | None = None,
    ) -> StatusPanel:
        return StatusPanel(
            on_connect,
            on_start_engine,
            on_stop_engine,
            on_save_settings,
            on_disconnect=on_disconnect,
            connection_defaults=connection_defaults or {},
        )
