from collections import deque

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox, QPushButton
import pyqtgraph as pg


class ChartPanel(QWidget):
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

        self.main_plot = pg.PlotWidget()
        self.main_plot.setTitle("Chart")
        self.main_plot.setLabel("bottom", "Tick #")
        self.main_plot.setLabel("left", "Price")
        root_layout.addWidget(self.main_plot)
        self.bid_item = self.main_plot.plot([], [], pen=pg.mkPen("g", width=1))
        self.ask_item = self.main_plot.plot([], [], pen=pg.mkPen("r", width=1))

        self.tick_index = 0
        self.tick_x = deque(maxlen=int(max_candles))
        self.tick_bid = deque(maxlen=int(max_candles))
        self.tick_ask = deque(maxlen=int(max_candles))

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return

        max_candles = payload.get("max_candles")
        if isinstance(max_candles, int) and max_candles > 0:
            if self.max_candles_input.value() != max_candles:
                self.max_candles_input.setValue(max_candles)
            if self.tick_x.maxlen != max_candles:
                self.tick_x = deque(self.tick_x, maxlen=max_candles)
                self.tick_bid = deque(self.tick_bid, maxlen=max_candles)
                self.tick_ask = deque(self.tick_ask, maxlen=max_candles)

        market_symbol = payload.get("market_symbol")
        if isinstance(market_symbol, str):
            symbol = market_symbol.strip().upper()
            if symbol and self.market_symbol_input.text() != symbol:
                self.market_symbol_input.setText(symbol)

        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is None or ask is None:
            return

        self.tick_index += 1
        self.tick_x.append(self.tick_index)
        self.tick_bid.append(bid)
        self.tick_ask.append(ask)

        self.bid_item.setData(list(self.tick_x), list(self.tick_bid))
        self.ask_item.setData(list(self.tick_x), list(self.tick_ask))

        min_price = min(min(self.tick_bid), min(self.tick_ask))
        max_price = max(max(self.tick_bid), max(self.tick_ask))
        if min_price == max_price:
            pad = max(1e-4, abs(min_price) * 0.001)
        else:
            pad = (max_price - min_price) * 0.1
        self.main_plot.setYRange(min_price - pad, max_price + pad, padding=0)
        self.main_plot.setXRange(self.tick_x[0], self.tick_x[-1], padding=0.02)
