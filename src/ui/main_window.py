from collections.abc import Callable

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from ui.panels.account_summary_panel import PortfolioPanel
from ui.panels.book_panel import BookPanel, OpenPositionsPanel
from ui.panels.logs_panel import LogsPanel
from ui.panels.order_ticket_panel import OrderTicketPanel
from ui.panels.pnl_chart_panel import PnlSpotPanel
from ui.panels.runtime_status_panel import StatusPanel
from ui.panels.smile_chart_panel import SmileChartPanel, SmileDetailsPanel
from ui.panels.term_structure_panel import TermStructurePanel
from ui.panels.tick_chart_panel import ChartPanel
from ui.panels.vol_scanner_panel import VolScannerPanel


class MainWindow(QMainWindow):
    window_closed = pyqtSignal()

    def closeEvent(self, event):
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
        self.order_ticket_panel = self.create_order_ticket_panel()
        self.portfolio_panel = self.create_portfolio_panel()
        self.vol_scanner_panel = VolScannerPanel()
        self.term_structure_panel = TermStructurePanel()
        self.smile_chart_panel = SmileChartPanel()
        self.smile_details_panel = SmileDetailsPanel()
        self.smile_chart_panel.set_details_panel(self.smile_details_panel)
        self.book_panel = BookPanel()
        self.pnl_spot_panel = PnlSpotPanel()
        self.open_positions_panel = OpenPositionsPanel()
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
        col1_widget.setFixedWidth(365)

        # ── Box 2 (center): Charts row on top + Vol Scanner below ──
        col2 = QVBoxLayout()
        col2.setContentsMargins(0, 0, 0, 0)
        #col2.setSpacing(6)

        charts_row = QHBoxLayout()
        charts_row.setContentsMargins(0, 0, 0, 0)
        charts_row.setSpacing(0)
        charts_row.addWidget(self.chart_panel, 1)
        charts_row.addWidget(self.term_structure_panel, 1)
        charts_row.addWidget(self.smile_chart_panel, 1)
        charts_row.addWidget(self.smile_details_panel, 1)
        charts_row_widget = QWidget()
        charts_row_widget.setLayout(charts_row)
        charts_row_widget.setMaximumHeight(375)
        col2.addWidget(charts_row_widget, 1)

        col2.addWidget(self.vol_scanner_panel, 1)
        col2.addWidget(self.open_positions_panel, 1)
        col2_widget = QWidget()
        col2_widget.setLayout(col2)
        col2_widget.setFixedWidth(1500)

        # ── Box 3 (right): Order Ticket + Greeks Summary + PnL Chart ──
        col3 = QVBoxLayout()
        col3.setContentsMargins(0, 0, 0, 0)
        col3.setSpacing(6)
        col3.addWidget(self.order_ticket_panel, 0)
        col3.addWidget(self.book_panel, 0)
        col3.addWidget(self.pnl_spot_panel, 1)
        col3_widget = QWidget()
        col3_widget.setLayout(col3)
        col3_widget.setFixedWidth(450)

        main_layout.addWidget(col1_widget)
        main_layout.addWidget(col2_widget)
        main_layout.addWidget(col3_widget)

        self.setCentralWidget(container)
        self.adjustSize()
        self._center_on_screen()
        self._load_demo_data()

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 2 + geo.y()
            self.move(x, y)

    def _load_demo_data(self) -> None:
        """No demo data — panels are populated by live IB data."""



    # Create the chart panel instance.
    def create_chart_panel(self) -> ChartPanel:
        return ChartPanel(max_points=150)

    # Create the log panel instance.
    def create_logs_panel(self) -> LogsPanel:
        return LogsPanel()

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
