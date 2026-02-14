"""Performance computation service for live trading."""


class PerformanceService:
    def __init__(self, persistence):
        self.persistence = persistence

    def on_trade(self, trade):
        raise NotImplementedError

    def on_tick(self, tick):
        raise NotImplementedError

    def get_latest_metrics(self):
        raise NotImplementedError
