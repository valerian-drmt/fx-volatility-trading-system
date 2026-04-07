from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from typing import Any


class OrderTicketPanel(QWidget):
    FX_PAIRS = (
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCHF",
        "USDCAD",
        "AUDUSD",
        "NZDUSD",
        "EURGBP",
        "EURJPY",
        "GBPJPY",
        "EURCHF",
        "AUDJPY",
    )

    # Build order-entry controls and action buttons.
    def __init__(self) -> None:
        super().__init__()
        self._limit_price_update_available = False
        self._current_bid: float | None = None
        self._current_ask: float | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        ticket_group = QGroupBox("Order Ticket (FX)")
        ticket_form = QFormLayout(ticket_group)
        ticket_form.setContentsMargins(8, 8, 8, 8)
        ticket_form.setHorizontalSpacing(10)
        ticket_form.setVerticalSpacing(4)

        self.symbol_input = QComboBox()
        self.symbol_input.setEditable(False)
        self.symbol_input.addItems(self.FX_PAIRS)
        self.symbol_input.setCurrentText("EURUSD")
        self.symbol_input.setEnabled(False)
        self.symbol_input.setToolTip("Locked to the live tick chart ticker.")
        self.side_combo = QComboBox()
        self.side_combo.addItems(["BUY", "SELL"])
        self.order_type_combo = QComboBox()
        self.order_type_combo.addItems(["MKT", "LMT"])
        self.bracket_checkbox = QCheckBox("Enable TP/SL bracket")
        self.bracket_checkbox.setChecked(False)
        self.qty_input = QSpinBox()
        self.qty_input.setRange(1, 100000000)
        self.qty_input.setValue(20000)
        self.limit_price_input = QDoubleSpinBox()
        self.limit_price_input.setDecimals(8)
        self.limit_price_input.setRange(0.0, 1000000.0)
        self.limit_price_input.setValue(1.10000)
        self.limit_price_input.setSingleStep(0.00001)
        self.limit_price_update_button = QPushButton("Update")
        self.limit_price_update_button.setToolTip("Update limit price from latest market quote.")
        limit_price_row = QWidget()
        limit_price_row_layout = QHBoxLayout(limit_price_row)
        limit_price_row_layout.setContentsMargins(0, 0, 0, 0)
        limit_price_row_layout.setSpacing(6)
        limit_price_row_layout.addWidget(self.limit_price_input, 1)
        limit_price_row_layout.addWidget(self.limit_price_update_button)
        self.take_profit_pct_input = QDoubleSpinBox()
        self.take_profit_pct_input.setDecimals(3)
        self.take_profit_pct_input.setRange(0.001, 100.0)
        self.take_profit_pct_input.setSingleStep(0.1)
        self.take_profit_pct_input.setValue(0.5)
        self.stop_loss_pct_input = QDoubleSpinBox()
        self.stop_loss_pct_input.setDecimals(3)
        self.stop_loss_pct_input.setRange(0.001, 100.0)
        self.stop_loss_pct_input.setSingleStep(0.1)
        self.stop_loss_pct_input.setValue(0.25)
        self.requested_volume_value = QLabel("--")
        self.required_volume_value = QLabel("--")
        self.market_quote_value = QLabel("-- / --")

        ticket_form.addRow("FX Symbol:", self.symbol_input)
        ticket_form.addRow("Side:", self.side_combo)
        ticket_form.addRow("Type:", self.order_type_combo)
        ticket_form.addRow("Limit price:", limit_price_row)
        ticket_form.addRow("Bracket:", self.bracket_checkbox)
        ticket_form.addRow("TP (%):", self.take_profit_pct_input)
        ticket_form.addRow("SL (%):", self.stop_loss_pct_input)
        ticket_form.addRow("Volume:", self.qty_input)
        ticket_form.addRow("Requested Volume:", self.requested_volume_value)
        ticket_form.addRow("Required Volume:", self.required_volume_value)
        ticket_form.addRow("Bid / Ask:", self.market_quote_value)

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setEnabled(False)
        self.place_button = QPushButton("Place Order")
        self.place_button.setEnabled(False)
        actions_layout.addWidget(self.preview_button)
        actions_layout.addWidget(self.place_button)
        actions_layout.addStretch(1)

        self.feedback_label = QLabel("--")
        self.feedback_label.setWordWrap(True)

        layout.addWidget(ticket_group)
        layout.addLayout(actions_layout)
        layout.addWidget(self.feedback_label)
        layout.addStretch(1)

        self.order_type_combo.currentTextChanged.connect(self._on_order_type_changed)
        self.bracket_checkbox.toggled.connect(self._on_bracket_toggled)
        self._on_order_type_changed(self.order_type_combo.currentText())

    @staticmethod
    # Format one price for compact bid/ask display.
    def _format_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{float(value):.8f}".rstrip("0").rstrip(".")

    # Collect and normalize the current order form values.
    def get_order_request(self) -> dict[str, Any]:
        order_type = self.order_type_combo.currentText().strip().upper()
        side = self.side_combo.currentText().strip().upper()
        limit_price = float(self.limit_price_input.value()) if order_type == "LMT" else 0.0
        use_bracket = bool(self.bracket_checkbox.isChecked())
        take_profit_pct = float(self.take_profit_pct_input.value()) if use_bracket else None
        stop_loss_pct = float(self.stop_loss_pct_input.value()) if use_bracket else None
        rr_ratio = None
        if take_profit_pct is not None and stop_loss_pct is not None and stop_loss_pct > 0:
            rr_ratio = float(take_profit_pct) / float(stop_loss_pct)
        if order_type == "LMT":
            reference_price = limit_price
        else:
            reference_price = self._current_ask if side == "BUY" else self._current_bid
        return {
            "symbol": self.symbol_input.currentText().strip().upper(),
            "side": side,
            "order_type": order_type,
            "quantity": int(self.qty_input.value()),
            "volume": int(self.qty_input.value()),
            "limit_price": limit_price,
            "reference_price": reference_price,
            "use_bracket": use_bracket,
            "take_profit": None,
            "stop_loss": None,
            "take_profit_pct": take_profit_pct,
            "stop_loss_pct": stop_loss_pct,
            "rr_ratio": rr_ratio,
        }

    # Select order ticket symbol and add it to choices when missing.
    def set_symbol(self, symbol: str) -> None:
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            return
        if self.symbol_input.findText(normalized_symbol) < 0:
            self.symbol_input.addItem(normalized_symbol)
        self.symbol_input.setCurrentText(normalized_symbol)

    # Set current limit price value from external data.
    def set_limit_price(self, price: float) -> None:
        self.limit_price_input.setValue(float(price))

    # Set current market bid/ask values shown in ticket panel.
    def set_market_quote(self, bid: float | None, ask: float | None) -> None:
        self._current_bid = None if bid is None else float(bid)
        self._current_ask = None if ask is None else float(ask)
        self.market_quote_value.setText(
            f"{self._format_price(self._current_bid)} / {self._format_price(self._current_ask)}"
        )

    @staticmethod
    # Format one volume/currency pair for compact display.
    def _format_volume_with_currency(value: float | None, currency: str | None) -> str:
        if value is None:
            return "--"
        ccy = str(currency or "").strip().upper()
        if not ccy:
            return "--"
        return f"{float(value):,.2f} {ccy}"

    # Set whether market-driven limit updates are available.
    def set_limit_price_update_available(self, available: bool) -> None:
        self._limit_price_update_available = bool(available)
        self._sync_limit_price_update_button_state()

    # Keep the update button state aligned with order type and availability.
    def _sync_limit_price_update_button_state(self) -> None:
        order_type = self.order_type_combo.currentText().strip().upper()
        is_limit_order = order_type == "LMT"
        self.limit_price_update_button.setEnabled(is_limit_order and self._limit_price_update_available)

    # Keep bracket controls aligned with bracket toggle state.
    def _sync_bracket_fields_state(self) -> None:
        bracket_enabled = self.bracket_checkbox.isChecked()
        self.take_profit_pct_input.setEnabled(bracket_enabled)
        self.stop_loss_pct_input.setEnabled(bracket_enabled)

    # Toggle limit-specific fields based on selected order type.
    def _on_order_type_changed(self, value: str) -> None:
        order_type = str(value).strip().upper()
        is_limit_order = order_type == "LMT"
        self.limit_price_input.setEnabled(is_limit_order)
        self._sync_limit_price_update_button_state()
        self._sync_bracket_fields_state()

    # Toggle TP/SL controls when bracket checkbox changes.
    def _on_bracket_toggled(self, _checked: bool) -> None:
        self._sync_bracket_fields_state()

    # Display feedback text with a severity color.
    def set_feedback(self, message: str, level: str = "info") -> None:
        text = str(message).strip() or "--"
        level_key = str(level).strip().lower()
        if level_key == "error":
            color = "#e74c3c"
        elif level_key == "success":
            color = "#2ecc71"
        elif level_key == "preview":
            color = "#3498db"
        else:
            color = "#f1c40f"
        self.feedback_label.setText(text)
        self.feedback_label.setStyleSheet(f"color: {color};")

    # Apply status updates sent by the controller.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        if "bid" in payload or "ask" in payload:
            self.set_market_quote(payload.get("bid"), payload.get("ask"))
        if any(
            key in payload
            for key in (
                "requested_volume",
                "requested_currency",
                "required_volume",
                "required_currency",
                "available_required_volume",
                "funds_ok",
            )
        ):
            requested_text = self._format_volume_with_currency(
                payload.get("requested_volume", None),
                payload.get("requested_currency", None),
            )
            self.requested_volume_value.setText(requested_text)

            required_volume = payload.get("required_volume", None)
            required_currency = payload.get("required_currency", None)
            available_volume = payload.get("available_required_volume", None)
            required_text = self._format_volume_with_currency(required_volume, required_currency)
            if required_text != "--" and available_volume is not None:
                available_text = self._format_volume_with_currency(available_volume, required_currency)
                required_text = f"{required_text} (available: {available_text})"
            self.required_volume_value.setText(required_text)

            funds_ok = payload.get("funds_ok", None)
            if funds_ok is False:
                self.required_volume_value.setStyleSheet("color: #e74c3c;")
            else:
                self.required_volume_value.setStyleSheet("")
        if "message" in payload:
            level = str(payload.get("level", "info"))
            self.set_feedback(str(payload.get("message", "")), level=level)
