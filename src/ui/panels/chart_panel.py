from collections import deque
import math

import pyqtgraph as pg
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class ChartPanel(QWidget):
    def __init__(self, max_points: int = 500):
        super().__init__()
        self._max_points = max(50, int(max_points))
        self._x = deque(maxlen=self._max_points)
        self._y = deque(maxlen=self._max_points)
        self._last_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Live Tick Chart"))
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tick #")
        self.plot.setLabel("left", "Price")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#2ecc71", width=1.6))
        layout.addWidget(self.plot)

    @staticmethod
    def _is_valid_price(value) -> bool:
        if value is None:
            return False
        if not isinstance(value, (int, float)):
            return False
        return not math.isnan(float(value))

    @staticmethod
    def _tick_to_mid_price(tick: dict) -> float | None:
        bid = tick.get("bid")
        ask = tick.get("ask")
        last = tick.get("last")
        has_bid = ChartPanel._is_valid_price(bid)
        has_ask = ChartPanel._is_valid_price(ask)
        if has_bid and has_ask:
            return (float(bid) + float(ask)) / 2.0
        if has_bid:
            return float(bid)
        if has_ask:
            return float(ask)
        if ChartPanel._is_valid_price(last):
            return float(last)
        return None

    def _append_price(self, price: float):
        self._last_index += 1
        self._x.append(self._last_index)
        self._y.append(price)

    def _redraw(self):
        if not self._x:
            return
        self.curve.setData(list(self._x), list(self._y))
        y_min = min(self._y)
        y_max = max(self._y)
        if y_min == y_max:
            pad = max(0.0001, abs(y_min) * 0.001)
        else:
            pad = (y_max - y_min) * 0.1
        self.plot.setYRange(y_min - pad, y_max + pad, padding=0)

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return

        if payload.get("clear"):
            self._x.clear()
            self._y.clear()
            self._last_index = 0
            self.curve.setData([], [])
            return

        ticks = payload.get("ticks") or []
        if not isinstance(ticks, list):
            return

        changed = False
        for tick in ticks:
            if not isinstance(tick, dict):
                continue
            price = self._tick_to_mid_price(tick)
            if price is None:
                continue
            self._append_price(price)
            changed = True

        if changed:
            self._redraw()
