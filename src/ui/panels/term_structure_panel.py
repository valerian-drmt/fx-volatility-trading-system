from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QWidget


class TermStructurePanel(QWidget):
    # Demo tenor positions on x-axis (in months)
    TENOR_X = {"1W": 0.25, "2W": 0.5, "1M": 1, "2M": 2, "3M": 3, "4M": 4, "5M": 5, "6M": 6, "9M": 9, "1Y": 12}

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Term Structure")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tenor (months)")
        self.plot.setLabel("left", "Vol (%)")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.addLegend(offset=(10, 10))
        self.plot.setMouseEnabled(x=False, y=False)

        # IV market curve (blue)
        self._iv_curve = self.plot.plot(
            [], [], pen=pg.mkPen("#3498db", width=2), name="IV Market",
            symbol="o", symbolSize=6, symbolBrush="#3498db",
        )
        # σ fair curve (green)
        self._fair_curve = self.plot.plot(
            [], [], pen=pg.mkPen("#2ecc71", width=2), name="σ Fair",
            symbol="s", symbolSize=6, symbolBrush="#2ecc71",
        )
        # RV curve (orange dashed)
        self._rv_curve = self.plot.plot(
            [], [], pen=pg.mkPen("#e67e22", width=2, style=Qt.DashLine), name="RV",
            symbol="t", symbolSize=6, symbolBrush="#e67e22",
        )

        # Fill between IV and fair
        self._fill_above = pg.FillBetweenItem(self._iv_curve, self._fair_curve)
        self.plot.addItem(self._fill_above)

        inner.addWidget(self.plot)
        layout.addWidget(group)

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return

        tenors = payload.get("tenors", [])
        iv_values = payload.get("iv_market", [])
        fair_values = payload.get("sigma_fair", [])
        rv_values = payload.get("rv", [])

        if not tenors or not iv_values:
            return

        # Map tenor labels to x positions
        x = [self.TENOR_X.get(t, i) for i, t in enumerate(tenors)]

        self._iv_curve.setData(x, iv_values)
        if fair_values:
            self._fair_curve.setData(x, fair_values)
        if rv_values:
            self._rv_curve.setData(x, rv_values)

        # Update fill coloring based on IV vs fair
        if fair_values:
            iv_arr = np.array(iv_values, dtype=float)
            fair_arr = np.array(fair_values, dtype=float)
            avg_diff = float(np.mean(iv_arr - fair_arr))
            if avg_diff > 0:
                self._fill_above.setBrush(pg.mkBrush(231, 76, 60, 40))  # red transparent
            else:
                self._fill_above.setBrush(pg.mkBrush(46, 204, 113, 40))  # green transparent

