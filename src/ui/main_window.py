from typing import Callable

from PyQt5 import QtCore
from PyQt5.QtWidgets import QMainWindow, QWidget, QLabel, QDockWidget, QFrame, QSizePolicy

from ui.panels.chart_panel import ChartPanel
from ui.panels.performance_panel import PerformancePanel
from ui.panels.logs_panel import LogsPanel
from ui.panels.robots_panel import RobotsPanel
from ui.panels.orders_panel import OrdersPanel
from ui.panels.risk_panel import RiskPanel
from ui.panels.portfolio_panel import PortfolioPanel
from ui.panels.status_panel import StatusPanel


class MainWindow(QMainWindow):
    @classmethod
    def create_main_window(
        cls,
        max_candles: int,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        market_symbol: str,
    ) -> "MainWindow":
        return cls(
            max_candles=max_candles,
            on_connect=on_connect,
            on_start_live_streaming=on_start_live_streaming,
            on_save_settings=on_save_settings,
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
            market_symbol=market_symbol,
        )

    def __init__(
        self,
        max_candles: int,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        host: str,
        port: int,
        client_id: int,
        readonly: bool,
        market_symbol: str,
    ):
        super().__init__()

        self.setObjectName("main_window")
        self.setWindowTitle("Trading Control Center - IBKR")

        self.chart_panel = self.create_chart_panel(max_candles=max_candles)
        self.performance_panel = self.create_performance_panel()
        self.logs_panel = self.create_logs_panel()
        self.robots_panel = self.create_robots_panel()
        self.orders_panel = self.create_orders_panel()
        self.risk_panel = self.create_risk_panel()
        self.portfolio_panel = self.create_portfolio_panel()
        self.status_panel = self.create_status_panel(
            on_connect,
            on_start_live_streaming,
            on_save_settings,
            connection_defaults={
                "host": host,
                "port": port,
                "client_id": client_id,
                "readonly": readonly,
                "max_candles": max_candles,
                "market_symbol": market_symbol,
            },
        )

        dock_spacer = QWidget()
        dock_spacer.setObjectName("dock_spacer")
        dock_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        dock_spacer.setMinimumSize(0, 0)
        dock_spacer.setMaximumSize(0, 0)
        dock_spacer.hide()
        self.setCentralWidget(dock_spacer)
        self.setDockOptions(
            QMainWindow.AnimatedDocks | QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks
        )

        self._docks = {}
        self._docks["Robots"] = self.create_dock("Robots", QtCore.Qt.LeftDockWidgetArea, self.robots_panel)
        self._docks["Chart"] = self.create_dock("Chart", QtCore.Qt.LeftDockWidgetArea, self.chart_panel)
        self._docks["Status"] = self.create_dock("Status", QtCore.Qt.LeftDockWidgetArea, self.status_panel)
        self._docks["Logs"] = self.create_dock("Logs", QtCore.Qt.LeftDockWidgetArea, self.logs_panel)
        self._docks["Orders"] = self.create_dock("Orders", QtCore.Qt.LeftDockWidgetArea, self.orders_panel)
        self._docks["Portfolio"] = self.create_dock("Portfolio", QtCore.Qt.RightDockWidgetArea, self.portfolio_panel)
        self._docks["Performance"] = self.create_dock(
            "Performance", QtCore.Qt.RightDockWidgetArea, self.performance_panel
        )
        self._docks["Risk"] = self.create_dock("Risk", QtCore.Qt.RightDockWidgetArea, self.risk_panel)

        # 3-column layout:
        # col1 = Status / Robots / Logs
        # col2 = Chart / Orders
        # col3 = Portfolio / Performance / Risk
        self.splitDockWidget(self._docks["Status"], self._docks["Chart"], QtCore.Qt.Horizontal)
        self.splitDockWidget(self._docks["Chart"], self._docks["Portfolio"], QtCore.Qt.Horizontal)
        self.splitDockWidget(self._docks["Status"], self._docks["Robots"], QtCore.Qt.Vertical)
        self.splitDockWidget(self._docks["Robots"], self._docks["Logs"], QtCore.Qt.Vertical)
        self.splitDockWidget(self._docks["Chart"], self._docks["Orders"], QtCore.Qt.Vertical)
        self.splitDockWidget(self._docks["Portfolio"], self._docks["Performance"], QtCore.Qt.Vertical)
        self.splitDockWidget(self._docks["Performance"], self._docks["Risk"], QtCore.Qt.Vertical)

        first_col_initial_width = max(
            self.status_panel.minimumSizeHint().width(),
            self.status_panel.minimumWidth(),
            1,
        )
        self.resizeDocks(
            [self._docks["Status"], self._docks["Chart"], self._docks["Portfolio"]],
            [first_col_initial_width, 860, 300],
            QtCore.Qt.Horizontal,
        )
        self.resizeDocks(
            [self._docks["Status"], self._docks["Robots"], self._docks["Logs"]],
            [140, 280, 200],
            QtCore.Qt.Vertical,
        )
        self.resizeDocks(
            [self._docks["Chart"], self._docks["Orders"]],
            [650, 230],
            QtCore.Qt.Vertical,
        )
        self.resizeDocks(
            [self._docks["Portfolio"], self._docks["Performance"], self._docks["Risk"]],
            [220, 220, 200],
            QtCore.Qt.Vertical,
        )

        window_menu = self.menuBar().addMenu("Window")
        for title in ("Chart", "Robots", "Portfolio", "Performance", "Risk", "Status", "Orders", "Logs"):
            window_menu.addAction(self._docks[title].toggleViewAction())

    def create_chart_panel(self, max_candles: int) -> ChartPanel:
        return ChartPanel(max_candles=max_candles)

    def create_performance_panel(self) -> PerformancePanel:
        return PerformancePanel()

    def create_logs_panel(self) -> LogsPanel:
        return LogsPanel()

    def create_robots_panel(self) -> RobotsPanel:
        return RobotsPanel()

    def create_orders_panel(self) -> OrdersPanel:
        return OrdersPanel()

    def create_risk_panel(self) -> RiskPanel:
        return RiskPanel()

    def create_portfolio_panel(self) -> PortfolioPanel:
        return PortfolioPanel()

    def create_status_panel(
        self,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        connection_defaults: dict,
    ) -> StatusPanel:
        return StatusPanel(
            on_connect,
            on_start_live_streaming,
            on_save_settings,
            connection_defaults=connection_defaults,
        )

    def create_dock(self, title: str, area: QtCore.Qt.DockWidgetArea, widget: QWidget | None):
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{title.lower()}")
        dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable
        )
        if widget is None:
            placeholder = QLabel(f"{title} panel")
            placeholder.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
            placeholder.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
            dock.setWidget(placeholder)
        else:
            dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock
