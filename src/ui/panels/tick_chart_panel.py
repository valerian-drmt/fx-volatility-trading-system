import math
import time
from typing import Any, Callable

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QComboBox, QGroupBox, QLabel, QVBoxLayout, QWidget


class ChartPanel(QWidget):
    DEFAULT_SYMBOL = "EURUSD"
    FX_PAIRS = (
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
        "EURCHF", "AUDJPY",
    )
    BUCKET_INTERVAL_MS = 200          # aggregate ticks into 1 point every 200ms
    X_WINDOW_SECONDS = 30.0           # sliding window: show last 30 seconds (150 points)
    X_RIGHT_MARGIN_SECONDS = 1.5
    Y_RANGE_UPDATE_INTERVAL_MS = 500

    def __init__(self, max_points: int = 300) -> None:
        super().__init__()
        self._max_points = max(50, int(max_points))

        # Circular numpy buffers — O(1) append, no allocation
        self._x     = np.full(self._max_points, np.nan, dtype=np.float64)
        self._bid_y = np.full(self._max_points, np.nan, dtype=np.float64)
        self._ask_y = np.full(self._max_points, np.nan, dtype=np.float64)
        self._head  = 0
        self._count = 0

        # Pre-allocated reorder buffers
        self._disp_x   = np.full(self._max_points, np.nan, dtype=np.float64)
        self._disp_bid = np.full(self._max_points, np.nan, dtype=np.float64)
        self._disp_ask = np.full(self._max_points, np.nan, dtype=np.float64)

        self._start_time: float | None = None  # set on first data → chart starts at 0s
        self._last_bid: float | None = None
        self._last_ask: float | None = None

        # Aggregation bucket
        self._bucket_bids: list[float] = []
        self._bucket_asks: list[float] = []

        self._last_y_range_update_ms = 0.0
        self._last_y_min = 0.0
        self._last_y_max = 0.0
        self._symbol = self.DEFAULT_SYMBOL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        chart_group = QGroupBox("EUR/USD Spot")
        self._chart_group = chart_group
        chart_inner = QVBoxLayout(chart_group)
        chart_inner.setContentsMargins(8, 8, 8, 8)
        chart_inner.setSpacing(6)

        # Keep market_symbol_input interface for controller compatibility
        self.market_symbol_input = QComboBox()
        self.market_symbol_input.addItem(self._symbol)
        self.market_symbol_input.setCurrentText(self._symbol)
        self.market_symbol_input.setVisible(False)

        # pyqtgraph — optimized for streaming
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Time (s)")
        self.plot.setLabel("left", "Price")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.disableAutoRange()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()

        self.bid_curve = self.plot.plot([], [], pen=pg.mkPen("#2980b9", width=1.4))
        self.ask_curve = self.plot.plot([], [], pen=pg.mkPen("#e67e22", width=1.4))
        chart_inner.addWidget(self.plot)
        layout.addWidget(chart_group)
        self._prev_bid: float | None = None
        self._prev_ask: float | None = None
        self._bid_color = "#2ecc71"
        self._ask_color = "#2ecc71"
        self._bid_offer_label: QLabel | None = None
        self._on_price_update: Callable[[float | None, float | None], None] | None = None

        # Bucket timer: every 200ms, close the current bucket → 1 chart point
        self._bucket_timer = QTimer(self)
        self._bucket_timer.setTimerType(Qt.PreciseTimer)
        self._bucket_timer.setInterval(self.BUCKET_INTERVAL_MS)
        self._bucket_timer.timeout.connect(self._close_bucket)
        self._bucket_timer.start()

    @staticmethod
    def _is_valid_price(value: Any) -> bool:
        if value is None:
            return False
        if not isinstance(value, (int, float)):
            return False
        return not math.isnan(float(value))

    def _tick_to_bid_ask(self, tick: dict[str, Any]) -> tuple[float | None, float | None]:
        bid = tick.get("bid")
        ask = tick.get("ask")
        bid_price = float(bid) if self._is_valid_price(bid) else self._last_bid
        ask_price = float(ask) if self._is_valid_price(ask) else self._last_ask
        return bid_price, ask_price

    # O(1) append into circular buffer.
    def _append_prices(self, bid: float | None, ask: float | None) -> None:
        if self._start_time is None:
            self._start_time = time.monotonic()

        i = self._head % self._max_points
        self._x[i]     = time.monotonic() - self._start_time
        self._bid_y[i] = bid if bid is not None else np.nan
        self._ask_y[i] = ask if ask is not None else np.nan
        if bid is not None:
            self._last_bid = bid
        if ask is not None:
            self._last_ask = ask
        self._head += 1
        self._count = min(self._count + 1, self._max_points)

    # Zero-copy reorder into pre-allocated display buffers.
    def _get_display_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = self._count
        if n == 0:
            return self._disp_x[:0], self._disp_bid[:0], self._disp_ask[:0]
        if n < self._max_points:
            return self._x[:n], self._bid_y[:n], self._ask_y[:n]
        idx = self._head % self._max_points
        tail = self._max_points - idx
        self._disp_x[:tail]   = self._x[idx:]
        self._disp_x[tail:]   = self._x[:idx]
        self._disp_bid[:tail] = self._bid_y[idx:]
        self._disp_bid[tail:] = self._bid_y[:idx]
        self._disp_ask[:tail] = self._ask_y[idx:]
        self._disp_ask[tail:] = self._ask_y[:idx]
        return self._disp_x, self._disp_bid, self._disp_ask

    def _redraw(self) -> None:
        if self._count == 0:
            return

        x, bid, ask = self._get_display_arrays()

        self.bid_curve.setData(x, bid)
        self.ask_curve.setData(x, ask)

        # Fixed 60s window starting at 0, scrolls once data exceeds 60s
        now_s = x[-1]
        vb = self.plot.getViewBox()
        if now_s <= self.X_WINDOW_SECONDS:
            vb.setRange(xRange=(0, self.X_WINDOW_SECONDS), padding=0, update=False)
        else:
            x_right = now_s + self.X_RIGHT_MARGIN_SECONDS
            vb.setRange(xRange=(x_right - self.X_WINDOW_SECONDS, x_right), padding=0, update=False)

        # Throttled y-range update
        now_ms = time.monotonic() * 1000.0
        if (now_ms - self._last_y_range_update_ms) < self.Y_RANGE_UPDATE_INTERVAL_MS:
            vb.setRange(yRange=(self._last_y_min, self._last_y_max), padding=0, update=True)
            return
        self._last_y_range_update_ms = now_ms

        bid_valid = bid[~np.isnan(bid)] if bid.size else np.array([])
        ask_valid = ask[~np.isnan(ask)] if ask.size else np.array([])
        if bid_valid.size == 0 and ask_valid.size == 0:
            return

        vals = np.concatenate([bid_valid, ask_valid]) if bid_valid.size and ask_valid.size else (
            bid_valid if bid_valid.size else ask_valid
        )
        y_min = float(vals.min())
        y_max = float(vals.max())
        pad = (y_max - y_min) * 0.1 if y_min != y_max else max(0.0001, abs(y_min) * 0.001)
        self._last_y_min = y_min - pad
        self._last_y_max = y_max + pad
        vb.setRange(yRange=(self._last_y_min, self._last_y_max), padding=0, update=True)

    def set_symbol(self, symbol: str) -> None:
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            return
        if normalized_symbol == self._symbol:
            return
        self._symbol = normalized_symbol
        self._chart_group.setTitle("EUR/USD Spot")

    # Close the current bucket: average ticks or repeat last known value.
    def _close_bucket(self) -> None:
        if self._bucket_bids:
            avg_bid = sum(self._bucket_bids) / len(self._bucket_bids)
        else:
            avg_bid = self._last_bid

        if self._bucket_asks:
            avg_ask = sum(self._bucket_asks) / len(self._bucket_asks)
        else:
            avg_ask = self._last_ask

        self._bucket_bids.clear()
        self._bucket_asks.clear()

        if avg_bid is None and avg_ask is None:
            return

        self._append_prices(avg_bid, avg_ask)
        self._redraw()
        self._update_price_bar()

    def set_bid_offer_label(self, label: QLabel) -> None:
        self._bid_offer_label = label

    def set_on_price_update(self, callback: Callable[[float | None, float | None], None]) -> None:
        self._on_price_update = callback

    def _update_price_bar(self) -> None:
        bid = self._last_bid
        ask = self._last_ask

        if bid is not None:
            if self._prev_bid is not None and bid > self._prev_bid:
                self._bid_color = "#2ecc71"
            elif self._prev_bid is not None and bid < self._prev_bid:
                self._bid_color = "#e74c3c"
            self._prev_bid = bid

        if ask is not None:
            if self._prev_ask is not None and ask > self._prev_ask:
                self._ask_color = "#2ecc71"
            elif self._prev_ask is not None and ask < self._prev_ask:
                self._ask_color = "#e74c3c"
            self._prev_ask = ask

        if self._bid_offer_label is not None:
            bid_text = f"{bid:.6f}" if bid is not None else "--"
            ask_text = f"{ask:.6f}" if ask is not None else "--"
            self._bid_offer_label.setText(
                f"Bid / Offer : "
                f"<span style='color:{self._bid_color}'>{bid_text}</span>"
                f" / "
                f"<span style='color:{self._ask_color}'>{ask_text}</span>"
            )

        if self._on_price_update is not None:
            self._on_price_update(bid, ask)

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        if "symbol" in payload:
            self.set_symbol(str(payload.get("symbol", "")))

        if payload.get("clear"):
            self._x[:]     = np.nan
            self._bid_y[:] = np.nan
            self._ask_y[:] = np.nan
            self._head     = 0
            self._count    = 0
            self._start_time = None
            self._last_bid   = None
            self._last_ask   = None
            self._bucket_bids.clear()
            self._bucket_asks.clear()
            self.bid_curve.setData([], [])
            self.ask_curve.setData([], [])
            return

        ticks = payload.get("ticks") or []
        if not isinstance(ticks, list):
            return
        for tick in ticks:
            if not isinstance(tick, dict):
                continue
            bid_price, ask_price = self._tick_to_bid_ask(tick)
            if bid_price is not None:
                self._bucket_bids.append(bid_price)
            if ask_price is not None:
                self._bucket_asks.append(ask_price)
