import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from typing import Any


class SmileChartPanel(QWidget):
    """Per-tenor volatility smile chart + drill-down strike table."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Smile Chart")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        # Tenor selector
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(QLabel("Tenor:"))
        self.tenor_combo = QComboBox()
        self.tenor_combo.setEditable(False)
        self.tenor_combo.currentTextChanged.connect(self._on_tenor_changed)
        header.addWidget(self.tenor_combo)
        header.addStretch(1)
        inner.addLayout(header)

        # Chart + Table side by side
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(6)

        # Chart (left)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Delta")
        self.plot.setLabel("left", "Vol (%)")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.addLegend(offset=(10, 10))
        self.plot.setMouseEnabled(x=False, y=False)

        self._iv_curve = self.plot.plot(
            [], [], pen=pg.mkPen("#3498db", width=2), name="IV Market",
            symbol="o", symbolSize=7, symbolBrush="#3498db",
        )

        content_row.addWidget(self.plot, 2)

        # Drill-down table (right)
        table_cols = ["Delta", "Strike", "IV Mid", "Skew"]
        self.table = QTableWidget(0, len(table_cols))
        self.table.setHorizontalHeaderLabels(table_cols)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        content_row.addWidget(self.table, 1)

        inner.addLayout(content_row)
        layout.addWidget(group)

        self._data: dict[str, dict] = {}

    def _on_tenor_changed(self, tenor: str) -> None:
        td = self._data.get(tenor)
        if not td:
            self._iv_curve.setData([], [])
            self.table.setRowCount(0)
            return

        # Update chart
        self._iv_curve.setData(td["deltas"], td["iv_market"])

        # Update drill-down table
        labels = td.get("delta_labels", [])
        strikes = td.get("strikes", [])
        ivs = td.get("iv_market", [])
        skews = td.get("skew", [])

        self.table.setRowCount(len(labels))
        for i, (dl, k, iv, sk) in enumerate(zip(labels, strikes, ivs, skews)):
            items = [
                QTableWidgetItem(dl),
                QTableWidgetItem(f"{k:.5f}"),
                QTableWidgetItem(f"{iv:.2f}"),
                QTableWidgetItem(f"{sk:+.2f}"),
            ]
            for col, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, col, item)

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        smiles = payload.get("smiles", {})
        if not smiles:
            return

        self._data = smiles
        current = self.tenor_combo.currentText()
        self.tenor_combo.blockSignals(True)
        self.tenor_combo.clear()
        self.tenor_combo.addItems(sorted(smiles.keys(), key=_tenor_sort_key))
        self.tenor_combo.blockSignals(False)

        if current in smiles:
            self.tenor_combo.setCurrentText(current)
        self._on_tenor_changed(self.tenor_combo.currentText())


def _tenor_sort_key(tenor: str) -> float:
    mapping = {"1M": 1, "2M": 2, "3M": 3, "4M": 4, "5M": 5, "6M": 6, "9M": 9, "1Y": 12}
    return mapping.get(tenor, 99)
