from collections import deque
import math
import time
from typing import Any

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class ChartPanel(QWidget):
    DEFAULT_SYMBOL = "EURUSD"
    REDRAW_INTERVAL_MS = 200
    MAX_TICKS_PER_UPDATE = 50
    Y_RANGE_UPDATE_INTERVAL_MS = 400
    X_RANGE_LEFT_PADDING_POINTS = 2
    X_RANGE_RIGHT_SHIFT_POINTS = 12

    # Initialize chart buffers and plotting widgets.
    def __init__(self, max_points: int = 100) -> None:
        super().__init__()
        self._max_points = max(50, int(max_points))
        self._x = deque(maxlen=self._max_points)
        self._bid_y = deque(maxlen=self._max_points)
        self._ask_y = deque(maxlen=self._max_points)
        self._last_index = 0
        self._last_bid: float | None = None
        self._last_ask: float | None = None
        self._pending_ticks: list[dict[str, Any]] = []
        self._pending_redraw = False
        self._last_y_range_update_ms = 0.0
        self._symbol = self.DEFAULT_SYMBOL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._title_label = QLabel()
        layout.addWidget(self._title_label)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tick #")
        self.plot.setLabel("left", "Price")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.addLegend()
        self.bid_curve = self.plot.plot([], [], pen=pg.mkPen("#2980b9", width=1.4))
        self.ask_curve = self.plot.plot([], [], pen=pg.mkPen("#e67e22", width=1.4))
        layout.addWidget(self.plot)
        self._apply_chart_title()

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setTimerType(Qt.PreciseTimer)
        self._redraw_timer.setInterval(self.REDRAW_INTERVAL_MS)
        self._redraw_timer.timeout.connect(self._flush_redraw)
        self._redraw_timer.start()

    @staticmethod
    # Return True when a value can be used as a numeric price.
    def _is_valid_price(value: Any) -> bool:
        if value is None:
            return False
        if not isinstance(value, (int, float)):
            return False
        return not math.isnan(float(value))

    # Convert one tick payload into bid/ask price pair.
    def _tick_to_bid_ask(self, tick: dict[str, Any]) -> tuple[float | None, float | None]:
        bid = tick.get("bid")
        ask = tick.get("ask")
        bid_price = float(bid) if self._is_valid_price(bid) else self._last_bid
        ask_price = float(ask) if self._is_valid_price(ask) else self._last_ask
        return bid_price, ask_price

    # Append one bid/ask point to fixed-size series.
    def _append_prices(self, bid: float | None, ask: float | None) -> None:
        self._last_index += 1
        self._x.append(self._last_index)
        if bid is None:
            self._bid_y.append(float("nan"))
        else:
            self._bid_y.append(bid)
            self._last_bid = bid
        if ask is None:
            self._ask_y.append(float("nan"))
        else:
            self._ask_y.append(ask)
            self._last_ask = ask

    # Redraw curves and update y-axis range.
    def _redraw(self) -> None:
        if not self._x:
            return
        x_values = list(self._x)
        bid_values = list(self._bid_y)
        ask_values = list(self._ask_y)
        self.bid_curve.setData(x_values, bid_values)
        self.ask_curve.setData(x_values, ask_values)
        self._update_x_range()

        now_ms = time.monotonic() * 1000.0
        if (now_ms - self._last_y_range_update_ms) < self.Y_RANGE_UPDATE_INTERVAL_MS:
            return
        self._last_y_range_update_ms = now_ms

        all_prices = [
            value for value in (bid_values + ask_values) if isinstance(value, (int, float)) and not math.isnan(value)
        ]
        if not all_prices:
            return
        y_min = min(all_prices)
        y_max = max(all_prices)
        if y_min == y_max:
            pad = max(0.0001, abs(y_min) * 0.001)
        else:
            pad = (y_max - y_min) * 0.1
        self.plot.setYRange(y_min - pad, y_max + pad, padding=0)

    # Keep a stable moving x-window with right offset for readability.
    def _update_x_range(self) -> None:
        if not self._x:
            return
        left = float(self._x[0] - self.X_RANGE_LEFT_PADDING_POINTS)
        right = float(self._x[-1] + self.X_RANGE_RIGHT_SHIFT_POINTS)
        if right <= left:
            right = left + 1.0
        self.plot.setXRange(left, right, padding=0)

    # Apply current symbol to chart title controls.
    def _apply_chart_title(self) -> None:
        title = f"Live Tick Chart - {self._symbol}"
        self._title_label.setText(title)
        self.plot.setTitle(self._symbol)

    # Set active FX symbol shown in chart title.
    def set_symbol(self, symbol: str) -> None:
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            return
        if normalized_symbol == self._symbol:
            return
        self._symbol = normalized_symbol
        self._apply_chart_title()

    # Redraw only when new data arrived since the previous refresh slot.
    def _flush_redraw(self) -> None:
        if not self._pending_redraw:
            return
        pending_ticks = self._pending_ticks
        self._pending_ticks = []
        self._pending_redraw = False
        if not pending_ticks:
            return

        changed = False
        for tick in pending_ticks:
            bid_price, ask_price = self._tick_to_bid_ask(tick)
            if bid_price is None and ask_price is None:
                continue
            self._append_prices(bid_price, ask_price)
            changed = True

        if changed:
            self._redraw()

    # Apply incoming chart payload data.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        if "symbol" in payload:
            self.set_symbol(str(payload.get("symbol", "")))

        if payload.get("clear"):
            self._x.clear()
            self._bid_y.clear()
            self._ask_y.clear()
            self._last_index = 0
            self._last_bid = None
            self._last_ask = None
            self._pending_ticks = []
            self._pending_redraw = False
            self.bid_curve.setData([], [])
            self.ask_curve.setData([], [])
            return

        ticks = payload.get("ticks") or []
        if not isinstance(ticks, list):
            return
        filtered_ticks = [tick for tick in ticks if isinstance(tick, dict)]
        if not filtered_ticks:
            return
        merged_ticks = self._pending_ticks + filtered_ticks
        self._pending_ticks = merged_ticks[-self.MAX_TICKS_PER_UPDATE :]
        if self._pending_ticks:
            self._pending_redraw = True
