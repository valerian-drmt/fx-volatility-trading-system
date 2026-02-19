from collections import deque
import math

from PyQt5 import QtCore, QtGui
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton
import pyqtgraph as pg


class PriceAxisItem(pg.AxisItem):
    def __init__(self, *args, decimals: int = 8, **kwargs):
        super().__init__(*args, **kwargs)
        self._decimals = max(1, int(decimals))

    def tickStrings(self, values, scale, spacing):
        return [f"{float(value):.{self._decimals}f}" for value in values]


class CandlestickItem(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self._picture = QtGui.QPicture()

    @staticmethod
    def _is_valid(value) -> bool:
        return isinstance(value, (int, float)) and not math.isnan(float(value))

    def set_data(self, data: list[tuple[float, float, float, float, float]]):
        picture = QtGui.QPicture()
        painter = QtGui.QPainter(picture)
        body_half_width = 0.35

        for x_pos, open_price, high_price, low_price, close_price in data:
            if not (
                self._is_valid(x_pos)
                and self._is_valid(open_price)
                and self._is_valid(high_price)
                and self._is_valid(low_price)
                and self._is_valid(close_price)
            ):
                continue

            # Ensure wick always encloses body, even if input payload has edge anomalies.
            high_price = max(high_price, open_price, close_price)
            low_price = min(low_price, open_price, close_price)

            is_bull = close_price >= open_price
            color = "#2ecc71" if is_bull else "#e74c3c"
            pen = pg.mkPen(color, width=1)
            brush = pg.mkBrush(color)

            painter.setPen(pen)
            painter.drawLine(QtCore.QPointF(x_pos, low_price), QtCore.QPointF(x_pos, high_price))

            body_height = close_price - open_price
            if abs(body_height) < 1e-12:
                painter.drawLine(
                    QtCore.QPointF(x_pos - body_half_width, open_price),
                    QtCore.QPointF(x_pos + body_half_width, open_price),
                )
            else:
                painter.setBrush(brush)
                painter.drawRect(
                    QtCore.QRectF(
                        x_pos - body_half_width,
                        open_price,
                        body_half_width * 2.0,
                        body_height,
                    )
                )

        painter.end()
        self.prepareGeometryChange()
        self._picture = picture
        self.update()

    def paint(self, painter, *_):
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self):
        return QtCore.QRectF(self._picture.boundingRect())


class ChartPanel(QWidget):
    _PRICE_DECIMALS = 8

    def __init__(self, max_candles: int, market_symbol: str, on_apply_and_save=None):
        super().__init__()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.market_symbol_input = QLineEdit(str(market_symbol).upper())
        self.market_symbol_input.setMaxLength(32)
        self.market_symbol_input.setMaximumWidth(160)

        self.max_candles_input = QSpinBox()
        self.max_candles_input.setRange(10, 50000)
        self.max_candles_input.setValue(int(max_candles))
        self.max_candles_input.setMaximumWidth(120)

        self.apply_save_button = QPushButton("Apply & Save")
        if callable(on_apply_and_save):
            self.apply_save_button.clicked.connect(on_apply_and_save)
        else:
            self.apply_save_button.setEnabled(False)

        controls_layout.addWidget(QLabel("Market symbol:"))
        controls_layout.addWidget(self.market_symbol_input)
        controls_layout.addSpacing(12)
        controls_layout.addWidget(QLabel("Max candles:"))
        controls_layout.addWidget(self.max_candles_input)
        controls_layout.addSpacing(12)
        controls_layout.addWidget(self.apply_save_button)
        controls_layout.addStretch(1)
        root_layout.addLayout(controls_layout)

        self.main_plot = pg.PlotWidget(
            axisItems={"left": PriceAxisItem(orientation="left", decimals=self._PRICE_DECIMALS)}
        )
        self.main_plot.setTitle("Candlestick Chart (1s)")
        self.main_plot.setLabel("bottom", "Candle #")
        self.main_plot.setLabel("left", "Price")
        self.main_plot.showGrid(x=True, y=True, alpha=0.15)
        root_layout.addWidget(self.main_plot)

        self.candle_item = CandlestickItem()
        self.main_plot.addItem(self.candle_item)

        self.candle_x = deque(maxlen=int(max_candles))
        self.candle_open = deque(maxlen=int(max_candles))
        self.candle_high = deque(maxlen=int(max_candles))
        self.candle_low = deque(maxlen=int(max_candles))
        self.candle_close = deque(maxlen=int(max_candles))
        self._last_candle_index = 0

    @staticmethod
    def _to_float(value) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        value_float = float(value)
        if math.isnan(value_float):
            return None
        return value_float

    def _append_candle(self, candle: dict):
        open_price = self._to_float(candle.get("open"))
        high_price = self._to_float(candle.get("high"))
        low_price = self._to_float(candle.get("low"))
        close_price = self._to_float(candle.get("close"))
        if open_price is None or high_price is None or low_price is None or close_price is None:
            return

        # Keep OHLC coherent in case payload is inconsistent for one sample.
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        index_value = candle.get("index")
        if isinstance(index_value, (int, float)):
            candle_index = int(index_value)
            self._last_candle_index = candle_index
        else:
            self._last_candle_index += 1
            candle_index = self._last_candle_index

        self.candle_x.append(candle_index)
        self.candle_open.append(open_price)
        self.candle_high.append(high_price)
        self.candle_low.append(low_price)
        self.candle_close.append(close_price)

    def _redraw_chart(self):
        if not self.candle_x:
            return

        candle_data = list(
            zip(
                self.candle_x,
                self.candle_open,
                self.candle_high,
                self.candle_low,
                self.candle_close,
            )
        )
        self.candle_item.set_data(candle_data)

        min_price = min(self.candle_low)
        max_price = max(self.candle_high)
        if min_price == max_price:
            pad = max(1e-4, abs(min_price) * 0.001)
        else:
            pad = (max_price - min_price) * 0.1
        self.main_plot.setYRange(min_price - pad, max_price + pad, padding=0)

        first_x = self.candle_x[0]
        last_x = self.candle_x[-1]
        self.main_plot.setXRange(first_x - 1, last_x + 1, padding=0)

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return

        max_candles = payload.get("max_candles")
        if isinstance(max_candles, int) and max_candles > 0:
            if self.max_candles_input.value() != max_candles:
                self.max_candles_input.setValue(max_candles)
            if self.candle_x.maxlen != max_candles:
                self.candle_x = deque(self.candle_x, maxlen=max_candles)
                self.candle_open = deque(self.candle_open, maxlen=max_candles)
                self.candle_high = deque(self.candle_high, maxlen=max_candles)
                self.candle_low = deque(self.candle_low, maxlen=max_candles)
                self.candle_close = deque(self.candle_close, maxlen=max_candles)

        market_symbol = payload.get("market_symbol")
        if isinstance(market_symbol, str):
            symbol = market_symbol.strip().upper()
            if symbol and self.market_symbol_input.text() != symbol:
                self.market_symbol_input.setText(symbol)

        candle = payload.get("candle")
        if not isinstance(candle, dict):
            return

        self._append_candle(candle)
        self._redraw_chart()
