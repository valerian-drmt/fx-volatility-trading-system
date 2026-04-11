from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QGroupBox,
)
from typing import Any, Callable


class StatusPanel(QWidget):
    def __init__(
        self,
        on_connect: Callable[[], None] | None,
        on_start_engine: Callable[[], None] | None,
        on_stop_engine: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        on_disconnect: Callable[[], None] | None = None,
        connection_defaults: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        required = ("host", "port", "client_id", "market_symbol")
        missing = [key for key in required if key not in connection_defaults]
        if missing:
            raise ValueError(f"Missing connection defaults keys: {', '.join(missing)}")

        host_default = str(connection_defaults["host"])
        port_default = int(connection_defaults["port"])
        client_id_default = int(connection_defaults["client_id"])

        # Connection status
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet("background-color: #666666; border-radius: 5px;")
        self.status_conn_label = QLabel("Disconnected")
        self.connect_button = QPushButton("Start")
        self.connect_button.setFixedWidth(60)
        if callable(on_connect):
            self.connect_button.clicked.connect(on_connect)
        self.disconnect_button = QPushButton("Stop")
        self.disconnect_button.setFixedWidth(60)
        self.disconnect_button.setEnabled(False)
        if callable(on_disconnect):
            self.disconnect_button.clicked.connect(on_disconnect)

        # Engine status
        self.engine_dot = QLabel()
        self.engine_dot.setFixedSize(10, 10)
        self.engine_dot.setStyleSheet("background-color: #666666; border-radius: 5px;")
        self.engine_status_label = QLabel("Stopped")
        self.start_engine_button = QPushButton("Start")
        self.start_engine_button.setFixedWidth(60)
        self.start_engine_button.setEnabled(False)
        if callable(on_start_engine):
            self.start_engine_button.clicked.connect(on_start_engine)
        self.stop_engine_button = QPushButton("Stop")
        self.stop_engine_button.setFixedWidth(60)
        self.stop_engine_button.setEnabled(False)
        if callable(on_stop_engine):
            self.stop_engine_button.clicked.connect(on_stop_engine)

        # Runtime info labels
        self.status_mode_label = QLabel("--")
        self.status_env_label = QLabel("--")
        self.status_latency_label = QLabel("--")
        self.status_server_time_label = QLabel("--")
        self.status_client_label = QLabel("--")
        self.status_account_label = QLabel("--")

        # Settings display (read-only labels)
        self.host_input = QLabel(host_default)
        self.port_input = QLabel(str(port_default))
        self.client_id_input = QLabel(str(client_id_default))
        self.save_button = QPushButton("Settings")
        if callable(on_save_settings):
            self.save_button.clicked.connect(on_save_settings)
        else:
            self.save_button.setEnabled(False)

        # ── Layout ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        status_group = QGroupBox("Runtime Status")
        status_inner = QVBoxLayout(status_group)
        status_inner.setContentsMargins(8, 8, 8, 8)
        status_inner.setSpacing(6)

        # Connection row
        conn_row = QHBoxLayout()
        conn_row.setContentsMargins(0, 0, 0, 0)
        conn_row.setSpacing(6)
        conn_row.addWidget(QLabel("Connection:"))
        conn_row.addWidget(self.status_conn_label)
        conn_row.addWidget(self.status_dot)
        conn_row.addWidget(self.connect_button)
        conn_row.addWidget(self.disconnect_button)
        conn_row.addStretch(1)
        status_inner.addLayout(conn_row)

        # Engine row
        engine_row = QHBoxLayout()
        engine_row.setContentsMargins(0, 0, 0, 0)
        engine_row.setSpacing(6)
        engine_row.addWidget(QLabel("Engine:"))
        engine_row.addWidget(self.engine_status_label)
        engine_row.addWidget(self.engine_dot)
        engine_row.addWidget(self.start_engine_button)
        engine_row.addWidget(self.stop_engine_button)
        engine_row.addStretch(1)
        status_inner.addLayout(engine_row)

        # Runtime info
        status_form = QFormLayout()
        status_form.setContentsMargins(0, 0, 0, 0)
        status_form.setHorizontalSpacing(10)
        status_form.setVerticalSpacing(4)
        status_form.addRow("Mode:", self.status_mode_label)
        status_form.addRow("Env:", self.status_env_label)
        status_form.addRow("Latency:", self.status_latency_label)
        status_form.addRow("Server time:", self.status_server_time_label)
        status_form.addRow("ClientId:", self.status_client_label)
        status_form.addRow("Account:", self.status_account_label)
        status_inner.addLayout(status_form)

        # Settings
        settings_group = QGroupBox("Connection Settings")
        settings_form = QFormLayout(settings_group)
        settings_form.setContentsMargins(8, 8, 8, 8)
        settings_form.setHorizontalSpacing(10)
        settings_form.setVerticalSpacing(4)
        settings_form.addRow("Host:", self.host_input)
        settings_form.addRow("Port:", self.port_input)
        settings_form.addRow("ClientId cfg:", self.client_id_input)
        settings_form.addRow("", self.save_button)

        layout.addWidget(status_group)
        layout.addWidget(settings_group)
        layout.addStretch(1)

        # Keep backward compat aliases
        self.live_stream_button = self.start_engine_button
        self.stop_live_stream_button = self.stop_engine_button

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        state = str(payload.get("connection_state", "disconnected")).lower()
        mode = str(payload.get("mode", "--"))
        env = str(payload.get("env", "--"))
        client_id = str(payload.get("client_id", "--"))
        account = str(payload.get("account", "--"))
        latency = str(payload.get("latency", "--"))
        server_time = str(payload.get("server_time", "--"))
        connecting = bool(payload.get("connecting", False))
        engine_running = bool(payload.get("pipeline_running", False))

        # Connection status
        if state == "connected":
            text = "Connected"
            color = "#2ecc71"
        elif state == "connecting":
            text = "Connecting"
            color = "#f1c40f"
        else:
            text = "Disconnected"
            color = "#e74c3c"
        connected = state == "connected"

        self.status_conn_label.setText(text)
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 5px;")
        self.connect_button.setEnabled(not connecting and not connected)
        self.disconnect_button.setEnabled(connected and not connecting)

        # Engine status
        if engine_running:
            self.engine_status_label.setText("Running")
            self.engine_dot.setStyleSheet("background-color: #2ecc71; border-radius: 5px;")
        else:
            self.engine_status_label.setText("Stopped")
            self.engine_dot.setStyleSheet("background-color: #666666; border-radius: 5px;")

        self.start_engine_button.setEnabled(connected and not connecting and not engine_running)
        self.stop_engine_button.setEnabled(connected and not connecting and engine_running)

        # Runtime info
        self.status_mode_label.setText(mode)
        self.status_env_label.setText(env)
        self.status_client_label.setText(client_id)
        self.status_account_label.setText(account)
        self.status_latency_label.setText(latency)
        self.status_server_time_label.setText(server_time)
