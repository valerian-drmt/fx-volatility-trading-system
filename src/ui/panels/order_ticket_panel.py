from typing import Any

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


class OrderConfirmDialog(QDialog):
    """Modal dialog showing IB preview data before order submission."""

    # Per-field formatting: (key, label, format)
    # format: "str"=as-is, "qty"=int, "price5"=5 dec, "price6"=6 dec,
    #          "usd"=comma+2dec, "margin"=comma+2dec+IB max filter
    _FIELDS = [
        ("contract", "Contract:", "str"),
        ("side", "Side:", "str"),
        ("quantity", "Quantity:", "qty"),
        ("right", "Right:", "str"),
        ("strike", "Strike:", "price5"),
        ("expiry", "Expiry:", "str"),
        ("bid", "Bid:", "price6"),
        ("ask", "Ask:", "price6"),
        ("mid", "Mid:", "price6"),
        ("iv", "IV:", "str"),
        ("notional", "Notional (USD):", "usd"),
        ("delta_usd", "Delta (USD):", "usd"),
        ("gamma_usd", "Gamma (USD/pip):", "usd"),
        ("vega_usd", "Vega (USD/1%):", "usd"),
        ("theta_usd", "Theta (USD/day):", "usd"),
        ("init_margin", "Init Margin:", "margin"),
        ("maint_margin", "Maint Margin:", "margin"),
        ("commission", "Commission:", "margin"),
        ("equity_change", "Equity Change:", "margin"),
    ]

    @staticmethod
    def _fmt(value: Any, fmt: str) -> str:
        if value is None or value == "--":
            return "--"
        if fmt == "str":
            return str(value)
        try:
            f = float(value)
        except (TypeError, ValueError):
            return str(value)
        if f > 1e+300 or f < -1e+300:
            return "--"
        if fmt == "qty":
            return f"{int(f):,}"
        if fmt == "price5":
            return f"{f:.5f}"
        if fmt == "price6":
            return f"{f:.6f}"
        if fmt == "usd":
            return f"{f:+,.2f}"
        # margin
        return f"{f:,.2f}"

    # Fields summed in Net Position section
    _NET_KEYS = ("notional", "delta_usd", "gamma_usd", "vega_usd", "theta_usd",
                 "init_margin", "maint_margin", "commission", "equity_change")

    def __init__(self, preview: dict[str, Any], parent: QWidget | None = None,
                 hedge_preview: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Order Confirmation")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Option / Future order section ──
        section_title = "Option Order" if hedge_preview else "Order Preview"
        self._add_section(layout, section_title, preview)

        # ── Delta Hedge section ──
        if hedge_preview:
            self._add_section(layout, "Delta Hedge (Future)", hedge_preview)
            self._add_net_section(layout, preview, hedge_preview)

        # ── Buttons ──
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_text = "Send Orders" if hedge_preview else "Send Order"
        self.confirm_btn = QPushButton(btn_text)
        self.confirm_btn.setStyleSheet(
            "background-color: #2ecc71; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.confirm_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)

    def _add_section(self, parent_layout: QVBoxLayout,
                     title: str, data: dict[str, Any]) -> None:
        section_label = QLabel(title)
        section_label.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 6px;")
        parent_layout.addWidget(section_label)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)
        for key, label, fmt in self._FIELDS:
            value = data.get(key)
            if value is None:
                continue
            display = self._fmt(value, fmt)
            val_label = QLabel(display)
            val_label.setStyleSheet("font-weight: bold;")
            form.addRow(label, val_label)
        parent_layout.addLayout(form)

    def _add_net_section(self, parent_layout: QVBoxLayout,
                         opt: dict[str, Any], hedge: dict[str, Any]) -> None:
        section_label = QLabel("Net Position")
        section_label.setStyleSheet("font-size: 12px; font-weight: bold; margin-top: 6px;")
        parent_layout.addWidget(section_label)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)

        for key, label, fmt in self._FIELDS:
            if key not in self._NET_KEYS:
                continue
            v1 = opt.get(key)
            v2 = hedge.get(key)
            try:
                f1 = float(v1) if v1 is not None and v1 != "--" else None
                f2 = float(v2) if v2 is not None and v2 != "--" else None
            except (TypeError, ValueError):
                continue
            if f1 is not None and f1 > 1e+300:
                f1 = None
            if f2 is not None and f2 > 1e+300:
                f2 = None
            if f1 is None and f2 is None:
                continue
            total = (f1 or 0) + (f2 or 0)
            display = self._fmt(total, fmt)
            val_label = QLabel(display)
            val_label.setStyleSheet("font-weight: bold;")
            form.addRow(f"Net {label}", val_label)

        parent_layout.addLayout(form)


