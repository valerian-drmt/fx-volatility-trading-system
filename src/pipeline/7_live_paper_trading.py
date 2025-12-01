import sys
import math
from collections import deque

from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import QTimer
import pyqtgraph as pg

from ib_insync import IB, Forex


class LiveTickWindow(QMainWindow):
    def __init__(self, ib: IB, ticker, max_points: int = 500):
        super().__init__()

        self.ib = ib
        self.ticker = ticker
        self.max_points = max_points

        # --- internal state for last valid quote ---
        self.last_bid = None
        self.last_ask = None

        # --- data buffers ---
        self.bids = deque(maxlen=max_points)
        self.asks = deque(maxlen=max_points)

        # --- UI setup ---
        self.setWindowTitle("EURUSD Live Bid/Ask - IBKR")

        self.plot = pg.PlotWidget()
        self.setCentralWidget(self.plot)

        self.plot.setLabel("bottom", "Tick index")
        self.plot.setLabel("left", "Price")
        self.plot.addLegend()

        # two curves: bid (green), ask (red)
        self.bid_curve = self.plot.plot([], [], pen=pg.mkPen("g", width=2), name="Bid")
        self.ask_curve = self.plot.plot([], [], pen=pg.mkPen("r", width=2), name="Ask")

        # --- timer to drive both IB and plotting ---
        self.timer = QTimer(self)
        self.timer.setInterval(100)  # ms; ~10 updates per second
        self.timer.timeout.connect(self.update_data_and_plot)
        self.timer.start()

    def update_data_and_plot(self):
        """
        Called periodically by QTimer.
        1) Give ib_insync time to process incoming network messages.
        2) Read latest ticker snapshot.
        3) Append to buffers and update the curves.
        """
        # Step 1: let ib_insync pump the IB socket (non-blocking for small dt)
        self.ib.sleep(0)

        # Step 2: read latest quote
        bid = self.ticker.bid
        ask = self.ticker.ask

        # Ignore NaN (IB often updates bid and ask separately)
        if bid is not None and not math.isnan(bid):
            self.last_bid = bid
        if ask is not None and not math.isnan(ask):
            self.last_ask = ask

        # Only append if we have a complete quote (both bid and ask known)
        if self.last_bid is not None and self.last_ask is not None:
            self.bids.append(self.last_bid)
            self.asks.append(self.last_ask)

            x = list(range(len(self.bids)))
            self.bid_curve.setData(x, list(self.bids))
            self.ask_curve.setData(x, list(self.asks))


def main():
    # --- Qt application ---
    app = QApplication(sys.argv)

    # --- IB connection ---
    ib = IB()
    # Adapt host/port/clientId to your setup
    ib.connect("127.0.0.1", 4002, clientId=2, readonly=True)

    eurusd = Forex("EURUSD")
    ticker = ib.reqMktData(eurusd)

    # --- main window ---
    window = LiveTickWindow(ib, ticker, max_points=500)
    window.resize(900, 600)
    window.show()

    # Qt event loop (ib_insync is driven by QTimer via ib.sleep(0))
    exit_code = app.exec_()

    # Clean disconnect on close
    if ib.isConnected():
        ib.disconnect()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
