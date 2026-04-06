import math
from typing import Any

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget


class CurrencyAllocationChart(QWidget):
    _PALETTE = (
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    )

    # Build a compact donut chart for currency allocation percentages.
    def __init__(self) -> None:
        super().__init__()
        self._segments: list[tuple[str, float, QColor]] = []
        self.setMinimumWidth(190)
        self.setMinimumHeight(190)

    # Apply (currency, weight) rows and normalize them for drawing.
    def set_segments(self, rows: list[tuple[str, float]]) -> None:
        filtered: list[tuple[str, float]] = []
        for name, raw_value in rows:
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            filtered.append((str(name), value))

        total = sum(value for _name, value in filtered)
        if total <= 0:
            self._segments = []
            self.update()
            return

        normalized: list[tuple[str, float, QColor]] = []
        for index, (name, value) in enumerate(filtered):
            color_hex = self._PALETTE[index % len(self._PALETTE)]
            normalized.append((name, value / total, QColor(color_hex)))
        self._segments = normalized
        self.update()

    # Clear all chart segments.
    def clear_segments(self) -> None:
        self._segments = []
        self.update()

    # Return segment color map keyed by currency name.
    def get_color_map(self) -> dict[str, str]:
        return {name: color.name() for name, _ratio, color in self._segments}

    # Paint donut slices and fallback placeholder when no data exists.
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().window())

        if not self._segments:
            painter.setPen(QColor("#6e6e6e"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No allocation data")
            return

        area = self.rect().adjusted(10, 10, -10, -10)
        side = float(min(area.width(), area.height()))
        left = float(area.x()) + (float(area.width()) - side) / 2.0
        top = float(area.y()) + (float(area.height()) - side) / 2.0
        pie_rect = QRectF(left, top, side, side)

        start_angle = 90 * 16
        for _name, ratio, color in self._segments:
            span_angle = -int(round(ratio * 360.0 * 16.0))
            painter.setPen(QColor("#ffffff"))
            painter.setBrush(color)
            painter.drawPie(pie_rect, start_angle, span_angle)
            start_angle += span_angle

        hole_padding = side * 0.28
        hole_rect = pie_rect.adjusted(hole_padding, hole_padding, -hole_padding, -hole_padding)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.palette().window())
        painter.drawEllipse(hole_rect)

        # Draw currency codes around the donut with slice-matching colors.
        center_x = pie_rect.center().x()
        center_y = pie_rect.center().y()
        radius = side / 2.0
        label_radius = radius + 16.0
        start_deg = 90.0
        for name, ratio, color in self._segments:
            span_deg = ratio * 360.0
            mid_deg = start_deg - (span_deg / 2.0)
            angle_rad = math.radians(mid_deg)
            text_x = center_x + math.cos(angle_rad) * label_radius
            text_y = center_y - math.sin(angle_rad) * label_radius

            painter.setPen(color)
            text_width = painter.fontMetrics().horizontalAdvance(name)
            text_height = painter.fontMetrics().height()
            painter.drawText(
                int(text_x - (text_width / 2.0)),
                int(text_y + (text_height / 3.0)),
                name,
            )
            start_deg -= span_deg


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

    # Build account summary and exposure widgets.
    def __init__(self) -> None:
        super().__init__()

        self.fields = {tag: QLabel("--") for tag, _ in self._SUMMARY_FIELDS}
        self.open_positions_label = QLabel("--")
        self.exposure_label = QLabel("--")
        self.exposure_label.setWordWrap(True)
        self.currency_holdings_label = QLabel("--")
        self.currency_holdings_label.setWordWrap(True)
        self.currency_holdings_label.setTextFormat(Qt.RichText)
        self.currency_chart = CurrencyAllocationChart()

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
        positions_form.addRow("Currencies:", self.currency_holdings_label)

        layout.addLayout(summary_form)
        layout.addLayout(positions_form)
        layout.addWidget(self.currency_chart, 0, Qt.AlignHCenter)
        layout.addStretch(1)

    @staticmethod
    # Format position quantities without unnecessary decimals.
    def _format_position_qty(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")

    @staticmethod
    # Parse value into finite float or return None.
    def _parse_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(parsed):
            return None
        return parsed

    @staticmethod
    # Format currency amount with readable precision.
    def _format_amount(value: float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    # Build per-currency balances from account summary rows.
    def _extract_currency_balances(self, summary: list[Any]) -> dict[str, float]:
        tag_priority = {
            "TotalCashBalance": 3,
            "CashBalance": 2,
            "AvailableFunds": 1,
        }
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

    # Render per-currency balances and percentages.
    def _set_currency_holdings(self, summary: list[Any]) -> None:
        balances = self._extract_currency_balances(summary)
        if not balances:
            self.currency_holdings_label.setText("--")
            self.currency_chart.clear_segments()
            return

        totals_basis = sum(abs(amount) for amount in balances.values())
        if totals_basis <= 0:
            self.currency_holdings_label.setText(
                "\n".join(
                    f"{currency}: {self._format_amount(amount)}"
                    for currency, amount in sorted(balances.items(), key=lambda item: item[0])
                )
            )
            self.currency_chart.clear_segments()
            return

        ranked = sorted(balances.items(), key=lambda item: abs(item[1]), reverse=True)
        self.currency_chart.set_segments([(currency, abs(amount)) for currency, amount in ranked])
        color_map = self.currency_chart.get_color_map()
        self.currency_holdings_label.setText(
            "<br/>".join(
                (
                    f'<span style="color:{color_map.get(currency, "#333333")}; '
                    f'font-weight:600;">{currency}</span>: '
                    f"{self._format_amount(amount)} "
                    f"({(abs(amount) / totals_basis) * 100:.1f}%)"
                )
                for currency, amount in ranked
            )
        )

    # Refresh summary and top exposure values from payload data.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        summary = payload.get("summary") or []
        positions = payload.get("positions") or []

        for label in self.fields.values():
            label.setText("--")
        self.currency_holdings_label.setText("--")
        self.currency_chart.clear_segments()

        for item in summary:
            tag = getattr(item, "tag", None)
            target = self.fields.get(tag)
            if target is None:
                continue
            value = getattr(item, "value", "--")
            currency = getattr(item, "currency", "")
            target.setText(f"{value} {currency}".strip())

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
