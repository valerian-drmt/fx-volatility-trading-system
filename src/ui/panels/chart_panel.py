from collections import deque

from PyQt5.QtWidgets import QWidget, QGridLayout
import pyqtgraph as pg


class ChartPanel(QWidget):
    def __init__(self, max_candles: int = 500):
        super().__init__()

        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.plots = []
        titles = ["Chart 1", "Chart 2", "Chart 3", "Chart 4"]
        for index in range(4):
            plot = pg.PlotWidget()
            plot.setTitle(titles[index])
            plot.setLabel("bottom", "Tick #")
            plot.setLabel("left", "Price")
            self.plots.append(plot)
            layout.addWidget(plot, index // 2, index % 2)

        self.main_plot = self.plots[0]
        self.bid_item = self.main_plot.plot([], [], pen=pg.mkPen("g", width=1))
        self.ask_item = self.main_plot.plot([], [], pen=pg.mkPen("r", width=1))

        self.tick_index = 0
        self.tick_x = deque(maxlen=max_candles)
        self.tick_bid = deque(maxlen=max_candles)
        self.tick_ask = deque(maxlen=max_candles)

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return

        max_candles = payload.get("max_candles")
        if isinstance(max_candles, int) and max_candles > 0 and self.tick_x.maxlen != max_candles:
            self.tick_x = deque(self.tick_x, maxlen=max_candles)
            self.tick_bid = deque(self.tick_bid, maxlen=max_candles)
            self.tick_ask = deque(self.tick_ask, maxlen=max_candles)

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
