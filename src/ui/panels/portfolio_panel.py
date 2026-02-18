from PyQt5.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel


class PortfolioPanel(QWidget):
    def __init__(self):
        super().__init__()

        self.fields = {
            "NetLiquidation": QLabel("--"),
            "TotalCashValue": QLabel("--"),
            "AvailableFunds": QLabel("--"),
            "UnrealizedPnL": QLabel("--"),
            "RealizedPnL": QLabel("--"),
            "DailyPnL": QLabel("--"),
            "GrossPositionValue": QLabel("--"),
        }
        self.exposure_label = QLabel("--")
        self.exposure_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        form.addRow("Net Liq:", self.fields["NetLiquidation"])
        form.addRow("Cash:", self.fields["TotalCashValue"])
        form.addRow("Available:", self.fields["AvailableFunds"])
        form.addRow("Unrealized PnL:", self.fields["UnrealizedPnL"])
        form.addRow("Realized PnL:", self.fields["RealizedPnL"])
        form.addRow("Daily PnL:", self.fields["DailyPnL"])
        form.addRow("Gross Pos:", self.fields["GrossPositionValue"])

        layout.addLayout(form)
        layout.addWidget(QLabel("Exposure (top 5):"))
        layout.addWidget(self.exposure_label)
        layout.addStretch(1)

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return
        summary = payload.get("summary") or []
        positions = payload.get("positions") or []

        for item in summary:
            tag = getattr(item, "tag", None)
            label = self.fields.get(tag)
            if label is None:
                continue
            value = getattr(item, "value", "--")
            currency = getattr(item, "currency", "")
            label.setText(f"{value} {currency}".strip())

        if not positions:
            self.exposure_label.setText("--")
            return

        items = []
        for pos in positions[:5]:
            contract = getattr(pos, "contract", None)
            symbol = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", None) or "?"
            position_value = getattr(pos, "position", "?")
            items.append(f"{symbol}:{position_value}")
        self.exposure_label.setText(", ".join(items))
