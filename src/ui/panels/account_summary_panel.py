import math
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget


class PortfolioPanel(QWidget):
    _SUMMARY_FIELDS = (
        ("NetLiquidation", "Net Liq:"),
        ("TotalCashValue", "Cash:"),
        ("AvailableFunds", "Available:"),
        ("BuyingPower", "Buying Power:"),
        ("UnrealizedPnL", "Unrealized PnL:"),
        ("RealizedPnL", "Realized PnL:"),
        ("GrossPositionValue", "Gross Pos:"),
    )

    def __init__(self) -> None:
        super().__init__()

        self.fields = {tag: QLabel("--") for tag, _ in self._SUMMARY_FIELDS}
        self.open_positions_label = QLabel("--")
        self.exposure_label = QLabel("--")
        self.exposure_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Account Summary")
        group_inner = QVBoxLayout(group)
        group_inner.setContentsMargins(8, 8, 8, 8)
        group_inner.setSpacing(6)

        summary_form = QFormLayout()
        summary_form.setContentsMargins(0, 0, 0, 0)
        summary_form.setHorizontalSpacing(10)
        summary_form.setVerticalSpacing(4)
        for tag, title in self._SUMMARY_FIELDS:
            summary_form.addRow(title, self.fields[tag])
        summary_form.addRow("Open positions:", self.open_positions_label)

        group_inner.addLayout(summary_form)

        # Currencies sub-box
        currencies_group = QGroupBox("Currencies")
        currencies_form = QFormLayout(currencies_group)
        currencies_form.setContentsMargins(8, 8, 8, 8)
        currencies_form.setHorizontalSpacing(10)
        currencies_form.setVerticalSpacing(4)
        self.usd_balance_label = QLabel("--")
        self.eur_balance_label = QLabel("--")
        currencies_form.addRow("USD:", self.usd_balance_label)
        currencies_form.addRow("EUR:", self.eur_balance_label)
        group_inner.addWidget(currencies_group)

        layout.addWidget(group)

    def reset(self) -> None:
        for label in self.fields.values():
            label.setText("--")
        self.open_positions_label.setText("--")
        self.exposure_label.setText("--")
        self.usd_balance_label.setText("--")
        self.eur_balance_label.setText("--")

    @staticmethod
    def _format_position_qty(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(parsed):
            return None
        return parsed

    @staticmethod
    def _format_amount(value: float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    def _extract_currency_balances(self, summary: list[Any]) -> dict[str, float]:
        tag_priority = {"TotalCashBalance": 3, "CashBalance": 2, "AvailableFunds": 1}
        balances: dict[str, tuple[int, float]] = {}
        for item in summary:
            tag = str(getattr(item, "tag", "")).strip()
            priority = tag_priority.get(tag)
            if priority is None:
                continue
            currency = str(getattr(item, "currency", "")).strip().upper()
            if currency in {"BASE", "TOTAL"}:
                continue
            value = self._parse_float(getattr(item, "value", None))
            if not currency or value is None:
                continue
            current = balances.get(currency)
            if current is None or priority > current[0]:
                balances[currency] = (priority, value)
        return {currency: value for currency, (_priority, value) in balances.items()}

    def _set_currency_holdings(self, summary: list[Any]) -> None:
        balances = self._extract_currency_balances(summary)
        if not balances:
            self.usd_balance_label.setText("--")
            self.eur_balance_label.setText("--")
            return
        usd = balances.get("USD")
        eur = balances.get("EUR")
        self.usd_balance_label.setText(f"{usd / 1000:,.1f}k" if usd is not None else "--")
        self.eur_balance_label.setText(f"{eur / 1000:,.1f}k" if eur is not None else "--")

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        summary = payload.get("summary") or []
        positions = payload.get("positions") or []

        for label in self.fields.values():
            label.setText("--")
        self.usd_balance_label.setText("--")
        self.eur_balance_label.setText("--")

        for item in summary:
            tag = getattr(item, "tag", None)
            target = self.fields.get(tag)
            if target is None:
                continue
            raw = getattr(item, "value", "--")
            currency = str(getattr(item, "currency", "")).strip().upper()
            suffix = ""
            if currency == "USD":
                suffix = " USD/k"
            elif currency == "EUR":
                suffix = " EUR/k"
            elif currency:
                suffix = f" {currency}/k"
            parsed = self._parse_float(raw)
            if parsed is not None:
                text = f"{parsed / 1000:,.1f}{suffix}"
            else:
                text = f"{raw} {currency}".strip()
            target.setText(text)

        self._set_currency_holdings(summary)

        normalized_positions = []
        for pos in positions:
            contract = getattr(pos, "contract", None)
            symbol = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None) or "?"
            raw_qty = getattr(pos, "position", None)
            try:
                qty = float(raw_qty)
            except (TypeError, ValueError):
                continue
            if math.isnan(qty) or qty == 0:
                continue
            normalized_positions.append((symbol, qty))

        self.open_positions_label.setText(str(len(normalized_positions)))
        if not normalized_positions:
            self.exposure_label.setText("--")
            return

        normalized_positions.sort(key=lambda item: abs(item[1]), reverse=True)
        top_exposure = normalized_positions[:5]
        self.exposure_label.setText(
            ", ".join(f"{symbol}:{self._format_position_qty(qty)}" for symbol, qty in top_exposure)
        )
