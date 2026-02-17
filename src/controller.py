import sys

from PyQt5.QtWidgets import QApplication
from ib_insync import IB

from services.ib_client import IBClient
from ui.main_window import LiveTickWindow


class Controller:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 2,
        readonly: bool = True,
        max_candles: int = 500,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self.max_candles = max_candles

        self.app = QApplication(sys.argv)
        self.ib = IB()
        self.window = None

    def _connect_ib(self):
        ticker = None
        bootstrap_client = IBClient(
            ib=self.ib,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=self.readonly,
        )
        try:
            ticker = bootstrap_client.connect_and_prepare()
        except Exception as exc:
            print(f"IB startup connection failed: {exc}")
        return ticker

    def _create_window(self, ticker):
        self.window = LiveTickWindow(
            self.ib,
            ticker=ticker,
            max_candles=self.max_candles,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
            readonly=self.readonly,
            connect_on_start=False,
        )
        self.window.resize(900, 600)
        self.window.show()

    def run(self) -> int:
        ticker = self._connect_ib()
        self._create_window(ticker)
        exit_code = self.app.exec_()

        if self.ib.isConnected():
            self.ib.disconnect()

        return exit_code

