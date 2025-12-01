import sys
import math
import time
from collections import deque

from PyQt5 import QtCore, QtGui
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import QTimer
import pyqtgraph as pg

from ib_insync import IB, Forex


from PyQt5 import QtCore, QtGui
import pyqtgraph as pg


class CandlestickItem(pg.GraphicsObject):
    """
    Simple candlestick item for pyqtgraph.
    Data is a list of (x, open, high, low, close).
    """
    def __init__(self, data):
        super().__init__()
        self.data = data
        self.picture = None
        self._generate_picture()

    def set_data(self, data):
        self.data = data
        self._generate_picture()
        self.update()

    def _generate_picture(self):
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        w = 0.6  # candle body width

        for (x, open_, high, low, close) in self.data:
            if close >= open_:
                pen = pg.mkPen('g')
                brush = pg.mkBrush('g')
            else:
                pen = pg.mkPen('r')
                brush = pg.mkBrush('r')

            p.setPen(pen)
            p.setBrush(brush)

            # wick
            p.drawLine(QtCore.QPointF(x, low), QtCore.QPointF(x, high))

            # body
            rect = QtCore.QRectF(x - w / 2, open_, w, close - open_)
            p.drawRect(rect.normalized())

        p.end()

    def paint(self, painter, *args):
        if self.picture is not None:
            painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        if self.picture is None:
            return QtCore.QRectF()
        # QPicture.boundingRect() -> QRect, convert to QRectF
        r = self.picture.boundingRect()
        return QtCore.QRectF(r.left(), r.top(), r.width(), r.height())



class LiveTickWindow(QMainWindow):
    def __init__(self, ib: IB, ticker, max_candles: int = 500):
        super().__init__()

        self.ib = ib
        self.ticker = ticker
        self.max_candles = max_candles

        # last valid quote
        self.last_bid = None
        self.last_ask = None

        # --- candlestick state ---
        # deque of (x_index, open, high, low, close)
        self.candles = deque(maxlen=max_candles)
        self.current_sec = None
        self.current_open = None
        self.current_high = None
        self.current_low = None
        self.current_close = None

        # --- UI setup ---
        self.setWindowTitle("EURUSD Live 1s Candlesticks - IBKR")

        self.plot = pg.PlotWidget()
        self.setCentralWidget(self.plot)

        self.plot.setLabel("bottom", "Candle index (1s)")
        self.plot.setLabel("left", "Price")

        # candlestick graphics item
        self.candle_item = CandlestickItem([])
        self.plot.addItem(self.candle_item)

        # timer to drive both IB and plotting
        self.timer = QTimer(self)
        self.timer.setInterval(100)  # ms; ~10 calls per second
        self.timer.timeout.connect(self.update_data_and_plot)
        self.timer.start()

    def _update_current_candle(self, price: float, now_sec: int):
        """
        Update or roll the current 1-second candle using a new tick price.
        """
        if self.current_sec is None:
            # first candle
            self.current_sec = now_sec
            self.current_open = price
            self.current_high = price
            self.current_low = price
            self.current_close = price
            return

        if now_sec == self.current_sec:
            # same second -> update OHLC
            self.current_high = max(self.current_high, price)
            self.current_low = min(self.current_low, price)
            self.current_close = price
        else:
            # second changed -> finalize previous candle and start a new one
            x_index = len(self.candles)  # simple incremental index on x-axis
            self.candles.append(
                (x_index, self.current_open, self.current_high, self.current_low, self.current_close)
            )

            # start new candle
            self.current_sec = now_sec
            self.current_open = price
            self.current_high = price
            self.current_low = price
            self.current_close = price

    def update_data_and_plot(self):
        """
        Periodic callback.
        1) Process IB messages.
        2) Read latest quote and update current 1s candle.
        3) Update the candlestick plot.
        """
        # 1) let ib_insync pump the IB socket
        self.ib.sleep(0)

        # 2) read latest quote
        bid = self.ticker.bid
        ask = self.ticker.ask

        if bid is not None and not math.isnan(bid):
            self.last_bid = bid
        if ask is not None and not math.isnan(ask):
            self.last_ask = ask

        if self.last_bid is None or self.last_ask is None:
            return  # not enough info yet

        # use mid-price for candles
        price = 0.5 * (self.last_bid + self.last_ask)
        now_sec = int(time.time())

        # update 1-second candle
        self._update_current_candle(price, now_sec)

        # 3) build data list for plotting:
        # all finalized candles + the current (partial) candle (optional)
        data = list(self.candles)

        # append current candle as the last one (as an "in-progress" candle)
        if self.current_sec is not None:
            x_index = len(self.candles)
            data.append(
                (x_index, self.current_open, self.current_high, self.current_low, self.current_close)
            )

        self.candle_item.set_data(data)


def main():
    # Qt application
    app = QApplication(sys.argv)

    # IB connection
    ib = IB()
    ib.connect("127.0.0.1", 4002, clientId=2, readonly=True)

    eurusd = Forex("EURUSD")
    ticker = ib.reqMktData(eurusd)

    # main window
    window = LiveTickWindow(ib, ticker, max_candles=500)
    window.resize(900, 600)
    window.show()

    # Qt event loop
    exit_code = app.exec_()

    # Clean disconnect on close
    if ib.isConnected():
        ib.disconnect()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