class OrderTicketPanel(QWidget):
    order_confirmed = pyqtSignal(dict)
    order_preview_requested = pyqtSignal(dict)

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
        self._preview_dialog: QDialog | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        main_group = QGroupBox("Order Ticket")
        main_inner = QVBoxLayout(main_group)
        main_inner.setContentsMargins(8, 8, 8, 8)
        main_inner.setSpacing(8)

        # ── Spot order (full width, top) ──
        spot_order_group = QGroupBox("Spot")
        spot_order_inner = QVBoxLayout(spot_order_group)
        spot_order_inner.setContentsMargins(8, 6, 8, 6)
        spot_order_inner.setSpacing(4)

        self._spot_symbol_label = QLabel("EURUSD")
        self.spot_side_combo = QComboBox()
        self.spot_side_combo.addItems(["BUY", "SELL"])
        self._spot_type_label = QLabel("MKT")
        self.spot_qty_input = QSpinBox()
        self.spot_qty_input.setRange(0, 10_000_000)
        self.spot_qty_input.setSingleStep(25_000)
        self.spot_qty_input.setValue(0)
        self.spot_book_button = QPushButton("Book (Preview)")
        self.spot_book_button.setEnabled(True)
        self.spot_book_button.setStyleSheet("QPushButton { font-weight: bold; padding: 4px; }")
        self.spot_book_button.clicked.connect(self._on_spot_book_clicked)

        # Row 1: Symbol + Type (centered with spacing)
        row1 = QHBoxLayout()
        row1.addStretch(1)
        row1.addWidget(QLabel("Symbol:"))
        row1.addWidget(self._spot_symbol_label)
        row1.addSpacing(30)
        row1.addWidget(QLabel("Type:"))
        row1.addWidget(self._spot_type_label)
        row1.addStretch(1)
        spot_order_inner.addLayout(row1)

        # Row 2: Side + Quantity (centered with spacing)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(QLabel("Side:"))
        row2.addWidget(self.spot_side_combo)
        row2.addSpacing(30)
        row2.addWidget(QLabel("Qty:"))
        row2.addWidget(self.spot_qty_input)
        row2.addStretch(1)
        spot_order_inner.addLayout(row2)

        # Row 3: qty EUR → notional USD (centered)
        self.spot_notional_label = QLabel("-- EUR  →  -- USD")
        self.spot_notional_label.setAlignment(Qt.AlignCenter)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        row3.addWidget(self.spot_notional_label)
        row3.addStretch(1)
        spot_order_inner.addLayout(row3)

        # Row 4: Book button (centered)
        row4 = QHBoxLayout()
        row4.addStretch(1)
        row4.addWidget(self.spot_book_button)
        row4.addStretch(1)
        spot_order_inner.addLayout(row4)

        main_inner.addWidget(spot_order_group)

        # ── Two panels side by side: Future | Option ──
        panels_row = QHBoxLayout()
        panels_row.setContentsMargins(0, 0, 0, 0)
        panels_row.setSpacing(6)

        # ── Futures panel ──
        fut_group = QGroupBox("Future")
        fut_inner = QVBoxLayout(fut_group)
        fut_inner.setContentsMargins(8, 8, 8, 8)
        fut_inner.setSpacing(6)

        fut_form = QFormLayout()
        fut_form.setContentsMargins(0, 0, 0, 0)
        fut_form.setHorizontalSpacing(8)
        fut_form.setVerticalSpacing(4)

        self._fut_symbol_label = QLabel("EURUSD")
        self.side_combo = QComboBox()
        self.side_combo.addItems(["BUY", "SELL"])
        self._fut_type_label = QLabel("MKT")
        self._fut_contract_label = QLabel("6E - 125k")
        self.qty_input = QSpinBox()
        self.qty_input.setRange(0, 100_000)
        self.qty_input.setValue(0)

        self.fut_notional_label = QLabel("--")
        self.fut_delta_label = QLabel("--")

        fut_form.addRow("Symbol:", self._fut_symbol_label)
        fut_form.addRow("Side:", self.side_combo)
        fut_form.addRow("Type:", self._fut_type_label)
        fut_form.addRow("Contract:", self._fut_contract_label)
        fut_form.addRow("Quantity:", self.qty_input)
        fut_form.addRow("Notional (USD):", self.fut_notional_label)
        fut_form.addRow("Delta (USD):", self.fut_delta_label)
        fut_inner.addLayout(fut_form)

        self.fut_book_button = QPushButton("Book (Preview)")
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

        opt_form = QFormLayout()
        opt_form.setContentsMargins(0, 0, 0, 0)
        opt_form.setHorizontalSpacing(8)
        opt_form.setVerticalSpacing(4)

        self._opt_symbol_label = QLabel("EUR FOP (CME)")
        self.opt_side_combo = QComboBox()
        self.opt_side_combo.addItems(["BUY", "SELL"])
        self.opt_right_combo = QComboBox()
        self.opt_right_combo.addItems(["CALL", "PUT"])
        self.opt_expiry_combo = QComboBox()
        self.opt_expiry_combo.addItems(self.EXPIRIES)
        self.opt_expiry_combo.setCurrentText("3M")
        self.opt_strike_combo = QComboBox()
        self.opt_strike_combo.setEditable(False)
        self._opt_type_label = QLabel("MKT")
        self.opt_qty_input = QSpinBox()
        self.opt_qty_input.setRange(0, 100_000)
        self.opt_qty_input.setValue(0)

        self.opt_delta_hedge_checkbox = QCheckBox("Delta hedge")
        self.opt_delta_hedge_checkbox.setChecked(False)

        self._opt_strikes_by_tenor: dict[str, list[float]] = {}
        self._opt_atm_needed = True

        opt_form.addRow("Symbol:", self._opt_symbol_label)
        opt_form.addRow("Side:", self.opt_side_combo)
        opt_form.addRow("Right:", self.opt_right_combo)
        opt_form.addRow("Expiry:", self.opt_expiry_combo)
        opt_form.addRow("Strike:", self.opt_strike_combo)
        opt_form.addRow("Type:", self._opt_type_label)
        opt_form.addRow("Quantity:", self.opt_qty_input)
        opt_form.addRow("", self.opt_delta_hedge_checkbox)
        opt_inner.addLayout(opt_form)

        self.opt_book_button = QPushButton("Book (Preview)")
        self.opt_book_button.setEnabled(True)
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

        main_inner.addWidget(self.feedback_label)
        layout.addWidget(main_group, 1)

        # Wire spot notional update
        self.spot_qty_input.valueChanged.connect(lambda _: self._update_spot_notional())
        self.spot_side_combo.currentTextChanged.connect(lambda _: self._update_spot_notional())
        # Wire futures delta preview to side/qty changes
        self.side_combo.currentTextChanged.connect(lambda _: self._update_fut_delta())
        self.qty_input.valueChanged.connect(lambda _: (self._update_fut_notional(), self._update_fut_delta()))
        # Wire option tenor change → update strikes
        self.opt_expiry_combo.currentTextChanged.connect(lambda _: self._update_strikes_for_tenor())

    FUT_MULTIPLIER = 125_000
    FUT_IB_SYMBOL = "EUR"

    def _get_mid_price(self) -> float | None:
        if self._current_bid is not None and self._current_ask is not None:
            return (self._current_bid + self._current_ask) / 2.0
        return self._current_bid or self._current_ask

    def _update_fut_notional(self) -> None:
        mid = self._get_mid_price()
        qty = self.qty_input.value()
        multiplier = self.FUT_MULTIPLIER
        if mid is not None and mid > 0 and qty > 0:
            notional = mid * qty * multiplier
            self.fut_notional_label.setText(f"{notional:,.2f}")
        else:
            self.fut_notional_label.setText("--")

    def _update_fut_delta(self) -> None:
        mid = self._get_mid_price()
        qty = self.qty_input.value()
        multiplier = self.FUT_MULTIPLIER
        side = self.side_combo.currentText().strip().upper()
        sign = 1 if side == "BUY" else -1
        if mid is not None and mid > 0 and qty > 0:
            delta = sign * mid * qty * multiplier
            self.fut_delta_label.setText(f"{delta:+,.2f}")
        else:
            self.fut_delta_label.setText("--")

    def _update_spot_notional(self) -> None:
        mid = self._get_mid_price()
        qty = self.spot_qty_input.value()
        side = self.spot_side_combo.currentText().strip().upper()
        if mid is not None and mid > 0 and qty > 0:
            other = mid * qty
            if side == "SELL":
                # Sell EUR, receive USD: qty EUR → other USD
                self.spot_notional_label.setText(f"{qty:,.0f} EUR  →  {other:,.0f} USD")
            else:
                # Buy EUR, pay USD: other USD → qty EUR
                self.spot_notional_label.setText(f"{other:,.0f} USD  →  {qty:,.0f} EUR")
        else:
            self.spot_notional_label.setText("-- USD  →  -- EUR" if side == "BUY" else "-- EUR  →  -- USD")

    def _get_spot_order(self) -> dict[str, Any]:
        side = self.spot_side_combo.currentText().strip().upper()
        return {
            "instrument": "Spot",
            "symbol": self._spot_symbol_label.text().strip().upper(),
            "side": side,
            "order_type": "MKT",
            "quantity": int(self.spot_qty_input.value()),
            "volume": int(self.spot_qty_input.value()),
            "limit_price": 0.0,
            "reference_price": self._current_ask if side == "BUY" else self._current_bid,
            "use_bracket": False,
            "take_profit_pct": None,
            "stop_loss_pct": None,
        }

    def _is_market_open(self) -> bool:
        mid = self._get_mid_price()
        return mid is not None and mid > 0

    def _on_spot_book_clicked(self) -> None:
        if not self._is_market_open():
            self.set_feedback("Market is closed.", level="error")
            return
        order = self._get_spot_order()
        self.order_preview_requested.emit(order)

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
            "fut_symbol": self.FUT_IB_SYMBOL,
            "symbol": self._fut_symbol_label.text().strip().upper(),
            "side": side,
            "order_type": "MKT",
            "quantity": int(self.qty_input.value()),
            "volume": int(self.qty_input.value()),
            "multiplier": self.FUT_MULTIPLIER,
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
        strike_text = self.opt_strike_combo.currentText().strip()
        try:
            strike = float(strike_text)
        except ValueError:
            strike = 0.0
        return {
            "instrument": "Option",
            "symbol": "EUR",
            "side": self.opt_side_combo.currentText().strip().upper(),
            "right": self.opt_right_combo.currentText().strip().upper(),
            "tenor": self.opt_expiry_combo.currentText().strip(),
            "strike": strike,
            "order_type": "MKT",
            "quantity": int(self.opt_qty_input.value()),
            "limit_price": 0.0,
            "multiplier": self.FUT_MULTIPLIER,
            "delta_hedge": self.opt_delta_hedge_checkbox.isChecked(),
        }

    def set_option_chains(self, strikes_by_tenor: dict[str, list[float]]) -> None:
        """Populate strike combo from IB data. Called by controller after connect."""
        self._opt_strikes_by_tenor = strikes_by_tenor
        self._update_strikes_for_tenor()

    def _update_strikes_for_tenor(self) -> None:
        """Update strike combo for the currently selected tenor."""
        tenor = self.opt_expiry_combo.currentText().strip()
        strikes = self._opt_strikes_by_tenor.get(tenor, [])
        self.opt_strike_combo.clear()
        for s in strikes:
            self.opt_strike_combo.addItem(f"{s:.5f}")
        self._opt_atm_needed = True  # re-select ATM on next price tick
        # Try ATM now if price is available
        mid = self._get_mid_price()
        if mid and strikes:
            closest = min(strikes, key=lambda s: abs(s - mid))
            self.opt_strike_combo.setCurrentText(f"{closest:.5f}")
            self._opt_atm_needed = False

    def _on_fut_book_clicked(self) -> None:
        if not self._is_market_open():
            self.set_feedback("Market is closed.", level="error")
            return
        order = self._get_futures_order()
        self.order_preview_requested.emit(order)

    def show_preview_dialog(
        self, preview: dict[str, Any], on_confirmed: Any = None,
        hedge_preview: dict[str, Any] | None = None,
    ) -> None:
        """Show preview dialog (non-blocking). Calls on_confirmed() if user clicks Send Order."""
        dialog = OrderConfirmDialog(preview, parent=self, hedge_preview=hedge_preview)
        self._preview_dialog = dialog  # prevent garbage collection
        if callable(on_confirmed):
            dialog.accepted.connect(on_confirmed)
        dialog.finished.connect(lambda _: setattr(self, "_preview_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_opt_book_clicked(self) -> None:
        if not self._is_market_open():
            self.set_feedback("Market is closed.", level="error")
            return
        order = self._get_option_order()
        self.order_preview_requested.emit(order)

    def set_symbol(self, symbol: str) -> None:
        normalized = str(symbol).strip().upper()
        if not normalized:
            return
        self._spot_symbol_label.setText(normalized)
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
        self._update_spot_notional()
        self._update_fut_notional()
        self._update_fut_delta()
        # Auto-select ATM strike once after connect or tenor change
        mid = self._get_mid_price()
        if self._opt_atm_needed and mid is not None and self._opt_strikes_by_tenor:
            tenor = self.opt_expiry_combo.currentText().strip()
            strikes = self._opt_strikes_by_tenor.get(tenor, [])
            if strikes:
                closest = min(strikes, key=lambda s: abs(s - mid))
                self.opt_strike_combo.setCurrentText(f"{closest:.5f}")
                self._opt_atm_needed = False

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

    def set_option_fields(self, right: str, tenor: str, strike: float) -> None:
        """Pre-fill option fields (e.g. from Vol Scanner row click)."""
        if right:
            self.opt_right_combo.setCurrentText(right.upper())
        if tenor:
            self.opt_expiry_combo.setCurrentText(tenor)
        if strike:
            self.opt_strike_combo.setCurrentText(f"{float(strike):.5f}")

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
