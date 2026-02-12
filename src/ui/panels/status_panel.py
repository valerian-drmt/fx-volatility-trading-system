from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
)


class StatusPanel(QWidget):
    def __init__(self, on_reconnect):
        super().__init__()

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
        self.reconnect_button.clicked.connect(on_reconnect)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

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

        layout.addLayout(header_layout)
        layout.addLayout(form)
        layout.addStretch(1)

    def set_connection_state(self, connected: bool, connecting: bool):
        if connected:
            text = "Connected"
            color = "#2ecc71"
        elif connecting:
            text = "Connecting"
            color = "#f1c40f"
        else:
            text = "Disconnected"
            color = "#e74c3c"
        self.status_conn_label.setText(text)
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 5px;")

    def set_mode(self, mode: str):
        self.status_mode_label.setText(mode)

    def set_env(self, env: str):
        self.status_env_label.setText(env)

    def set_latency(self, text: str):
        self.status_latency_label.setText(text)

    def set_server_time(self, text: str):
        self.status_server_time_label.setText(text)

    def set_client_id(self, text: str):
        self.status_client_label.setText(text)

    def set_account(self, text: str):
        self.status_account_label.setText(text)

    def set_reconnect_enabled(self, enabled: bool):
        self.reconnect_button.setEnabled(enabled)
