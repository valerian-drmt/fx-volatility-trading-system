import pyqtgraph as pg
from PyQt5.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from typing import Any


class SmileChartPanel(QWidget):
    """Per-tenor volatility smile: IV Market vs σ Fair plotted against delta."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Smile Chart — IV vs σ Fair")
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
        self._fair_curve = self.plot.plot(
            [], [], pen=pg.mkPen("#2ecc71", width=2), name="σ Fair",
            symbol="s", symbolSize=7, symbolBrush="#2ecc71",
        )

        self._fill = pg.FillBetweenItem(self._iv_curve, self._fair_curve)
        self.plot.addItem(self._fill)

        inner.addWidget(self.plot)
        layout.addWidget(group)

        self._data: dict[str, dict] = {}

    def _on_tenor_changed(self, tenor: str) -> None:
        tenor_data = self._data.get(tenor)
        if not tenor_data:
            self._iv_curve.setData([], [])
            self._fair_curve.setData([], [])
            return
        deltas = tenor_data["deltas"]
        self._iv_curve.setData(deltas, tenor_data["iv_market"])
        if tenor_data.get("sigma_fair"):
            self._fair_curve.setData(deltas, tenor_data["sigma_fair"])
            self._update_fill(tenor_data["iv_market"], tenor_data["sigma_fair"])
        else:
            self._fair_curve.setData([], [])

    def _update_fill(self, iv: list[float], fair: list[float]) -> None:
        avg_diff = sum(a - b for a, b in zip(iv, fair)) / max(len(iv), 1)
        if avg_diff > 0:
            self._fill.setBrush(pg.mkBrush(231, 76, 60, 40))
        else:
            self._fill.setBrush(pg.mkBrush(46, 204, 113, 40))

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
        self.tenor_combo.addItems(sorted(smiles.keys(), key=lambda t: _tenor_sort_key(t)))
        self.tenor_combo.blockSignals(False)

        if current in smiles:
            self.tenor_combo.setCurrentText(current)
        self._on_tenor_changed(self.tenor_combo.currentText())


def _tenor_sort_key(tenor: str) -> float:
    mapping = {"1W": 0.25, "2W": 0.5, "1M": 1, "2M": 2, "3M": 3, "6M": 6, "9M": 9, "1Y": 12, "2Y": 24}
    return mapping.get(tenor, 99)
