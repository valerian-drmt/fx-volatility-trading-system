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
        """Populate Vol Scanner and Term Structure with static data for visualization."""
        # Vol Scanner demo rows
        self.vol_scanner_panel.update({
            "spot": 1.09250,
            "scanner_rows": [
                # 1M
                {"tenor": "1M", "delta_label": "ATM", "strike": 1.0925, "iv_market_pct": 7.80, "sigma_fair_pct": 7.40},
                {"tenor": "1M", "delta_label": "25Dc", "strike": 1.1020, "iv_market_pct": 7.15, "sigma_fair_pct": 7.30},
                {"tenor": "1M", "delta_label": "25Dp", "strike": 1.0780, "iv_market_pct": 7.95, "sigma_fair_pct": 7.60},
                {"tenor": "1M", "delta_label": "10Dc", "strike": 1.1150, "iv_market_pct": 8.40, "sigma_fair_pct": 7.90},
                {"tenor": "1M", "delta_label": "10Dp", "strike": 1.0620, "iv_market_pct": 9.10, "sigma_fair_pct": 8.50},
                # 2M
                {"tenor": "2M", "delta_label": "ATM", "strike": 1.0925, "iv_market_pct": 8.10, "sigma_fair_pct": 7.70},
                {"tenor": "2M", "delta_label": "25Dc", "strike": 1.1050, "iv_market_pct": 7.45, "sigma_fair_pct": 7.60},
                {"tenor": "2M", "delta_label": "25Dp", "strike": 1.0700, "iv_market_pct": 8.30, "sigma_fair_pct": 7.95},
                {"tenor": "2M", "delta_label": "10Dc", "strike": 1.1200, "iv_market_pct": 8.85, "sigma_fair_pct": 8.20},
                {"tenor": "2M", "delta_label": "10Dp", "strike": 1.0500, "iv_market_pct": 9.50, "sigma_fair_pct": 8.90},
                # 3M
                {"tenor": "3M", "delta_label": "ATM", "strike": 1.0925, "iv_market_pct": 8.50, "sigma_fair_pct": 7.81},
                {"tenor": "3M", "delta_label": "25Dc", "strike": 1.1080, "iv_market_pct": 7.68, "sigma_fair_pct": 7.95},
                {"tenor": "3M", "delta_label": "25Dp", "strike": 1.0560, "iv_market_pct": 8.22, "sigma_fair_pct": 8.45},
                {"tenor": "3M", "delta_label": "10Dc", "strike": 1.1280, "iv_market_pct": 9.28, "sigma_fair_pct": 8.80},
                {"tenor": "3M", "delta_label": "10Dp", "strike": 1.0350, "iv_market_pct": 10.10, "sigma_fair_pct": 9.60},
                # 6M
                {"tenor": "6M", "delta_label": "ATM", "strike": 1.0925, "iv_market_pct": 9.20, "sigma_fair_pct": 8.46},
                {"tenor": "6M", "delta_label": "25Dc", "strike": 1.1200, "iv_market_pct": 8.55, "sigma_fair_pct": 8.70},
                {"tenor": "6M", "delta_label": "25Dp", "strike": 1.0400, "iv_market_pct": 10.33, "sigma_fair_pct": 9.85},
                {"tenor": "6M", "delta_label": "10Dc", "strike": 1.1400, "iv_market_pct": 9.80, "sigma_fair_pct": 9.30},
                {"tenor": "6M", "delta_label": "10Dp", "strike": 1.0200, "iv_market_pct": 11.50, "sigma_fair_pct": 10.80},
            ],
            "error": None,
        })

        # Book demo data
        self.book_panel.update({
            "summary": {
                "delta_net": -12450.00, "vega_net": 3820.50,
                "gamma_net": 185.30, "theta_net": -42.75, "pnl_total": 1800.0,
            },
            "open_positions": [
                {"symbol": "EUR 3M C1.0925", "side": "SELL", "qty": 10, "tenor": "3M",
                 "strike": "1.0925", "right": "C", "iv_entry_pct": 8.50, "iv_now_pct": 7.90,
                 "delta": -0.52, "vega": 0.18, "gamma": 0.012, "theta": -0.03, "pnl": 1350.0},
                {"symbol": "EUR 3M P1.0560", "side": "BUY", "qty": 5, "tenor": "3M",
                 "strike": "1.0560", "right": "P", "iv_entry_pct": 8.22, "iv_now_pct": 8.80,
                 "delta": 0.25, "vega": 0.14, "gamma": 0.008, "theta": -0.02, "pnl": 775.0},
                {"symbol": "EUR 6M C1.0925", "side": "SELL", "qty": 8, "tenor": "6M",
                 "strike": "1.0925", "right": "C", "iv_entry_pct": 9.20, "iv_now_pct": 9.50,
                 "delta": -0.48, "vega": 0.22, "gamma": 0.006, "theta": -0.04, "pnl": -850.0},
            ],
            "closed_positions": [
                {"symbol": "EUR 1M C1.0900", "side": "BUY", "qty": 10, "tenor": "1M",
                 "strike": "1.0900", "right": "C", "iv_entry_pct": 7.80, "iv_close_pct": 8.30,
                 "pnl_total": 525.0, "verdict": "BON"},
                {"symbol": "EUR 2M P1.0700", "side": "SELL", "qty": 5, "tenor": "2M",
                 "strike": "1.0700", "right": "P", "iv_entry_pct": 8.10, "iv_close_pct": 8.90,
                 "pnl_total": -320.0, "verdict": "MAUVAIS"},
            ],
        })

        # Term Structure demo data
        self.term_structure_panel.update({
            "tenors": ["1M", "2M", "3M", "6M"],
            "iv_market": [7.80, 8.10, 8.50, 9.20],
            "sigma_fair": [7.40, 7.70, 7.81, 8.46],
            "rv": [7.00, 7.20, 7.40, 7.80],
        })

        # Smile Chart demo data (per-tenor)
        self.smile_chart_panel.update({"smiles": {
            "1M": {
                "deltas": [10, 25, 50, 75, 90],
                "iv_market": [9.10, 7.95, 7.80, 7.15, 8.40],
                "sigma_fair": [8.50, 7.60, 7.40, 7.30, 7.90],
            },
            "2M": {
                "deltas": [10, 25, 50, 75, 90],
                "iv_market": [9.50, 8.30, 8.10, 7.45, 8.85],
                "sigma_fair": [8.90, 7.95, 7.70, 7.60, 8.20],
            },
            "3M": {
                "deltas": [10, 25, 50, 75, 90],
                "iv_market": [10.10, 8.22, 8.50, 7.68, 9.28],
                "sigma_fair": [9.60, 8.45, 7.81, 7.95, 8.80],
            },
            "6M": {
                "deltas": [10, 25, 50, 75, 90],
                "iv_market": [11.50, 10.33, 9.20, 8.55, 9.80],
                "sigma_fair": [10.80, 9.85, 8.46, 8.70, 9.30],
            },
        }})



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
