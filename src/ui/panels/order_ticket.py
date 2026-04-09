from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from typing import Any


class OrderConfirmDialog(QDialog):
    """Modal dialog summarizing the order before submission."""

    def __init__(self, order: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Order Confirmation")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Confirm Order")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)

        for key, label in [
            ("instrument", "Instrument:"),
            ("symbol", "Symbol:"),
            ("side", "Side:"),
            ("order_type", "Type:"),
            ("quantity", "Quantity:"),
            ("limit_price", "Limit Price:"),
            ("strike", "Strike:"),
            ("expiry", "Expiry:"),
            ("right", "Right:"),
        ]:
            value = order.get(key)
            if value is None:
                continue
            val_label = QLabel(str(value))
            val_label.setStyleSheet("font-weight: bold;")
            form.addRow(label, val_label)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.confirm_btn = QPushButton("Send Order")
        self.confirm_btn.setStyleSheet(
            "background-color: #2ecc71; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.confirm_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)


class OrderTicketPanel(QWidget):
    order_confirmed = pyqtSignal(dict)

    FX_PAIRS = (
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
        "EURCHF", "AUDJPY",
    )

    EXPIRIES = ("1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y")

    PRICE_UPDATE_INTERVAL_S = 2  # throttle mid price / notional / delta refresh

    def __init__(self) -> None:
        super().__init__()
        self._limit_price_update_available = False
        self._current_bid: float | None = None
        self._current_ask: float | None = None
        self._last_price_update: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        main_group = QGroupBox("Order Ticket")
        main_inner = QVBoxLayout(main_group)
        main_inner.setContentsMargins(8, 8, 8, 8)
        main_inner.setSpacing(8)

        # ── Two panels side by side: Futures | Option ──
        panels_row = QHBoxLayout()
        panels_row.setContentsMargins(0, 0, 0, 0)
        panels_row.setSpacing(6)

        # ── Futures panel ──
        fut_group = QGroupBox("Future")
        fut_inner = QVBoxLayout(fut_group)
        fut_inner.setContentsMargins(8, 8, 8, 8)
        fut_inner.setSpacing(6)

        self.fut_mid_price_label = QLabel("Mid Price : --")
        self.fut_mid_price_label.setTextFormat(Qt.RichText)
        self.fut_mid_price_label.setAlignment(Qt.AlignCenter)
        self.fut_mid_price_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        fut_inner.addWidget(self.fut_mid_price_label)
        self._fut_mid_color = "#ffffff"
        self._fut_prev_mid: float | None = None

        fut_form = QFormLayout()
        fut_form.setContentsMargins(0, 0, 0, 0)
        fut_form.setHorizontalSpacing(8)
        fut_form.setVerticalSpacing(4)

        self._fut_symbol_label = QLabel("EURUSD")
        self.side_combo = QComboBox()
        self.side_combo.addItems(["BUY", "SELL"])
        self._fut_type_label = QLabel("MKT")
        self.qty_input = QSpinBox()
        self.qty_input.setRange(0, 100_000_000)
        self.qty_input.setValue(0)

        self.fut_notional_label = QLabel("--")
        self.fut_delta_label = QLabel("--")

        fut_form.addRow("Symbol:", self._fut_symbol_label)
        fut_form.addRow("Side:", self.side_combo)
        fut_form.addRow("Type:", self._fut_type_label)
        fut_form.addRow("Contracts:", self.qty_input)
        fut_form.addRow("Notional:", self.fut_notional_label)
        fut_form.addRow("Delta:", self.fut_delta_label)
        fut_inner.addLayout(fut_form)

        self.fut_book_button = QPushButton("Book")
        self.fut_book_button.setEnabled(True)
        self.fut_book_button.setStyleSheet("QPushButton { font-weight: bold; padding: 4px; }")
        self.fut_book_button.clicked.connect(self._on_fut_book_clicked)
        fut_inner.addWidget(self.fut_book_button)
        fut_inner.addStretch(1)

        # ── Option panel ──
        opt_group = QGroupBox("Option")
        opt_inner = QVBoxLayout(opt_group)
        opt_inner.setContentsMargins(8, 8, 8, 8)
        opt_inner.setSpacing(6)

        self.opt_bid_offer_label = QLabel("Bid / Offer : -- / --")
        self.opt_bid_offer_label.setTextFormat(Qt.RichText)
        self.opt_bid_offer_label.setAlignment(Qt.AlignCenter)
        self.opt_bid_offer_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        opt_inner.addWidget(self.opt_bid_offer_label)

        opt_form = QFormLayout()
        opt_form.setContentsMargins(0, 0, 0, 0)
        opt_form.setHorizontalSpacing(8)
        opt_form.setVerticalSpacing(4)

        self._opt_symbol_label = QLabel("EURUSD")
        self.opt_side_combo = QComboBox()
        self.opt_side_combo.addItems(["BUY", "SELL"])
        self.opt_right_combo = QComboBox()
        self.opt_right_combo.addItems(["CALL", "PUT"])
        self.opt_expiry_combo = QComboBox()
        self.opt_expiry_combo.addItems(self.EXPIRIES)
        self.opt_expiry_combo.setCurrentText("3M")
        self.opt_strike_input = QDoubleSpinBox()
        self.opt_strike_input.setDecimals(5)
        self.opt_strike_input.setRange(0.0, 1_000_000.0)
        self.opt_strike_input.setValue(1.09250)
        self.opt_strike_input.setSingleStep(0.00050)
        self._opt_type_label = QLabel("MKT")
        self.opt_qty_input = QSpinBox()
        self.opt_qty_input.setRange(0, 100_000)
        self.opt_qty_input.setValue(0)

        self.opt_delta_label = QLabel("--")
        self.opt_gamma_label = QLabel("--")
        self.opt_theta_label = QLabel("--")
        self.opt_vega_label = QLabel("--")
        self.opt_delta_hedge_checkbox = QCheckBox("Delta hedge")
        self.opt_delta_hedge_checkbox.setChecked(False)

        opt_form.addRow("Symbol:", self._opt_symbol_label)
        opt_form.addRow("Side:", self.opt_side_combo)
        opt_form.addRow("Right:", self.opt_right_combo)
        opt_form.addRow("Expiry:", self.opt_expiry_combo)
        opt_form.addRow("Strike:", self.opt_strike_input)
        opt_form.addRow("Type:", self._opt_type_label)
        opt_form.addRow("Contracts:", self.opt_qty_input)
        opt_form.addRow("Delta:", self.opt_delta_label)
        opt_form.addRow("Gamma:", self.opt_gamma_label)
        opt_form.addRow("Theta:", self.opt_theta_label)
        opt_form.addRow("Vega:", self.opt_vega_label)
        opt_form.addRow("", self.opt_delta_hedge_checkbox)
        opt_inner.addLayout(opt_form)

        self.opt_book_button = QPushButton("Book")
        self.opt_book_button.setEnabled(False)
        self.opt_book_button.setStyleSheet("QPushButton { font-weight: bold; padding: 4px; }")
        self.opt_book_button.clicked.connect(self._on_opt_book_clicked)
        opt_inner.addWidget(self.opt_book_button)
        opt_inner.addStretch(1)

        for grp in (fut_group, opt_group):
            sp = grp.sizePolicy()
            sp.setHorizontalPolicy(QSizePolicy.Ignored)
            grp.setSizePolicy(sp)
        panels_row.addWidget(fut_group, 1)
        panels_row.addWidget(opt_group, 1)
        main_inner.addLayout(panels_row)

        # Hidden label for chart_panel's set_bid_offer_label (not displayed)
        self.bid_offer_label = QLabel(self)
        self.bid_offer_label.setVisible(False)

        # Hidden labels for controller compatibility
        self.requested_volume_value = QLabel("--")
        self.requested_volume_value.setVisible(False)
        self.required_volume_value = QLabel("--")
        self.required_volume_value.setVisible(False)
        self.market_quote_value = QLabel("-- / --")
        self.market_quote_value.setVisible(False)

        # Keep place_button for controller compatibility
        self.place_button = QPushButton()
        self.place_button.setVisible(False)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setVisible(False)

        # Hidden limit price widgets for controller compatibility
        self.limit_price_input = QDoubleSpinBox(self)
        self.limit_price_input.setVisible(False)
        self.limit_price_update_button = QPushButton(self)
        self.limit_price_update_button.setVisible(False)

        # Hidden bracket widgets (kept for compatibility)
        self.bracket_checkbox = QCheckBox(self)
        self.bracket_checkbox.setVisible(False)
        self.take_profit_pct_input = QDoubleSpinBox(self)
        self.take_profit_pct_input.setVisible(False)
        self.stop_loss_pct_input = QDoubleSpinBox(self)
        self.stop_loss_pct_input.setVisible(False)

        layout.addWidget(main_group, 1)

        # Wire futures delta preview to side/qty changes
        self.side_combo.currentTextChanged.connect(lambda _: self._update_fut_delta())
        self.qty_input.valueChanged.connect(lambda _: (self._update_fut_notional(), self._update_fut_delta()))

    def _get_mid_price(self) -> float | None:
        if self._current_bid is not None and self._current_ask is not None:
            return (self._current_bid + self._current_ask) / 2.0
        return self._current_bid or self._current_ask

    def _update_fut_notional(self) -> None:
        mid = self._get_mid_price()
        qty = self.qty_input.value()
        if mid is not None and mid > 0 and qty > 0:
            notional = mid * qty
            self.fut_notional_label.setText(f"{notional:,.5f}")
        else:
            self.fut_notional_label.setText("--")

    def _update_fut_delta(self) -> None:
        qty = self.qty_input.value()
        side = self.side_combo.currentText().strip().upper()
        sign = 1 if side == "BUY" else -1
        price = self._current_bid if self._current_bid is not None else self._current_ask
        if price is not None and price > 0 and qty > 0:
            delta = sign * price * qty
            self.fut_delta_label.setText(f"{delta:+,.5f}")
        else:
            self.fut_delta_label.setText("--")

    def set_option_greeks(
        self, delta: float | None, gamma: float | None,
        theta: float | None, vega: float | None,
    ) -> None:
        self.opt_delta_label.setText(f"{delta:+.4f}" if delta is not None else "--")
        self.opt_gamma_label.setText(f"{gamma:+.6f}" if gamma is not None else "--")
        self.opt_theta_label.setText(f"{theta:+.4f}" if theta is not None else "--")
        self.opt_vega_label.setText(f"{vega:+.4f}" if vega is not None else "--")

    @staticmethod
    def _format_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{float(value):.8f}".rstrip("0").rstrip(".")

    def get_order_request(self) -> dict[str, Any]:
        return self._get_futures_order()

    def _get_futures_order(self) -> dict[str, Any]:
        side = self.side_combo.currentText().strip().upper()
        reference_price = self._current_ask if side == "BUY" else self._current_bid
        return {
            "instrument": "Future",
            "symbol": self._fut_symbol_label.text().strip().upper(),
            "side": side,
            "order_type": "MKT",
            "quantity": int(self.qty_input.value()),
            "volume": int(self.qty_input.value()),
            "limit_price": 0.0,
            "reference_price": reference_price,
            "use_bracket": False,
            "take_profit": None,
            "stop_loss": None,
            "take_profit_pct": None,
            "stop_loss_pct": None,
            "rr_ratio": None,
        }

    def _get_option_order(self) -> dict[str, Any]:
        return {
            "instrument": "Option",
            "symbol": self._opt_symbol_label.text().strip().upper(),
            "side": self.opt_side_combo.currentText().strip().upper(),
            "right": self.opt_right_combo.currentText().strip().upper(),
            "expiry": self.opt_expiry_combo.currentText().strip(),
            "strike": float(self.opt_strike_input.value()),
            "order_type": "MKT",
            "quantity": int(self.opt_qty_input.value()),
            "limit_price": 0.0,
        }

    def _on_fut_book_clicked(self) -> None:
        order = self._get_futures_order()
        self._show_confirm_dialog(order)

    def _show_confirm_dialog(self, order: dict[str, Any]) -> None:
        """Non-blocking confirm dialog — avoids nested event loop crash."""
        dialog = OrderConfirmDialog(order, parent=self.window())
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.accepted.connect(lambda: self.order_confirmed.emit(order))
        dialog.open()

    def _on_strike_atm_clicked(self) -> None:
        bid = self._current_bid
        ask = self._current_ask
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            self.opt_strike_input.setValue(mid)
        elif bid is not None:
            self.opt_strike_input.setValue(bid)
        elif ask is not None:
            self.opt_strike_input.setValue(ask)

    def _on_opt_book_clicked(self) -> None:
        order = self._get_option_order()
        self._show_confirm_dialog(order)

    def set_symbol(self, symbol: str) -> None:
        normalized = str(symbol).strip().upper()
        if not normalized:
            return
        self._fut_symbol_label.setText(normalized)
        self._opt_symbol_label.setText(normalized)

    def set_limit_price(self, price: float) -> None:
        self.limit_price_input.setValue(float(price))

    def set_market_quote(self, bid: float | None, ask: float | None) -> None:
        # Store valid prices, keep previous value if None (avoids "--" flash)
        if bid is not None:
            self._current_bid = float(bid)
        if ask is not None:
            self._current_ask = float(ask)

        # Throttle UI updates
        import time
        now = time.monotonic()
        if now - self._last_price_update < self.PRICE_UPDATE_INTERVAL_S:
            return
        self._last_price_update = now

        self.market_quote_value.setText(
            f"{self._format_price(self._current_bid)} / {self._format_price(self._current_ask)}"
        )
        # Update mid price label with color
        mid = self._get_mid_price()
        if mid is not None:
            if self._fut_prev_mid is not None and mid > self._fut_prev_mid:
                self._fut_mid_color = "#2ecc71"
            elif self._fut_prev_mid is not None and mid < self._fut_prev_mid:
                self._fut_mid_color = "#e74c3c"
            self._fut_prev_mid = mid
            self.fut_mid_price_label.setText(
                f"Mid Price : <span style='color:{self._fut_mid_color}'>{mid:.6f}</span>"
            )
        self._update_fut_notional()
        self._update_fut_delta()

    @staticmethod
    def _format_volume_with_currency(value: float | None, currency: str | None) -> str:
        if value is None:
            return "--"
        ccy = str(currency or "").strip().upper()
        if not ccy:
            return "--"
        return f"{float(value):,.2f} {ccy}"

    def set_limit_price_update_available(self, available: bool) -> None:
        self._limit_price_update_available = bool(available)

    def set_option_fields(self, right: str, expiry: str, strike: float) -> None:
        """Pre-fill option fields (e.g. from Vol Scanner row click)."""
        if right:
            self.opt_right_combo.setCurrentText(right.upper())
        if expiry:
            self.opt_expiry_combo.setCurrentText(expiry)
        if strike:
            self.opt_strike_input.setValue(float(strike))

    def set_feedback(self, message: str, level: str = "info") -> None:
        text = str(message).strip()
        self.feedback_label.setVisible(bool(text))
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

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        if "bid" in payload or "ask" in payload:
            self.set_market_quote(payload.get("bid"), payload.get("ask"))
        if any(
            key in payload
            for key in (
                "requested_volume", "requested_currency",
                "required_volume", "required_currency",
                "available_required_volume", "funds_ok",
            )
        ):
            requested_text = self._format_volume_with_currency(
                payload.get("requested_volume"),
                payload.get("requested_currency"),
            )
            self.requested_volume_value.setText(requested_text)

            required_volume = payload.get("required_volume")
            required_currency = payload.get("required_currency")
            available_volume = payload.get("available_required_volume")
            required_text = self._format_volume_with_currency(required_volume, required_currency)
            if required_text != "--" and available_volume is not None:
                available_text = self._format_volume_with_currency(available_volume, required_currency)
                required_text = f"{required_text} (available: {available_text})"
            self.required_volume_value.setText(required_text)

            funds_ok = payload.get("funds_ok")
            if funds_ok is False:
                self.required_volume_value.setStyleSheet("color: #e74c3c;")
            else:
                self.required_volume_value.setStyleSheet("")
        if "message" in payload:
            level = str(payload.get("level", "info"))
            self.set_feedback(str(payload.get("message", "")), level=level)
