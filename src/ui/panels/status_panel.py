from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QCheckBox,
    QGroupBox,
)
from typing import Any, Callable


class StatusPanel(QWidget):
    # Build connection controls, runtime labels, and settings inputs.
    def __init__(
        self,
        on_connect: Callable[[], None] | None,
        on_start_live_streaming: Callable[[], None] | None,
        on_stop_live_streaming: Callable[[], None] | None,
        on_save_settings: Callable[[], None] | None,
        connection_defaults: dict[str, Any],
    ) -> None:
        super().__init__()
        required = ("host", "port", "client_id", "readonly", "market_symbol")
        missing = [key for key in required if key not in connection_defaults]
        if missing:
            raise ValueError(f"Missing connection defaults keys: {', '.join(missing)}")

        host_default = str(connection_defaults["host"])
        port_default = int(connection_defaults["port"])
        client_id_default = int(connection_defaults["client_id"])
        readonly_default = bool(connection_defaults["readonly"])
        market_symbol_default = str(connection_defaults["market_symbol"]).strip().upper()

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
        self.host_input = QLineEdit(host_default)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(port_default)
        self.client_id_input = QSpinBox()
        self.client_id_input.setRange(0, 999999)
        self.client_id_input.setValue(client_id_default)
        self.readonly_input = QCheckBox("Read-only")
        self.readonly_input.setChecked(readonly_default)
        self.market_symbol_input = QLineEdit(market_symbol_default)
        self.market_symbol_input.setMaxLength(32)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(on_connect)
        self.save_button = QPushButton("Save Settings")
        if callable(on_save_settings):
            self.save_button.clicked.connect(on_save_settings)
        else:
            self.save_button.setEnabled(False)
        self.live_stream_button = QPushButton("Start Live Streaming")
        self.live_stream_button.setEnabled(False)
        self.live_stream_button.clicked.connect(on_start_live_streaming)
        self.stop_live_stream_button = QPushButton("Stop Live Streaming")
        self.stop_live_stream_button.setEnabled(False)
        if callable(on_stop_live_streaming):
            self.stop_live_stream_button.clicked.connect(on_stop_live_streaming)
        else:
            self.stop_live_stream_button.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.addWidget(QLabel("Connection:"))
        header_layout.addWidget(self.status_conn_label)
        header_layout.addWidget(self.status_dot)
        header_layout.addWidget(self.connect_button)
        header_layout.addStretch(1)

        live_controls_layout = QHBoxLayout()
        live_controls_layout.setContentsMargins(0, 0, 0, 0)
        live_controls_layout.setSpacing(8)
        live_controls_layout.addWidget(self.live_stream_button)
        live_controls_layout.addWidget(self.stop_live_stream_button)
        live_controls_layout.addStretch(1)

        status_group = QGroupBox("Runtime Status")
        status_form = QFormLayout(status_group)
        status_form.setContentsMargins(8, 8, 8, 8)
        status_form.setHorizontalSpacing(10)
        status_form.setVerticalSpacing(4)
        status_form.addRow("Mode:", self.status_mode_label)
        status_form.addRow("Env:", self.status_env_label)
        status_form.addRow("Latency:", self.status_latency_label)
        status_form.addRow("Server time:", self.status_server_time_label)
        status_form.addRow("ClientId:", self.status_client_label)
        status_form.addRow("Account:", self.status_account_label)

        settings_group = QGroupBox("Connection Settings")
        settings_form = QFormLayout(settings_group)
        settings_form.setContentsMargins(8, 8, 8, 8)
        settings_form.setHorizontalSpacing(10)
        settings_form.setVerticalSpacing(4)
        settings_form.addRow("Host:", self.host_input)
        settings_form.addRow("Port:", self.port_input)
        settings_form.addRow("ClientId cfg:", self.client_id_input)
        settings_form.addRow("Read Only:", self.readonly_input)
        settings_form.addRow("Market symbol:", self.market_symbol_input)
        settings_form.addRow("", self.save_button)

        layout.addLayout(header_layout)
        layout.addLayout(live_controls_layout)
        layout.addWidget(status_group)
        layout.addWidget(settings_group)
        layout.addStretch(1)

    # Update connection status labels and action button states.
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
        pipeline_running = bool(payload.get("pipeline_running", False))

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
        self.status_mode_label.setText(mode)
        self.status_env_label.setText(env)
        self.status_client_label.setText(client_id)
        self.status_account_label.setText(account)
        self.status_latency_label.setText(latency)
        self.status_server_time_label.setText(server_time)

        self.connect_button.setEnabled(not connecting and not connected)
        self.live_stream_button.setText("Start Live Streaming")
        self.live_stream_button.setEnabled(connected and not connecting and not pipeline_running)
        self.stop_live_stream_button.setEnabled(not connecting and pipeline_running)
