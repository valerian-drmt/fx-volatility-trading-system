from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QComboBox,
    QLineEdit,
)


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

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return
        if payload.get("clear"):
            self.log_view.clear()
            return

        message = payload.get("message")
        if message:
            self.log_view.append(str(message))

        messages = payload.get("messages") or []
        for item in messages:
            self.log_view.append(str(item))
