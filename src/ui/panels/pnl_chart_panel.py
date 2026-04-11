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
        self._plot.setLabel("left", "PnL ($)")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setMouseEnabled(x=False, y=False)

        # Main PnL curve
        self._curve = self._plot.plot([], [], pen=pg.mkPen("#3498db", width=2))

        # Zero line
        self._zero_line = pg.InfiniteLine(pos=0, angle=0,
                                          pen=pg.mkPen("#666666", width=1, style=Qt.DashLine))
        self._plot.addItem(self._zero_line)

        # Spot vertical line
        self._spot_line = pg.InfiniteLine(pos=0, angle=90,
                                          pen=pg.mkPen("#e74c3c", width=1, style=Qt.DashLine))
        self._plot.addItem(self._spot_line)

        # Fill areas (profit / loss)
        self._fill_pos = pg.PlotCurveItem()
        self._fill_neg = pg.PlotCurveItem()
        self._curve_zero_pos = pg.PlotCurveItem()
        self._curve_zero_neg = pg.PlotCurveItem()
        self._fill_above = pg.FillBetweenItem(self._curve_zero_pos, self._fill_pos,
                                               brush=pg.mkBrush(46, 204, 113, 40))
        self._fill_below = pg.FillBetweenItem(self._curve_zero_neg, self._fill_neg,
                                               brush=pg.mkBrush(231, 76, 60, 40))
        self._plot.addItem(self._fill_above)
        self._plot.addItem(self._fill_below)

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
        zeros = np.zeros_like(pnls)

        # Main curve
        self._curve.setData(spots, pnls)

        # Fill: profit zones (pnl >= 0)
        pnl_pos = np.where(pnls >= 0, pnls, 0)
        self._fill_pos.setData(spots, pnl_pos)
        self._curve_zero_pos.setData(spots, zeros)

        # Fill: loss zones (pnl < 0)
        pnl_neg = np.where(pnls < 0, pnls, 0)
        self._fill_neg.setData(spots, pnl_neg)
        self._curve_zero_neg.setData(spots, zeros)

        # Spot marker
        self._spot_line.setValue(spot)
        pnl_now = float(np.interp(spot, spots, pnls))
        self._spot_dot.setData([spot], [pnl_now])
        self._pnl_label.setText(f" {pnl_now:+,.0f}")
        self._pnl_label.setPos(spot, pnl_now)
