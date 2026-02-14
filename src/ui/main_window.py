import time
from collections import deque

from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QDockWidget, QFrame, QSizePolicy
import pyqtgraph as pg

from ib_insync import IB
from services.ib_client import IBClient

from ui.panels.chart_panel import ChartPanel
from ui.panels.performance_panel import PerformancePanel
from ui.panels.logs_panel import LogsPanel
from ui.panels.robots_panel import RobotsPanel
from ui.panels.orders_panel import OrdersPanel
from ui.panels.risk_panel import RiskPanel
from ui.panels.portfolio_panel import PortfolioPanel
from ui.panels.status_panel import StatusPanel


class ServerTimeWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(str, str)

    def __init__(self, ib_client: IBClient):
        super().__init__()
        self.ib_client = ib_client

    def run(self):
        time_text, latency_text = self.ib_client.get_server_time_and_latency()
        self.result.emit(time_text, latency_text)


class LiveTickWindow(QMainWindow):
    def __init__(
        self,
        ib: IB,
        ticker=None,
        max_candles: int = 500,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 2,
        readonly: bool = True,
    ):
        super().__init__()

        self.max_candles = max_candles
        self.setObjectName("live_tick_window")
        self.ib_client = IBClient(
            ib=ib,
            ticker=ticker,
            host=host,
            port=port,
            client_id=client_id,
            readonly=readonly,
        )

        # --- tick state ---
        self.tick_index = 0
        self.tick_x = deque(maxlen=max_candles)
        self.tick_bid = deque(maxlen=max_candles)
        self.tick_ask = deque(maxlen=max_candles)

        # --- UI setup ---
        self.setWindowTitle("Trading Control Center - IBKR")

        self._docks = {}
        self._settings = QtCore.QSettings("TradingApp", "LiveTick")
        self._last_status_sec = None
        self._last_server_sync_sec = None
        self._server_time_text = "--"
        self._latency_ms_text = "--"
        self._connecting = False
        self._server_time_worker = None
        self._last_connect_error = ""

        self.chart_panel = ChartPanel()
        self.plot = self.chart_panel.main_plot
        self.performance_panel = PerformancePanel()
        self.logs_panel = LogsPanel()
        self.robots_panel = RobotsPanel()
        self.orders_panel = OrdersPanel()
        self.risk_panel = RiskPanel()
        self.portfolio_panel = PortfolioPanel()
        self.status_panel = StatusPanel(self._start_connect)

        self.bid_item = self.plot.plot([], [], pen=pg.mkPen('g', width=1))
        self.ask_item = self.plot.plot([], [], pen=pg.mkPen('r', width=1))

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

        robots_dock = self._add_dock("Robots", QtCore.Qt.LeftDockWidgetArea, self.robots_panel)
        chart_dock = self._add_dock("Chart", QtCore.Qt.LeftDockWidgetArea, self.chart_panel)
        portfolio_dock = self._add_dock("Portfolio", QtCore.Qt.RightDockWidgetArea, self.portfolio_panel)
        performance_dock = self._add_dock("Performance", QtCore.Qt.RightDockWidgetArea, self.performance_panel)
        risk_dock = self._add_dock("Risk", QtCore.Qt.RightDockWidgetArea, self.risk_panel)
        status_dock = self._add_dock("Status", QtCore.Qt.RightDockWidgetArea, self.status_panel)
        orders_dock = self._add_dock("Orders", QtCore.Qt.BottomDockWidgetArea, self.orders_panel)
        logs_dock = self._add_dock("Logs", QtCore.Qt.BottomDockWidgetArea, self.logs_panel)

        self._apply_default_layout(
            robots_dock,
            chart_dock,
            portfolio_dock,
            performance_dock,
            risk_dock,
            status_dock,
            orders_dock,
            logs_dock,
        )

        self._init_window_menu()
        self._restore_layout()
        QTimer.singleShot(0, self._start_connect)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_data_and_plot)
        self.timer.start()

    def update_data_and_plot(self):
        """
        Periodic callback.
        1) Process IB messages.
        2) Read latest bid/ask.
        3) Update the tick plot.
        """
        self._update_status()
        if not self.ib_client.is_connected():
            return

        self.ib_client.process_messages()
        self._update_portfolio_value()

        bid, ask = self.ib_client.get_latest_bid_ask()
        if bid is None or ask is None:
            return

        self._update_tick_series(bid, ask)

    def _update_tick_series(self, bid: float, ask: float):
        self.tick_index += 1
        self.tick_x.append(self.tick_index)
        self.tick_bid.append(bid)
        self.tick_ask.append(ask)

        self.bid_item.setData(list(self.tick_x), list(self.tick_bid))
        self.ask_item.setData(list(self.tick_x), list(self.tick_ask))
        self._autoscale_main_plot()

    def _autoscale_main_plot(self):
        if not self.tick_x:
            return

        min_price = min(min(self.tick_bid), min(self.tick_ask))
        max_price = max(max(self.tick_bid), max(self.tick_ask))
        if min_price == max_price:
            pad = max(1e-4, abs(min_price) * 0.001)
        else:
            pad = (max_price - min_price) * 0.1
        self.plot.setYRange(min_price - pad, max_price + pad, padding=0)
        self.plot.setXRange(self.tick_x[0], self.tick_x[-1], padding=0.02)

    def _update_portfolio_value(self):
        if not self.ib_client.is_connected():
            return
        summary, positions = self.ib_client.get_portfolio_snapshot()
        self.portfolio_panel.update_summary(summary)
        self.portfolio_panel.update_positions(positions)

    def closeEvent(self, event):
        try:
            self._save_layout()
            self.ib_client.cancel_account_summary()
        finally:
            super().closeEvent(event)

    def _add_dock(self, title: str, area: QtCore.Qt.DockWidgetArea, widget: QWidget = None):
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
        self._docks[title] = dock
        return dock

    def _apply_default_layout(
        self,
        robots_dock: QDockWidget,
        chart_dock: QDockWidget,
        portfolio_dock: QDockWidget,
        performance_dock: QDockWidget,
        risk_dock: QDockWidget,
        status_dock: QDockWidget,
        orders_dock: QDockWidget,
        logs_dock: QDockWidget,
    ):
        self.splitDockWidget(robots_dock, chart_dock, QtCore.Qt.Horizontal)
        self.splitDockWidget(chart_dock, portfolio_dock, QtCore.Qt.Horizontal)
        self.splitDockWidget(portfolio_dock, performance_dock, QtCore.Qt.Vertical)
        self.splitDockWidget(performance_dock, risk_dock, QtCore.Qt.Vertical)
        self.splitDockWidget(risk_dock, status_dock, QtCore.Qt.Vertical)
        self.splitDockWidget(chart_dock, orders_dock, QtCore.Qt.Vertical)
        self.splitDockWidget(orders_dock, logs_dock, QtCore.Qt.Vertical)

        self.resizeDocks([robots_dock, chart_dock, portfolio_dock], [220, 860, 260], QtCore.Qt.Horizontal)
        self.resizeDocks([chart_dock, orders_dock, logs_dock], [640, 200, 180], QtCore.Qt.Vertical)
        self.resizeDocks(
            [portfolio_dock, performance_dock, risk_dock, status_dock],
            [200, 200, 160, 120],
            QtCore.Qt.Vertical,
        )

    def _init_window_menu(self):
        window_menu = self.menuBar().addMenu("Window")
        for title in ("Chart", "Robots", "Portfolio", "Performance", "Risk", "Status", "Orders", "Logs"):
            dock = self._docks.get(title)
            if dock is not None:
                window_menu.addAction(dock.toggleViewAction())

    def _update_status(self):
        now_sec = int(time.time())
        if self._last_status_sec == now_sec:
            return
        self._last_status_sec = now_sec
        status = self.ib_client.get_status_snapshot()
        connected = status["connected"]
        self.status_panel.set_connection_state(connected, self._connecting)
        self.status_panel.set_reconnect_enabled(not self._connecting)
        self.status_panel.set_mode(status["mode"])
        self.status_panel.set_env(status["env"])
        self.status_panel.set_client_id(status["client_id"])
        self.status_panel.set_account(status["account"])

        if not connected:
            self._latency_ms_text = "--"
            self._server_time_text = "--"
        self.status_panel.set_latency(self._latency_ms_text)
        self.status_panel.set_server_time(self._server_time_text)

        if connected and (self._last_server_sync_sec is None or now_sec - self._last_server_sync_sec >= 10):
            self._start_server_time_sync()

    def _start_connect(self):
        if self._connecting or self.ib_client.is_connected():
            return
        self._connecting = True
        self._update_status()
        QApplication.processEvents()
        try:
            self.ib_client.connect_and_prepare()
        except Exception as exc:
            self._last_connect_error = str(exc)
        finally:
            self._connecting = False
            self._update_status()

    def _start_server_time_sync(self):
        if self._server_time_worker is not None:
            try:
                if self._server_time_worker.isRunning():
                    return
            except RuntimeError:
                self._server_time_worker = None
        if not self.ib_client.supports_server_time():
            return
        self._server_time_worker = ServerTimeWorker(self.ib_client)
        self._server_time_worker.result.connect(self._on_server_time_result)
        self._server_time_worker.finished.connect(self._on_server_time_finished)
        self._server_time_worker.start()

    def _on_server_time_result(self, time_text: str, latency_text: str):
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self.status_panel.set_latency(self._latency_ms_text)
        self.status_panel.set_server_time(self._server_time_text)

    def _on_server_time_finished(self):
        if self._server_time_worker is not None:
            self._server_time_worker.deleteLater()
        self._server_time_worker = None

    def _restore_layout(self):
        geometry = self._settings.value("window/geometry", type=QtCore.QByteArray)
        state = self._settings.value("window/state", type=QtCore.QByteArray)
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def _save_layout(self):
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/state", self.saveState())
        self._settings.sync()
