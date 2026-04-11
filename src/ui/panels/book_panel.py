from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from typing import Any


def _fmt_k(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value / 1000:+,.1f}k"


class BookPanel(QWidget):
    """Greeks Summary — 5 lines with PnL total in bold."""

    _ROWS = [
        ("Delta Net", "delta_net", True),
        ("Vega Net", "vega_net", True),
        ("Gamma Net", "gamma_net", False),
        ("Theta Net", "theta_net", False),
        ("PnL Total", "pnl_total", False),
    ]

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Greeks Summary")
        form = QFormLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)

        self._labels: dict[str, QLabel] = {}
        for label_text, key, _use_k in self._ROWS:
            lbl = QLabel("--")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if key == "pnl_total":
                bold_font = QFont()
                bold_font.setBold(True)
                bold_font.setPointSize(bold_font.pointSize() + 1)
                lbl.setFont(bold_font)
            self._labels[key] = lbl
            form.addRow(f"{label_text}:", lbl)

        layout.addWidget(group)

    @staticmethod
    def _pnl_color(value: float | None) -> str:
        if value is None or value == 0:
            return "#aaaaaa"
        return "#2ecc71" if value > 0 else "#e74c3c"

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return
        for _label_text, key, use_k in self._ROWS:
            raw = summary.get(key)
            if use_k:
                text = _fmt_k(raw)
            elif raw is not None:
                text = f"{raw:+,.2f}"
            else:
                text = "--"
            lbl = self._labels[key]
            lbl.setText(text)
            color = self._pnl_color(raw)
            lbl.setStyleSheet(f"color: {color};")


OPEN_COLUMNS = [
    "Symbol", "Side", "Qty", "Tenor", "Strike", "Right",
    "Fill Price", "IV Now %", "Delta", "Vega", "Gamma", "Theta", "PnL",
]


class OpenPositionsPanel(QWidget):
    """Open positions table — placed independently in main_window."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Open Positions")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        self.table = QTableWidget(0, len(OPEN_COLUMNS))
        self.table.setHorizontalHeaderLabels(OPEN_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        inner.addWidget(self.table)
        layout.addWidget(group)

    @staticmethod
    def _fmt(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+,.2f}"

    @staticmethod
    def _fmt_iv(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.2f}"

    @staticmethod
    def _fmt_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.6f}"

    @staticmethod
    def _pnl_color(value: float | None) -> str:
        if value is None or value == 0:
            return "#aaaaaa"
        return "#2ecc71" if value > 0 else "#e74c3c"

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        positions = payload.get("open_positions", [])
        self.table.setRowCount(len(positions))
        for row, pos in enumerate(positions):
            fields = [
                (str(pos.get("symbol", "")), None),
                (str(pos.get("side", "")), None),
                (str(pos.get("qty", "")), None),
                (str(pos.get("tenor", "")), None),
                (str(pos.get("strike", "")), None),
                (str(pos.get("right", "")), None),
                (self._fmt_price(pos.get("fill_price")), None),
                (self._fmt_iv(pos.get("iv_now_pct")), None),
                (_fmt_k(pos.get("delta")), pos.get("delta")),
                (_fmt_k(pos.get("vega")), pos.get("vega")),
                (self._fmt(pos.get("gamma")), pos.get("gamma")),
                (self._fmt(pos.get("theta")), pos.get("theta")),
                (self._fmt(pos.get("pnl")), pos.get("pnl")),
            ]
            for col, (text, raw) in enumerate(fields):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if raw is not None:
                    item.setForeground(QColor(self._pnl_color(raw)))
                self.table.setItem(row, col, item)
