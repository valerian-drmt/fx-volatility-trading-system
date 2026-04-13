"""PnL vs Spot chart (pyqtgraph) — renders pre-computed data from RiskEngine."""
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QWidget


class PnlSpotPanel(QWidget):
    """Plot total portfolio PnL as a function of spot, current spot marked."""

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("PnL vs Spot")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        self._plot = pg.PlotWidget()
        self._plot.setLabel("bottom", "Spot")
        self._plot.setLabel("left", "PnL (USD)")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setMouseEnabled(x=False, y=False)

        # Main PnL curve (Now)
        self._curve = self._plot.plot([], [], pen=pg.mkPen("#3498db", width=2), name="Now")

        # Expiry payoff curve
        self._curve_expiry = self._plot.plot(
            [], [], pen=pg.mkPen("#f39c12", width=2, style=Qt.DashLine), name="Expiry")

        # Zero line
        self._zero_line = pg.InfiniteLine(pos=0, angle=0,
                                          pen=pg.mkPen("#666666", width=1, style=Qt.DashLine))
        self._plot.addItem(self._zero_line)

        # Spot vertical line
        self._spot_line = pg.InfiniteLine(pos=0, angle=90,
                                          pen=pg.mkPen("#e74c3c", width=1, style=Qt.DashLine))
        self._plot.addItem(self._spot_line)

        # Break-even vertical lines
        self._be_lines: list[pg.InfiniteLine] = []
        self._be_labels: list[pg.TextItem] = []

        # Current spot marker
        self._spot_dot = pg.ScatterPlotItem(size=8, brush="#e74c3c", pen=pg.mkPen(None))
        self._plot.addItem(self._spot_dot)

        # PnL annotation at spot
        self._pnl_label = pg.TextItem(color="#e74c3c", anchor=(0, 1))
        self._pnl_label.setFont(pg.QtGui.QFont("", 8))
        self._plot.addItem(self._pnl_label)

        inner.addWidget(self._plot)
        layout.addWidget(group)

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        spot = payload.get("spot")
        spots_raw = payload.get("spots")
        pnls_raw = payload.get("pnls")
        if not spot or spot <= 0 or not spots_raw or not pnls_raw:
            return

        spots = np.asarray(spots_raw, dtype=np.float64)
        pnls = np.asarray(pnls_raw, dtype=np.float64)

        # Main curve
        self._curve.setData(spots, pnls)

        # Expiry payoff
        pnls_expiry = payload.get("pnls_expiry")
        if pnls_expiry:
            self._curve_expiry.setData(spots, np.asarray(pnls_expiry, dtype=np.float64))

        # Spot marker
        self._spot_line.setValue(spot)
        pnl_now = float(np.interp(spot, spots, pnls))
        self._spot_dot.setData([spot], [pnl_now])
        self._pnl_label.setText(f" {pnl_now:+,.0f} USD")
        self._pnl_label.setPos(spot, pnl_now)

        # Break-even markers
        break_evens = payload.get("break_evens", [])
        for line in self._be_lines:
            self._plot.removeItem(line)
        for lbl in self._be_labels:
            self._plot.removeItem(lbl)
        self._be_lines.clear()
        self._be_labels.clear()
        for be in break_evens:
            line = pg.InfiniteLine(pos=be, angle=90,
                                   pen=pg.mkPen("#9b59b6", width=1, style=Qt.DotLine))
            self._plot.addItem(line)
            self._be_lines.append(line)
            lbl = pg.TextItem(f"BE {be:.4f}", color="#9b59b6", anchor=(0.5, 0))
            lbl.setFont(pg.QtGui.QFont("", 7))
            lbl.setPos(be, min(pnls))
            self._plot.addItem(lbl)
            self._be_labels.append(lbl)
