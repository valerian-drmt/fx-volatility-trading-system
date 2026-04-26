"""Redis key name templates for the live-state cache.

Every string follows the spec in ``09-redis.md``. Templates that include
``{symbol}`` or ``{engine_name}`` are meant to be used with
``.format()``. Other keys are already final.

Usage :

    from bus import keys
    await redis.set(keys.LATEST_SPOT.format(symbol="EURUSD"), "1.0857", ex=keys.TTL_SPOT)
    await redis.set(keys.LATEST_GREEKS_PORTFOLIO, payload, ex=keys.TTL_GREEKS)

The TTL constants live in this module too because each key has a
prescribed expiration in the architecture doc — storing them together
with the key itself keeps the prescription local and obvious.
"""

from __future__ import annotations

# --- keys --------------------------------------------------------------------

# Per-symbol live quotes (Market Data Engine → everyone)
LATEST_SPOT: str = "latest_spot:{symbol}"
LATEST_BID: str = "latest_bid:{symbol}"
LATEST_ASK: str = "latest_ask:{symbol}"

# Per-symbol vol snapshot (Vol Engine → Risk Engine, FastAPI)
LATEST_VOL_SURFACE: str = "latest_vol_surface:{symbol}"
LATEST_SIGNALS: str = "latest_signals:{symbol}"

# Portfolio-level aggregates (Risk Engine → FastAPI)
# No ``{symbol}`` : greeks and PnL are computed across the whole book.
LATEST_GREEKS_PORTFOLIO: str = "latest_greeks:portfolio"
LATEST_PNL_CURVE: str = "latest_pnl_curve"

# Account snapshot (Market Data → FastAPI)
ACCOUNT_SNAPSHOT: str = "account_snapshot"

# Market open/closed state per symbol (Market Data → everyone)
MARKET_STATUS: str = "market_status:{symbol}"

# Per-engine liveness (each engine writes its own key → monitoring)
HEARTBEAT: str = "heartbeat:{engine_name}"


# --- TTL policy (seconds) ----------------------------------------------------

# Short TTL : data is refreshed many times per TTL window. If a producer
# dies, consumers see a stale/missing key within 30s and can flag an issue.
TTL_SPOT: int = 30
TTL_BID_ASK: int = 30
TTL_GREEKS: int = 30
TTL_HEARTBEAT: int = 30

# Medium TTL : refreshed every 10s to 60s — 60s ceiling matches the
# snapshot cadence and leaves a 6× margin.
TTL_ACCOUNT: int = 60

# Long TTL : vol scan runs every ~3 minutes ; keep the surface valid for
# 10 minutes so a skipped or slow scan does not blank the UI.
TTL_VOL_SURFACE: int = 600
TTL_SIGNALS: int = 600

# Semi-static : market_status rarely flips, 5 min refresh is fine.
TTL_MARKET_STATUS: int = 300

# Same as greeks — refreshed every risk cycle.
TTL_PNL_CURVE: int = 30


# --- canonical engine names (used to format HEARTBEAT) ----------------------

ENGINE_MARKET_DATA: str = "market_data"
ENGINE_VOL: str = "vol_engine"
ENGINE_RISK: str = "risk_engine"
