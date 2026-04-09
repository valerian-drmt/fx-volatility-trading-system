from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from typing import Any

COLUMNS = ["Tenor", "Delta", "Strike", "IV Mid %"]

# Tenor sort order
_TENOR_ORDER = {
    "1M": 1, "2M": 2, "3M": 3, "4M": 4, "5M": 5, "6M": 6,
    "7M": 7, "8M": 8, "9M": 9, "10M": 10, "11M": 11, "1Y": 12,
}
# Delta sort order within tenor
_DELTA_ORDER = {"10Δp": 1, "25Δp": 2, "ATM": 3, "25Δc": 4, "10Δc": 5}


class VolScannerPanel(QWidget):
    row_clicked = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._title = QLabel("Vol Scanner — waiting for data...")
        layout.addWidget(self._title)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        self._rows_data: list[dict] = []

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._rows_data):
            return
        self.row_clicked.emit(self._rows_data[row])

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        error = payload.get("error")
        if error:
            self._title.setText(f"Vol Scanner — error: {error}")
            self.table.setRowCount(0)
            self._rows_data.clear()
            return

        scanner_rows = payload.get("scanner_rows") or []
        spot = payload.get("spot", 0)
        self._title.setText(f"Vol Scanner — spot {spot:.5f} — {len(scanner_rows)} pillars")

        # Sort by tenor then delta order
        sorted_rows = sorted(
            scanner_rows,
            key=lambda r: (
                _TENOR_ORDER.get(r.get("tenor", ""), 99),
                _DELTA_ORDER.get(r.get("delta_label", ""), 99),
            ),
        )
        self._rows_data = sorted_rows

        self.table.setRowCount(len(sorted_rows))
        for row_idx, row_data in enumerate(sorted_rows):
            iv = row_data.get("iv_market_pct", 0) or 0
            items = [
                QTableWidgetItem(str(row_data.get("tenor", ""))),
                QTableWidgetItem(str(row_data.get("delta_label", ""))),
                QTableWidgetItem(f"{row_data.get('strike', 0):.5f}"),
                QTableWidgetItem(f"{iv:.2f}"),
            ]
            for col_idx, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)
