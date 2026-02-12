import sys
import math
import time
from collections import deque

from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QDockWidget,
    QFrame,
    QSizePolicy,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QFormLayout,
    QPushButton,
    QTextEdit,
    QComboBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
    QSlider,
    QCheckBox,
    QGroupBox,
)
import pyqtgraph as pg

from ib_insync import IB, Forex

class CandlestickItem(pg.GraphicsObject):
    """
    Simple candlestick item for pyqtgraph.
    Data is a list of (x, open, high, low, close).
    """
    def __init__(self, data):
        super().__init__()
        self.data = data
        self.picture = None
        self._generate_picture()

    def set_data(self, data):
        self.data = data
        self._generate_picture()
        self.update()

    def _generate_picture(self):
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        w = 0.6  # candle body width

        for (x, open_, high, low, close) in self.data:
            if close >= open_:
                pen = pg.mkPen('g', width=1)
                brush = pg.mkBrush('g')
            else:
                pen = pg.mkPen('r', width=1)
                brush = pg.mkBrush('r')

            p.setPen(pen)
            p.setBrush(brush)

            # wick
            p.drawLine(QtCore.QPointF(x, low), QtCore.QPointF(x, high))

            # body
            rect = QtCore.QRectF(x - w / 2, open_, w, close - open_)
            p.drawRect(rect.normalized())

        p.end()

    def paint(self, painter, *args):
        if self.picture is not None:
            painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        if self.picture is None:
            return QtCore.QRectF()
        # QPicture.boundingRect() -> QRect, convert to QRectF
        r = self.picture.boundingRect()
        return QtCore.QRectF(r.left(), r.top(), r.width(), r.height())


class ChartPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.plots = []
        titles = ["Chart 1", "Chart 2", "Chart 3", "Chart 4"]
        for index in range(4):
            plot = pg.PlotWidget()
            plot.setTitle(titles[index])
            plot.setLabel("bottom", "Tick #")
            plot.setLabel("left", "Price")
            self.plots.append(plot)
            layout.addWidget(plot, index // 2, index % 2)

        self.main_plot = self.plots[0]


class PerformancePanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.plot = pg.PlotWidget()
        self.plot.setTitle("Performance")
        self.plot.setLabel("bottom", "Time")
        self.plot.setLabel("left", "Equity (normalized)")
        self.plot.addLegend()

        stats_widget = QWidget()
        stats_layout = QFormLayout(stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setHorizontalSpacing(10)
        stats_layout.setVerticalSpacing(4)

        self.total_pnl_label = QLabel("--")
        self.max_dd_label = QLabel("--")
        self.trades_label = QLabel("--")
        self.win_rate_label = QLabel("--")

        stats_layout.addRow("Total PnL %:", self.total_pnl_label)
        stats_layout.addRow("Max DD %:", self.max_dd_label)
        stats_layout.addRow("Trades:", self.trades_label)
        stats_layout.addRow("Win rate:", self.win_rate_label)

        layout.addWidget(self.plot)
        layout.addWidget(stats_widget)
        layout.addStretch(1)


class RobotsPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self.start_all_button = QPushButton("Start all")
        self.stop_all_button = QPushButton("Stop all")
        self.refresh_button = QPushButton("Refresh")

        controls.addWidget(self.start_all_button)
        controls.addWidget(self.stop_all_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Instrument", "State", "Last action", "PnL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)

        layout.addLayout(controls)
        layout.addWidget(self.table)


class OrdersPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Open orders"))
        self.orders_table = QTableWidget(0, 6)
        self.orders_table.setHorizontalHeaderLabels(["Id", "Symbol", "Side", "Qty", "Price", "Status"])
        self.orders_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.orders_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.orders_table.verticalHeader().setVisible(False)

        layout.addWidget(self.orders_table)
        layout.addWidget(QLabel("Recent fills"))

        self.fills_table = QTableWidget(0, 5)
        self.fills_table.setHorizontalHeaderLabels(["Time", "Symbol", "Side", "Qty", "Price"])
        self.fills_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fills_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fills_table.setAlternatingRowColors(True)
        self.fills_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fills_table.verticalHeader().setVisible(False)

        layout.addWidget(self.fills_table)


class RiskPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        limits_group = QGroupBox("Limits")
        limits_layout = QFormLayout(limits_group)
        limits_layout.setContentsMargins(6, 6, 6, 6)
        limits_layout.setHorizontalSpacing(10)
        limits_layout.setVerticalSpacing(4)

        self.max_dd_label = QLabel("--")
        self.max_pos_label = QLabel("--")
        self.max_loss_label = QLabel("--")

        limits_layout.addRow("Max DD:", self.max_dd_label)
        limits_layout.addRow("Max position:", self.max_pos_label)
        limits_layout.addRow("Max loss/day:", self.max_loss_label)

        usage_group = QGroupBox("Usage")
        usage_layout = QFormLayout(usage_group)
        usage_layout.setContentsMargins(6, 6, 6, 6)
        usage_layout.setHorizontalSpacing(10)
        usage_layout.setVerticalSpacing(4)

        self.risk_used_label = QLabel("--")
        self.margin_used_label = QLabel("--")

        usage_layout.addRow("Risk used:", self.risk_used_label)
        usage_layout.addRow("Margin used:", self.margin_used_label)

        controls_group = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(6, 6, 6, 6)
        controls_layout.setSpacing(6)

        self.kill_switch = QCheckBox("Kill switch")
        self.kill_reason = QLineEdit()
        self.kill_reason.setPlaceholderText("Reason...")
        self.risk_slider = QSlider(QtCore.Qt.Horizontal)
        self.risk_slider.setMinimum(0)
        self.risk_slider.setMaximum(100)
        self.risk_slider.setValue(50)

        controls_layout.addWidget(self.kill_switch)
        controls_layout.addWidget(self.kill_reason)
        controls_layout.addWidget(QLabel("Risk budget %"))
        controls_layout.addWidget(self.risk_slider)

        layout.addWidget(limits_group)
        layout.addWidget(usage_group)
        layout.addWidget(controls_group)
        layout.addStretch(1)


class LogsPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self.level_combo = QComboBox()
        self.level_combo.addItems(["ALL", "INFO", "WARN", "ERROR"])
        self.source_combo = QComboBox()
        self.source_combo.addItems(["ALL", "system", "strategy", "execution"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter text...")

        controls.addWidget(QLabel("Level"))
        controls.addWidget(self.level_combo)
        controls.addWidget(QLabel("Source"))
        controls.addWidget(self.source_combo)
        controls.addWidget(self.search_edit, 1)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Logs will appear here...")

        layout.addLayout(controls)
        layout.addWidget(self.log_view)

    def append_log(self, message: str):
        self.log_view.append(message)


class ServerTimeWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(str, str)

    def __init__(self, ib: IB):
        super().__init__()
        self.ib = ib

    def run(self):
        try:
            start = time.time()
            server_time = self.ib.reqCurrentTime()
            elapsed_ms = int((time.time() - start) * 1000)
            if isinstance(server_time, (int, float)):
                server_dt = time.localtime(server_time)
                time_text = time.strftime("%H:%M:%S", server_dt)
            else:
                time_text = server_time.strftime("%H:%M:%S")
            self.result.emit(time_text, f"{elapsed_ms} ms")
        except Exception:
            self.result.emit("--", "--")



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

        self.ib = ib
        self.ticker = ticker
        self.max_candles = max_candles
        self.setObjectName("live_tick_window")
        self._ib_host = host
        self._ib_port = port
        self._ib_client_id = client_id
        self._ib_readonly = readonly

        # last valid quote
        self.last_bid = None
        self.last_ask = None

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

        # dock placeholders (single window now, can detach later)
        robots_dock = self._add_dock("Robots", QtCore.Qt.LeftDockWidgetArea, self.robots_panel)
        chart_dock = self._add_dock("Chart", QtCore.Qt.LeftDockWidgetArea, self.chart_panel)
        portfolio_dock = self._add_dock("Portfolio", QtCore.Qt.RightDockWidgetArea)
        risk_dock = self._add_dock("Risk", QtCore.Qt.RightDockWidgetArea, self.risk_panel)
        status_dock = self._add_dock("Status", QtCore.Qt.RightDockWidgetArea)
        performance_dock = self._add_dock("Performance", QtCore.Qt.RightDockWidgetArea, self.performance_panel)
        orders_dock = self._add_dock("Orders", QtCore.Qt.BottomDockWidgetArea, self.orders_panel)
        logs_dock = self._add_dock("Logs", QtCore.Qt.BottomDockWidgetArea, self.logs_panel)

        self._apply_default_layout(
            robots_dock,
            chart_dock,
            portfolio_dock,
            risk_dock,
            status_dock,
            performance_dock,
            orders_dock,
            logs_dock,
        )

        self._init_portfolio_panel()

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet("background-color: #666666; border-radius: 5px;")

        self.status_conn_label = QLabel("Disconnected")
        self.status_mode_label = QLabel("--")
        self.status_env_label = QLabel("--")
        self.status_latency_label = QLabel("--")
        self.status_server_time_label = QLabel("--")
        self.status_client_label = QLabel("--")
        self.status_account_label = QLabel("--")
        self.reconnect_button = QPushButton("Reconnect")
        self.reconnect_button.clicked.connect(self._start_connect)

        status_widget = QWidget()
        status_layout = QVBoxLayout(status_widget)
        status_layout.setContentsMargins(6, 6, 6, 6)
        status_layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.addWidget(QLabel("Connection:"))
        header_layout.addWidget(self.status_conn_label)
        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(self.reconnect_button)
        header_layout.addStretch(1)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        form.addRow("Mode:", self.status_mode_label)
        form.addRow("Env:", self.status_env_label)
        form.addRow("Latency:", self.status_latency_label)
        form.addRow("Server time:", self.status_server_time_label)
        form.addRow("ClientId:", self.status_client_label)
        form.addRow("Account:", self.status_account_label)

        status_layout.addLayout(header_layout)
        status_layout.addLayout(form)
        status_layout.addStretch(1)
        self._set_dock_content("Status", status_widget)

        self._init_window_menu()
        self._restore_layout()
        QTimer.singleShot(0, self._start_connect)

        # timer to drive both IB and plotting
        self.timer = QTimer(self)
        self.timer.setInterval(100)  # ms; ~10 calls per second
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
        if not self.ib.isConnected() or self.ticker is None:
            return

        # 1) let ib_insync pump the IB socket
        self.ib.sleep(0)
        self._update_portfolio_value()

        # 2) read latest quote
        bid = self.ticker.bid
        ask = self.ticker.ask

        if bid is not None and not math.isnan(bid):
            self.last_bid = bid
        if ask is not None and not math.isnan(ask):
            self.last_ask = ask

        if self.last_bid is None or self.last_ask is None:
            return  # not enough info yet

        self._update_tick_series(self.last_bid, self.last_ask)

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
        if not self.ib.isConnected():
            return
        summary = self.ib.accountSummary()
        for item in summary:
            label = self.portfolio_fields.get(item.tag)
            if label is None:
                continue
            if item.currency:
                label.setText(f"{item.value} {item.currency}")
            else:
                label.setText(str(item.value))

        positions = []
        if hasattr(self.ib, "positions"):
            try:
                positions = self.ib.positions()
            except Exception:
                positions = []
        if positions:
            items = []
            for pos in positions[:5]:
                contract = getattr(pos, "contract", None)
                symbol = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None) or "?"
                items.append(f"{symbol}:{pos.position}")
            self.portfolio_exposure_label.setText(", ".join(items))
        else:
            self.portfolio_exposure_label.setText("--")

    def closeEvent(self, event):
        try:
            self._save_layout()
            if hasattr(self.ib, "cancelAccountSummary"):
                self.ib.cancelAccountSummary()
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

    def _set_dock_content(self, title: str, widget: QWidget):
        dock = getattr(self, "_docks", {}).get(title)
        if dock is not None:
            dock.setWidget(widget)
        return dock

    def _apply_default_layout(
        self,
        robots_dock: QDockWidget,
        chart_dock: QDockWidget,
        portfolio_dock: QDockWidget,
        risk_dock: QDockWidget,
        status_dock: QDockWidget,
        performance_dock: QDockWidget,
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
        for title in ("Chart", "Robots", "Portfolio", "Risk", "Status", "Performance", "Orders", "Logs"):
            dock = self._docks.get(title)
            if dock is not None:
                window_menu.addAction(dock.toggleViewAction())

    def _update_status(self):
        now_sec = int(time.time())
        if self._last_status_sec == now_sec:
            return
        self._last_status_sec = now_sec
        connected = self.ib.isConnected()
        if connected:
            connected_text = "Connected"
            dot_color = "#2ecc71"
        elif self._connecting:
            connected_text = "Connecting"
            dot_color = "#f1c40f"
        else:
            connected_text = "Disconnected"
            dot_color = "#e74c3c"
        self.status_dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 5px;")
        self.status_conn_label.setText(connected_text)
        self.reconnect_button.setEnabled(not self._connecting)

        client = getattr(self.ib, "client", None)
        readonly = getattr(client, "readonly", None) if client is not None else None
        if readonly is None:
            mode = "unknown"
        else:
            mode = "read-only" if readonly else "read-write"
        self.status_mode_label.setText(mode)

        port = self._ib_port
        if port in (4002, 7497):
            env_text = "paper"
        elif port in (4001, 7496):
            env_text = "live"
        else:
            env_text = "unknown"
        self.status_env_label.setText(env_text)

        self.status_client_label.setText(str(self._ib_client_id))

        accounts = []
        if hasattr(self.ib, "managedAccounts"):
            accounts = self.ib.managedAccounts()
        account_text = accounts[0] if accounts else "--"
        self.status_account_label.setText(account_text)

        if not connected:
            self._latency_ms_text = "--"
            self._server_time_text = "--"
        self.status_latency_label.setText(self._latency_ms_text)
        self.status_server_time_label.setText(self._server_time_text)

        if connected and (self._last_server_sync_sec is None or now_sec - self._last_server_sync_sec >= 10):
            self._start_server_time_sync()

    def _start_connect(self):
        if self._connecting or self.ib.isConnected():
            return
        self._connecting = True
        self._update_status()
        QApplication.processEvents()
        try:
            try:
                self.ib.connect(
                    self._ib_host,
                    self._ib_port,
                    clientId=self._ib_client_id,
                    readonly=self._ib_readonly,
                    timeout=1.0,
                )
            except TypeError:
                self.ib.connect(
                    self._ib_host,
                    self._ib_port,
                    clientId=self._ib_client_id,
                    readonly=self._ib_readonly,
                )
            if self.ticker is None:
                eurusd = Forex("EURUSD")
                self.ticker = self.ib.reqMktData(eurusd)
            if hasattr(self.ib, "reqAccountSummary"):
                self.ib.reqAccountSummary()
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
        if not hasattr(self.ib, "reqCurrentTime"):
            return
        self._server_time_worker = ServerTimeWorker(self.ib)
        self._server_time_worker.result.connect(self._on_server_time_result)
        self._server_time_worker.finished.connect(self._on_server_time_finished)
        self._server_time_worker.start()

    def _on_server_time_result(self, time_text: str, latency_text: str):
        self._server_time_text = time_text
        self._latency_ms_text = latency_text
        self._last_server_sync_sec = int(time.time())
        self.status_latency_label.setText(self._latency_ms_text)
        self.status_server_time_label.setText(self._server_time_text)

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

    def _init_portfolio_panel(self):
        self.portfolio_fields = {
            "NetLiquidation": QLabel("--"),
            "TotalCashValue": QLabel("--"),
            "AvailableFunds": QLabel("--"),
            "UnrealizedPnL": QLabel("--"),
            "RealizedPnL": QLabel("--"),
            "DailyPnL": QLabel("--"),
            "GrossPositionValue": QLabel("--"),
        }
        self.portfolio_exposure_label = QLabel("--")
        self.portfolio_exposure_label.setWordWrap(True)

        portfolio_widget = QWidget()
        portfolio_layout = QVBoxLayout(portfolio_widget)
        portfolio_layout.setContentsMargins(6, 6, 6, 6)
        portfolio_layout.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        form.addRow("Net Liq:", self.portfolio_fields["NetLiquidation"])
        form.addRow("Cash:", self.portfolio_fields["TotalCashValue"])
        form.addRow("Available:", self.portfolio_fields["AvailableFunds"])
        form.addRow("Unrealized PnL:", self.portfolio_fields["UnrealizedPnL"])
        form.addRow("Realized PnL:", self.portfolio_fields["RealizedPnL"])
        form.addRow("Daily PnL:", self.portfolio_fields["DailyPnL"])
        form.addRow("Gross Pos:", self.portfolio_fields["GrossPositionValue"])

        portfolio_layout.addLayout(form)
        portfolio_layout.addWidget(QLabel("Exposure (top 5):"))
        portfolio_layout.addWidget(self.portfolio_exposure_label)
        portfolio_layout.addStretch(1)

        self._set_dock_content("Portfolio", portfolio_widget)


def main():
    # Qt application
    app = QApplication(sys.argv)

    # IB connection
    ib = IB()

    # main window
    window = LiveTickWindow(ib, max_candles=500)
    window.resize(900, 600)
    window.show()

    # Qt event loop
    exit_code = app.exec_()

    # Clean disconnect on close
    if ib.isConnected():
        ib.disconnect()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
