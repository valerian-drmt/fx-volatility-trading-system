from ib_insync import Forex, IB


class IBClient:
    def __init__(
        self,
        ib: IB,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 2,
        readonly: bool = True,
    ):
        self.ib = ib
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly

    def connect(self, timeout: float = 1.0):
        try:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                readonly=self.readonly,
                timeout=timeout,
            )
        except TypeError:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                readonly=self.readonly,
            )

    def connect_and_prepare(self, ticker=None):
        self.connect()
        if ticker is None:
            ticker = self.ib.reqMktData(Forex("EURUSD"))
        if hasattr(self.ib, "reqAccountSummary"):
            self.ib.reqAccountSummary()
        return ticker
