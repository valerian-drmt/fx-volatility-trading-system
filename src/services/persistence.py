"""Persistence layer for storing live ticks, trades, and performance snapshots."""


class Persistence:
    def __init__(self, db_url: str):
        self.db_url = db_url

    def write_tick(self, tick):
        raise NotImplementedError

    def write_trade(self, trade):
        raise NotImplementedError

    def write_perf_snapshot(self, snapshot):
        raise NotImplementedError
