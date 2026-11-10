"""Vol-engine settings: the shared schema plus the one knob the chain fetcher
still takes from the environment (#333). Width and concurrency moved to the
capacity dial in the versioned config (#372)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from shared.config import Settings


class VolSettings(Settings):
    # Gap between successive reqMktData subscriptions within a tenor: turns the
    # per-tenor burst into a rolling stream on the IB Gateway. 0 disables.
    VOL_SUBSCRIBE_GAP_S: float = Field(default=0.05)


@lru_cache(maxsize=1)
def get_vol_settings() -> VolSettings:
    return VolSettings()
