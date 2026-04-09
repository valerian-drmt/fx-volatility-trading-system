from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from typing import Any


SUMMARY_COLUMNS = ["Delta Net", "Vega Net", "Gamma Net", "Theta Net", "P&L Total"]

OPEN_COLUMNS = [
    "Symbol", "Side", "Qty", "Tenor", "Strike", "Right",
    "IV Entry %", "IV Now %", "Delta", "Vega", "Gamma", "Theta", "P&L",
]

CLOSED_COLUMNS = [
    "Symbol", "Side", "Qty", "Tenor", "Strike", "Right",
    "IV Entry %", "IV Close %", "P&L Total", "Verdict",
]


class BookPanel(QWidget):
    """Combined portfolio view: summary row + open positions + closed positions."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Book")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(8)

        # ── Summary table (1 row) ──
        inner.addWidget(QLabel("Portfolio Summary"))
        self.summary_table = QTableWidget(1, len(SUMMARY_COLUMNS))
        self.summary_table.setHorizontalHeaderLabels(SUMMARY_COLUMNS)
        self._configure_table(self.summary_table)
        self.summary_table.setMaximumHeight(58)
        self._init_summary_row()
        inner.addWidget(self.summary_table)

        # ── Open positions table ──
        inner.addWidget(QLabel("Open Positions"))
        self.open_table = QTableWidget(0, len(OPEN_COLUMNS))
        self.open_table.setHorizontalHeaderLabels(OPEN_COLUMNS)
        self._configure_table(self.open_table)
        inner.addWidget(self.open_table)

        # ── Closed positions table ──
        inner.addWidget(QLabel("Closed Positions"))
        self.closed_table = QTableWidget(0, len(CLOSED_COLUMNS))
        self.closed_table.setHorizontalHeaderLabels(CLOSED_COLUMNS)
        self._configure_table(self.closed_table)
        inner.addWidget(self.closed_table)

        layout.addWidget(group, 1)

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)

    def _init_summary_row(self) -> None:
        for col in range(len(SUMMARY_COLUMNS)):
            item = QTableWidgetItem("--")
            item.setTextAlignment(Qt.AlignCenter)
            self.summary_table.setItem(0, col, item)

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
    def _pnl_color(value: float | None) -> str:
        if value is None or value == 0:
            return "#aaaaaa"
        return "#2ecc71" if value > 0 else "#e74c3c"

    def _set_colored_item(self, table: QTableWidget, row: int, col: int, text: str, value: float | None) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if value is not None:
            item.setForeground(QColor(self._pnl_color(value)))
        table.setItem(row, col, item)

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        # ── Summary row ──
        summary = payload.get("summary")
        if isinstance(summary, dict):
            values = [
                (self._fmt(summary.get("delta_net")), summary.get("delta_net")),
                (self._fmt(summary.get("vega_net")), summary.get("vega_net")),
                (self._fmt(summary.get("gamma_net")), summary.get("gamma_net")),
                (self._fmt(summary.get("theta_net")), summary.get("theta_net")),
                (self._fmt(summary.get("pnl_total")), summary.get("pnl_total")),
            ]
            for col, (text, raw) in enumerate(values):
                self._set_colored_item(self.summary_table, 0, col, text, raw)

        # ── Open positions ──
        open_positions = payload.get("open_positions", [])
        self.open_table.setRowCount(len(open_positions))
        for row, pos in enumerate(open_positions):
            fields = [
                (str(pos.get("symbol", "")), None),
                (str(pos.get("side", "")), None),
                (str(pos.get("qty", "")), None),
                (str(pos.get("tenor", "")), None),
                (str(pos.get("strike", "")), None),
                (str(pos.get("right", "")), None),
                (self._fmt_iv(pos.get("iv_entry_pct")), None),
                (self._fmt_iv(pos.get("iv_now_pct")), None),
                (self._fmt(pos.get("delta")), pos.get("delta")),
                (self._fmt(pos.get("vega")), pos.get("vega")),
                (self._fmt(pos.get("gamma")), pos.get("gamma")),
                (self._fmt(pos.get("theta")), pos.get("theta")),
                (self._fmt(pos.get("pnl")), pos.get("pnl")),
            ]
            for col, (text, raw) in enumerate(fields):
                if raw is not None:
                    self._set_colored_item(self.open_table, row, col, text, raw)
                else:
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(Qt.AlignCenter)
                    self.open_table.setItem(row, col, item)

        # ── Closed positions ──
        closed_positions = payload.get("closed_positions", [])
        self.closed_table.setRowCount(len(closed_positions))
        for row, pos in enumerate(closed_positions):
            verdict = str(pos.get("verdict", "--"))
            fields = [
                (str(pos.get("symbol", "")), None),
                (str(pos.get("side", "")), None),
                (str(pos.get("qty", "")), None),
                (str(pos.get("tenor", "")), None),
                (str(pos.get("strike", "")), None),
                (str(pos.get("right", "")), None),
                (self._fmt_iv(pos.get("iv_entry_pct")), None),
                (self._fmt_iv(pos.get("iv_close_pct")), None),
                (self._fmt(pos.get("pnl_total")), pos.get("pnl_total")),
                (verdict, None),
            ]
            for col, (text, raw) in enumerate(fields):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if raw is not None:
                    item.setForeground(QColor(self._pnl_color(raw)))
                if col == 9:  # Verdict
                    if verdict == "BON":
                        item.setForeground(QColor("#2ecc71"))
                    elif verdict == "MAUVAIS":
                        item.setForeground(QColor("#e74c3c"))
                self.closed_table.setItem(row, col, item)
