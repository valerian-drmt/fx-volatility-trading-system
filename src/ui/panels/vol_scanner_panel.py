from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

COLUMNS = ["Tenor", "DTE", "σ Mid", "σ Fair", "Ecart", "Signal", "RV", "RR25", "BF25"]

COLOR_EXPENSIVE = QColor("#FCEBEB")
COLOR_CHEAP = QColor("#E1F5EE")

STATUS_IDLE = "idle"
STATUS_MARKET_CLOSED = "market_closed"
STATUS_COMPUTING = "computing"
STATUS_OK = "ok"

_STATUS_CONFIG = {
    STATUS_IDLE: ("#999999", "Engine not started"),
    STATUS_MARKET_CLOSED: ("#E74C3C", "Market closed"),
    STATUS_COMPUTING: ("#F39C12", "Computing..."),
    STATUS_OK: ("#2ECC71", "Scan complete"),
}


class VolScannerPanel(QWidget):
    row_clicked = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox()
        group.setTitle("")

        # Title bar with status indicator
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)

        title_label = QLabel("Vol Scanner")
        title_label.setStyleSheet("font-weight: bold;")
        title_layout.addWidget(title_label)

        self._status_circle = QLabel()
        self._status_circle.setFixedSize(12, 12)
        title_layout.addWidget(self._status_circle)

        self._status_label = QLabel()
        title_layout.addWidget(self._status_label)
        title_layout.addStretch()

        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)
        inner.addLayout(title_layout)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self._on_cell_clicked)
        inner.addWidget(self.table)
        layout.addWidget(group)

        self._rows_data: list[dict] = []
        self.set_status(STATUS_IDLE)

    def set_status(self, status: str) -> None:
        color, text = _STATUS_CONFIG.get(status, _STATUS_CONFIG[STATUS_IDLE])
        self._status_circle.setStyleSheet(
            f"background-color: {color}; border-radius: 6px; min-width: 12px; min-height: 12px;"
        )
        self._status_label.setText(text)

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._rows_data):
            return
        self.row_clicked.emit(self._rows_data[row])

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        error = payload.get("error")
        if error:
            self.table.setRowCount(0)
            self._rows_data.clear()
            return

        scanner_rows = payload.get("scanner_rows") or []
        self._rows_data = scanner_rows

        self.table.setRowCount(len(scanner_rows))
        for row_idx, r in enumerate(scanner_rows):
            signal = r.get("signal")
            bg = None
            if signal == "EXPENSIVE":
                bg = COLOR_EXPENSIVE
            elif signal == "CHEAP":
                bg = COLOR_CHEAP

            def _f(v, fmt=".2f"):
                return f"{v:{fmt}}" if v is not None else "—"

            items = [
                QTableWidgetItem(str(r.get("tenor", ""))),
                QTableWidgetItem(str(r.get("dte", ""))),
                QTableWidgetItem(_f(r.get("sigma_mid_pct"))),
                QTableWidgetItem(_f(r.get("sigma_fair_pct"))),
                QTableWidgetItem(_f(r.get("ecart_pct"), "+.2f") if r.get("ecart_pct") is not None else "—"),
                QTableWidgetItem(signal or "—"),
                QTableWidgetItem(_f(r.get("RV_pct"))),
                QTableWidgetItem(_f(r.get("RR25_pct"), "+.2f") if r.get("RR25_pct") is not None else "—"),
                QTableWidgetItem(_f(r.get("BF25_pct"), "+.2f") if r.get("BF25_pct") is not None else "—"),
            ]

            for col_idx, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                if bg is not None:
                    item.setBackground(QBrush(bg))
                self.table.setItem(row_idx, col_idx, item)
