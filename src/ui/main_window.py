from typing import Callable

from PyQt5.QtWidgets import QGridLayout, QMainWindow, QWidget

from ui.panels.chart_panel import ChartPanel
from ui.panels.logs_panel import LogsPanel
from ui.panels.order_ticket_panel import OrderTicketPanel
from ui.panels.orders_panel import OrdersPanel
from ui.panels.portfolio_panel import PortfolioPanel
from ui.panels.status_panel import StatusPanel


class MainWindow(QMainWindow):
    @classmethod
    # Build the main window with callbacks and default settings.
    def create_main_window(
        cls,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        status_defaults: dict[str, object],
    ) -> "MainWindow":
        return cls(
            on_connect=on_connect,
            on_start_live_streaming=on_start_live_streaming,
            on_stop_live_streaming=on_stop_live_streaming,
            on_save_settings=on_save_settings,
            status_defaults=status_defaults,
        )

    # Create and lay out all dashboard panels.
    def __init__(
        self,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        status_defaults: dict[str, object],
    ) -> None:
        super().__init__()

        self.setObjectName("main_window")
        self.setWindowTitle("Trading Dashboard - IBKR")

        self.chart_panel = self.create_chart_panel()
        self.logs_panel = self.create_logs_panel()
        self.orders_panel = self.create_orders_panel()
        self.order_ticket_panel = self.create_order_ticket_panel()
        self.portfolio_panel = self.create_portfolio_panel()
        self.status_panel = self.create_status_panel(
            on_connect,
            on_start_live_streaming,
            on_stop_live_streaming,
            on_save_settings,
            connection_defaults=status_defaults,
        )
        default_symbol = str(status_defaults.get("market_symbol", "EURUSD")).strip().upper()
        self.chart_panel.set_symbol(default_symbol)
        self.chart_panel.market_symbol_input.setCurrentText(default_symbol)
        self.order_ticket_panel.set_symbol(default_symbol)
        self.chart_panel.market_symbol_input.currentTextChanged.connect(self.order_ticket_panel.set_symbol)

        container = QWidget(self)
        grid = QGridLayout(container)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)

        # Row 1: Status | Chart | Order Ticket
        grid.addWidget(self.status_panel, 0, 0)
        grid.addWidget(self.chart_panel, 0, 1)
        grid.addWidget(self.order_ticket_panel, 0, 2)

        # Row 2: Logs | Orders | Portfolio
        grid.addWidget(self.logs_panel, 1, 0)
        grid.addWidget(self.orders_panel, 1, 1)
        grid.addWidget(self.portfolio_panel, 1, 2)

        # Relative sizes: col0/col1/col2 = 1/4/1 and row0/row1 = 1/1.
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 4)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        # Optional hard minimums to keep layout readable when window is small.
        grid.setColumnMinimumWidth(0, 250)
        grid.setColumnMinimumWidth(1, 800)
        grid.setColumnMinimumWidth(2, 250)
        grid.setRowMinimumHeight(0, 300)
        grid.setRowMinimumHeight(1, 300)

        self.setCentralWidget(container)

    # Create the chart panel instance.
    def create_chart_panel(self) -> ChartPanel:
        return ChartPanel(max_points=100)

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

    # Create the status panel wired with controller callbacks.
    def create_status_panel(
        self,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        connection_defaults: dict[str, object],
    ) -> StatusPanel:
        return StatusPanel(
            on_connect,
            on_start_live_streaming,
            on_stop_live_streaming,
            on_save_settings,
            connection_defaults=connection_defaults,
        )
