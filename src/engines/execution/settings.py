"""Execution-engine settings: the shared schema plus this service's knobs
(#333, audit 06#13). One name per variable, one place per service; the
engine reads these instead of ``os.getenv`` scattered over its modules.
Flags that compose may set to an empty string stay ``str`` and are parsed
by a property, so an empty value means "default", never a boot error."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from shared.config import Settings, truthy


class ExecutionSettings(Settings):
    # Defaults the engine had before #333 (compose sets all three explicitly).
    IB_HOST: str = Field(default="ib-gateway")
    IB_CLIENT_ID: int = Field(default=5)
    IB_READ_ONLY: str = Field(default="no")                 # must match the gateway's READ_ONLY_API

    SYNC_INTERVAL_S: float = Field(default=1.0)
    HEARTBEAT_INTERVAL_S: float = Field(default=10.0)
    STUCK_WATCH_INTERVAL_S: float = Field(default=60.0)
    STUCK_AFTER_S: float = Field(default=600.0)
    RECONCILE_INTERVAL_S: float = Field(default=60.0)      # stuck-order reconcile
    RECONCILE_POSITIONS_INTERVAL_S: float = Field(default=60.0)  # book vs broker breaks
    RECONCILE_AUTOCLOSE: str = Field(default="1")          # close a booking IB shows flat for > 1 h
    IB_RECONNECT_INTERVAL_S: float = Field(default=10.0)
    EXECUTION_SESSION_PASS_TIMEOUT_S: float = Field(default=60.0)
    EXECUTION_USE_COMBO: str = Field(default="0")          # BAG orders for combo-eligible structures
    EXECUTION_MAX_SYNC_AGE_S: float | None = Field(default=None)  # None = max(120, 10 x sync interval)
    REAPER_INTERVAL_S: float = Field(default=30.0)
    REAPER_TAU_STALE_S: float = Field(default=300.0)
    LIVE_SUBMIT_COLLAR_PCT: float = Field(default=0.5)

    @property
    def ib_read_only(self) -> bool:
        return truthy(self.IB_READ_ONLY)

    @property
    def execution_use_combo(self) -> bool:
        return truthy(self.EXECUTION_USE_COMBO)

    @property
    def reconcile_autoclose(self) -> bool:
        return truthy(self.RECONCILE_AUTOCLOSE)

    @property
    def max_sync_age_s(self) -> float:
        if self.EXECUTION_MAX_SYNC_AGE_S is not None:
            return float(self.EXECUTION_MAX_SYNC_AGE_S)
        return max(120.0, 10 * self.SYNC_INTERVAL_S)


@lru_cache(maxsize=1)
def get_execution_settings() -> ExecutionSettings:
    return ExecutionSettings()
