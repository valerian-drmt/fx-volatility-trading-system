from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class OrderTicketPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        ticket_group = QGroupBox("Order Ticket")
        ticket_form = QFormLayout(ticket_group)
        ticket_form.setContentsMargins(8, 8, 8, 8)
        ticket_form.setHorizontalSpacing(10)
        ticket_form.setVerticalSpacing(4)

        self.symbol_input = QLineEdit("EURUSD")
        self.symbol_input.setMaxLength(32)
        self.side_combo = QComboBox()
        self.side_combo.addItems(["BUY", "SELL"])
        self.order_type_combo = QComboBox()
        self.order_type_combo.addItems(["MKT", "LMT"])
        self.qty_input = QSpinBox()
        self.qty_input.setRange(1, 100000000)
        self.qty_input.setValue(10000)
        self.limit_price_input = QDoubleSpinBox()
        self.limit_price_input.setDecimals(8)
        self.limit_price_input.setRange(0.0, 1000000.0)
        self.limit_price_input.setValue(1.10000)
        self.limit_price_input.setSingleStep(0.00001)

        ticket_form.addRow("Symbol:", self.symbol_input)
        ticket_form.addRow("Side:", self.side_combo)
        ticket_form.addRow("Type:", self.order_type_combo)
        ticket_form.addRow("Quantity:", self.qty_input)
        ticket_form.addRow("Limit price:", self.limit_price_input)

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.preview_button = QPushButton("Preview")
        self.place_button = QPushButton("Place Order")
        self.cancel_all_button = QPushButton("Cancel All")
        self.cancel_all_button.setEnabled(False)
        actions_layout.addWidget(self.preview_button)
        actions_layout.addWidget(self.place_button)
        actions_layout.addWidget(self.cancel_all_button)
        actions_layout.addStretch(1)

        self.feedback_label = QLabel("--")

        layout.addWidget(ticket_group)
        layout.addLayout(actions_layout)
        layout.addWidget(self.feedback_label)
        layout.addStretch(1)

    def get_order_request(self) -> dict:
        return {
            "symbol": self.symbol_input.text().strip().upper(),
            "side": self.side_combo.currentText().strip().upper(),
            "order_type": self.order_type_combo.currentText().strip().upper(),
            "quantity": int(self.qty_input.value()),
            "limit_price": float(self.limit_price_input.value()),
        }

    def set_feedback(self, message: str, level: str = "info"):
        text = str(message).strip() or "--"
        level_key = str(level).strip().lower()
        if level_key == "error":
            color = "#e74c3c"
        elif level_key == "success":
            color = "#2ecc71"
        else:
            color = "#f1c40f"
        self.feedback_label.setText(text)
        self.feedback_label.setStyleSheet(f"color: {color};")

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return
        if "message" in payload:
            level = str(payload.get("level", "info"))
            self.set_feedback(str(payload.get("message", "")), level=level)
