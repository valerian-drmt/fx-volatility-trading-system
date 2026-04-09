from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
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

COLUMNS = ["Tenor", "Delta", "Strike", "IV Market %", "σ Fair %", "Ecart %", "Signal", "Action"]

# Signal thresholds (vol %)
SIGNAL_THRESHOLD = 0.20

# Row background colors
COLOR_EXPENSIVE = QColor("#FCEBEB")
COLOR_CHEAP = QColor("#E1F5EE")
COLOR_FAIR = QColor()  # default / transparent


class VolScannerPanel(QWidget):
    # Emitted when user clicks a row: dict with tenor, strike, delta_label, right
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
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self.table)

        self._rows_data: list[dict] = []

    def _classify_signal(self, ecart: float) -> tuple[str, str, QColor]:
        """Return (signal_text, action_text, row_color) based on ecart."""
        if ecart > SIGNAL_THRESHOLD:
            return "EXPENSIVE", "Sell", COLOR_EXPENSIVE
        elif ecart < -SIGNAL_THRESHOLD:
            return "CHEAP", "Buy", COLOR_CHEAP
        else:
            return "FAIR", "—", COLOR_FAIR

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

        # Sort by |ecart| descending (best opportunities first)
        for row in scanner_rows:
            iv = row.get("iv_market_pct", 0) or 0
            fair = row.get("sigma_fair_pct", 0) or 0
            row["ecart_pct"] = iv - fair

        sorted_rows = sorted(scanner_rows, key=lambda r: abs(r.get("ecart_pct", 0)), reverse=True)
        self._rows_data = sorted_rows

        self.table.setRowCount(len(sorted_rows))
        for row_idx, row_data in enumerate(sorted_rows):
            iv = row_data.get("iv_market_pct", 0) or 0
            fair = row_data.get("sigma_fair_pct", 0) or 0
            ecart = row_data.get("ecart_pct", 0)
            signal, action, bg_color = self._classify_signal(ecart)

            items = [
                QTableWidgetItem(str(row_data.get("tenor", ""))),
                QTableWidgetItem(str(row_data.get("delta_label", ""))),
                QTableWidgetItem(f"{row_data.get('strike', 0):.5f}"),
                QTableWidgetItem(f"{iv:.2f}"),
                QTableWidgetItem(f"{fair:.2f}" if fair else "—"),
                QTableWidgetItem(f"{ecart:+.2f}" if fair else "—"),
                QTableWidgetItem(signal if fair else "—"),
                QTableWidgetItem(action if fair else "—"),
            ]

            for col_idx, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                if bg_color.isValid():
                    item.setBackground(QBrush(bg_color))
                self.table.setItem(row_idx, col_idx, item)
