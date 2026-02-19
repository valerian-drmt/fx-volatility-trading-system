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
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        on_save_live_streaming_settings: Callable[[], None] | None,
        status_defaults: dict,
        live_streaming_defaults: dict,
    ) -> "MainWindow":
        return cls(
            on_connect=on_connect,
            on_start_live_streaming=on_start_live_streaming,
            on_stop_live_streaming=on_stop_live_streaming,
            on_save_settings=on_save_settings,
            on_save_live_streaming_settings=on_save_live_streaming_settings,
            status_defaults=status_defaults,
            live_streaming_defaults=live_streaming_defaults,
        )

    def __init__(
        self,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        on_save_live_streaming_settings: Callable[[], None] | None,
        status_defaults: dict,
        live_streaming_defaults: dict,
    ):
        super().__init__()

        self.setObjectName("main_window")
        self.setWindowTitle("Trading Control Center - IBKR")

        self.chart_panel = self.create_chart_panel(
            max_candles=int(live_streaming_defaults["max_candles"]),
            market_symbol=str(live_streaming_defaults["market_symbol"]).upper(),
            on_apply_and_save=on_save_live_streaming_settings,
        )
        self.performance_panel = self.create_performance_panel()
        self.logs_panel = self.create_logs_panel()
        self.robots_panel = self.create_robots_panel()
        self.orders_panel = self.create_orders_panel()
        self.risk_panel = self.create_risk_panel()
        self.portfolio_panel = self.create_portfolio_panel()
        self.status_panel = self.create_status_panel(
            on_connect,
            on_start_live_streaming,
            on_stop_live_streaming,
            on_save_settings,
            connection_defaults=status_defaults,
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

        first_col_initial_width = max(1, self.status_panel.minimumSizeHint().width())
        self.status_panel.setMinimumWidth(first_col_initial_width)
        self._docks["Status"].setMinimumWidth(first_col_initial_width)
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

    def create_chart_panel(
        self,
        max_candles: int,
        market_symbol: str,
        on_apply_and_save: Callable[[], None] | None,
    ) -> ChartPanel:
        return ChartPanel(
            max_candles=max_candles,
            market_symbol=market_symbol,
            on_apply_and_save=on_apply_and_save,
        )

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
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        connection_defaults: dict,
    ) -> StatusPanel:
        return StatusPanel(
            on_connect,
            on_start_live_streaming,
            on_stop_live_streaming,
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
