import math

from PyQt5.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget


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

    def __init__(self):
        super().__init__()

        self.fields = {tag: QLabel("--") for tag, _ in self._SUMMARY_FIELDS}
        self.open_positions_label = QLabel("--")
        self.exposure_label = QLabel("--")
        self.exposure_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        summary_form = QFormLayout()
        summary_form.setContentsMargins(0, 0, 0, 0)
        summary_form.setHorizontalSpacing(10)
        summary_form.setVerticalSpacing(4)
        for tag, title in self._SUMMARY_FIELDS:
            summary_form.addRow(title, self.fields[tag])

        positions_form = QFormLayout()
        positions_form.setContentsMargins(0, 0, 0, 0)
        positions_form.setHorizontalSpacing(10)
        positions_form.setVerticalSpacing(4)
        positions_form.addRow("Open positions:", self.open_positions_label)
        positions_form.addRow("Top exposure:", self.exposure_label)

        layout.addLayout(summary_form)
        layout.addLayout(positions_form)
        layout.addStretch(1)

    @staticmethod
    def _format_position_qty(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return

        summary = payload.get("summary") or []
        positions = payload.get("positions") or []

        for label in self.fields.values():
            label.setText("--")

        for item in summary:
            tag = getattr(item, "tag", None)
            target = self.fields.get(tag)
            if target is None:
                continue
            value = getattr(item, "value", "--")
            currency = getattr(item, "currency", "")
            target.setText(f"{value} {currency}".strip())

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
