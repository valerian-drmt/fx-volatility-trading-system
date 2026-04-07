from collections import deque
import math
import time
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ChartPanel(QWidget):
    DEFAULT_SYMBOL = "EURUSD"
    FX_PAIRS = (
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
        "EURCHF", "AUDJPY",
    )
    REDRAW_INTERVAL_MS = 50           # 20 fps — was 200 (5 fps), main source of perceived lag
    MAX_TICKS_PER_UPDATE = 50
    Y_RANGE_UPDATE_INTERVAL_MS = 400
    X_RANGE_LEFT_PADDING_POINTS = 2
    X_RANGE_RIGHT_SHIFT_POINTS = 12

    # Initialize chart buffers and plotting widgets.
    def __init__(self, max_points: int = 100) -> None:
        super().__init__()
        self._max_points = max(50, int(max_points))

        # --- numpy rolling buffers (zero-copy setData) ---
        # _head: next write index (mod _max_points)
        # _count: number of valid samples filled so far
        self._x     = np.empty(self._max_points, dtype=np.float64)
        self._bid_y = np.full(self._max_points, np.nan, dtype=np.float64)
        self._ask_y = np.full(self._max_points, np.nan, dtype=np.float64)
        self._head  = 0
        self._count = 0

        self._last_index = 0
        self._last_bid: float | None = None
        self._last_ask: float | None = None

        # deque(maxlen=N) drops oldest automatically — no list concat/slice
        self._pending_ticks: deque[dict[str, Any]] = deque(maxlen=self.MAX_TICKS_PER_UPDATE)
        self._pending_redraw = False
        self._last_y_range_update_ms = 0.0
        self._symbol = self.DEFAULT_SYMBOL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.market_symbol_input = QComboBox()
        self.market_symbol_input.setEditable(False)
        self.market_symbol_input.addItems(self.FX_PAIRS)
        self.market_symbol_input.setCurrentText(self._symbol)
        self.market_symbol_input.currentTextChanged.connect(self.set_symbol)
        header.addWidget(QLabel("Symbol:"))
        header.addWidget(self.market_symbol_input)
        self._title_label = QLabel()
        header.addWidget(self._title_label)
        header.addStretch(1)
        layout.addLayout(header)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Tick #")
        self.plot.setLabel("left", "Price")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.addLegend()
        self.bid_curve = self.plot.plot([], [], pen=pg.mkPen("#2980b9", width=1.4), name="Bid")
        self.ask_curve = self.plot.plot([], [], pen=pg.mkPen("#e67e22", width=1.4), name="Ask")
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

    # Write one sample into the circular numpy buffer — O(1), no allocation.
    def _append_prices(self, bid: float | None, ask: float | None) -> None:
        i = self._head % self._max_points
        self._last_index += 1
        self._x[i]     = float(self._last_index)
        self._bid_y[i] = bid if bid is not None else np.nan
        self._ask_y[i] = ask if ask is not None else np.nan
        if bid is not None:
            self._last_bid = bid
        if ask is not None:
            self._last_ask = ask
        self._head += 1
        self._count = min(self._count + 1, self._max_points)

    # Return contiguous view slices for the current window.
    # Returns (x, bid, ask) as numpy arrays in chronological order.
    def _get_display_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self._count < self._max_points:
            # Buffer not yet full — straight slice, no copy needed
            s = slice(0, self._count)
            return self._x[s], self._bid_y[s], self._ask_y[s]
        # Buffer full — data wraps around; reorder with np.roll
        # np.roll allocates a new array, but this only triggers once the
        # buffer is full, and it runs on the GPU-friendly contiguous path.
        idx = self._head % self._max_points
        return (
            np.roll(self._x,     -idx),
            np.roll(self._bid_y, -idx),
            np.roll(self._ask_y, -idx),
        )

    # Redraw curves and update y-axis range.
    def _redraw(self) -> None:
        if self._count == 0:
            return

        x, bid, ask = self._get_display_arrays()

        # setData on pre-existing PlotDataItem — fastest path, no scene rebuild
        self.bid_curve.setData(x, bid)
        self.ask_curve.setData(x, ask)
        self._update_x_range(x)

        now_ms = time.monotonic() * 1000.0
        if (now_ms - self._last_y_range_update_ms) < self.Y_RANGE_UPDATE_INTERVAL_MS:
            return
        self._last_y_range_update_ms = now_ms
        self._update_y_range(bid, ask)

    # Compute y-axis bounds using numpy nanmin/nanmax — no Python list comprehension.
    def _update_y_range(self, bid: np.ndarray, ask: np.ndarray) -> None:
        # nanmin/nanmax ignore NaN natively, no filtering loop required
        combined = np.concatenate([bid, ask])
        valid = combined[~np.isnan(combined)]
        if valid.size == 0:
            return
        y_min = float(valid.min())
        y_max = float(valid.max())
        if y_min == y_max:
            pad = max(0.0001, abs(y_min) * 0.001)
        else:
            pad = (y_max - y_min) * 0.1
        self.plot.setYRange(y_min - pad, y_max + pad, padding=0)

    # Keep a stable moving x-window with right offset for readability.
    def _update_x_range(self, x: np.ndarray) -> None:
        if x.size == 0:
            return
        left  = float(x[0])  - self.X_RANGE_LEFT_PADDING_POINTS
        right = float(x[-1]) + self.X_RANGE_RIGHT_SHIFT_POINTS
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

    # Drain pending ticks and repaint only when new data arrived.
    def _flush_redraw(self) -> None:
        if not self._pending_redraw:
            return
        # Swap out the pending queue atomically
        pending = list(self._pending_ticks)
        self._pending_ticks.clear()
        self._pending_redraw = False
        if not pending:
            return

        changed = False
        for tick in pending:
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
            self._x[:]     = 0.0
            self._bid_y[:] = np.nan
            self._ask_y[:] = np.nan
            self._head     = 0
            self._count    = 0
            self._last_index = 0
            self._last_bid   = None
            self._last_ask   = None
            self._pending_ticks.clear()
            self._pending_redraw = False
            self.bid_curve.setData([], [])
            self.ask_curve.setData([], [])
            return

        ticks = payload.get("ticks") or []
        if not isinstance(ticks, list):
            return
        filtered = [t for t in ticks if isinstance(t, dict)]
        if not filtered:
            return
        # deque(maxlen) handles overflow automatically — no concat/slice
        self._pending_ticks.extend(filtered)
        self._pending_redraw = True