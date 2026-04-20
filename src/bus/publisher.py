"""Publisher helpers — every SET + PUBLISH call the engines will make.

Each helper writes to the live-state cache (SET with the TTL prescribed
by ``bus.keys``) and, when relevant, fan-outs through pub/sub so
subscribers (FastAPI WebSocket bridge in R4) get a push.

All writes to ``CH_TICKS`` are throttled to one message per
``TICK_PUBLISH_THROTTLE_MS`` per symbol — Market Data fires at ~5/s
and we do not want the websocket fire-hose at that rate. The cache
keys (``latest_spot``, ``latest_bid``, ``latest_ask``) are **never**
throttled : they always reflect the most recent tick because cache
reads are sparse and TTL refresh is cheap.

All helpers are :
    - async (use ``bus.get_redis()`` in production)
    - single-call : one argument set -> one consistent write
    - resilient to redis down : the caller catches
      ``redis.ConnectionError`` / ``TimeoutError`` and logs, the
      engine loop keeps running.

Reference : releases/architecture_finale_project/09-redis.md
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis

from bus import channels, keys

TICK_PUBLISH_THROTTLE_MS: int = 200

# Last PUBLISH monotonic-ms per symbol, used by the tick throttle.
# Module-level : one process = one throttle state. Tests reset via
# ``reset_throttle_for_tests()``.
_tick_last_publish_ms: dict[str, float] = {}


def _iso_utc(ts: datetime | float | None = None) -> str:
    """ISO-8601 UTC timestamp ending in ``Z`` — the format the v2 spec picks."""
    if ts is None:
        dt = datetime.now(UTC)
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, tz=UTC)
    else:
        dt = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _json_default(obj: Any) -> Any:
    """json.dumps default : handle Decimal and datetime without extra deps."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return _iso_utc(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(payload: dict) -> str:
    return json.dumps(payload, default=_json_default)


async def publish_tick(
    redis: Redis,
    symbol: str,
    bid: float,
    ask: float,
    mid: float,
    timestamp: datetime | float | None = None,
) -> bool:
    """Write latest_spot/bid/ask for ``symbol`` and PUBLISH on ticks (throttled).

    Returns ``True`` if a PUBLISH was emitted, ``False`` if the throttle
    swallowed it. Cache SETs always happen regardless of the throttle.
    """
    ts_str = _iso_utc(timestamp)

    await redis.set(
        keys.LATEST_SPOT.format(symbol=symbol), str(mid), ex=keys.TTL_SPOT
    )
    await redis.set(
        keys.LATEST_BID.format(symbol=symbol), str(bid), ex=keys.TTL_BID_ASK
    )
    await redis.set(
        keys.LATEST_ASK.format(symbol=symbol), str(ask), ex=keys.TTL_BID_ASK
    )

    now_ms = time.monotonic() * 1000
    last_ms = _tick_last_publish_ms.get(symbol, 0.0)
    if now_ms - last_ms < TICK_PUBLISH_THROTTLE_MS:
        return False
    _tick_last_publish_ms[symbol] = now_ms

    payload = _dumps({
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "timestamp": ts_str,
    })
    await redis.publish(channels.CH_TICKS, payload)
    return True


async def publish_account(
    redis: Redis,
    account_payload: dict,
    timestamp: datetime | float | None = None,
) -> None:
    """Write account_snapshot + PUBLISH on the account channel (not throttled)."""
    body = dict(account_payload)
    body.setdefault("timestamp", _iso_utc(timestamp))
    serialized = _dumps(body)
    await redis.set(keys.ACCOUNT_SNAPSHOT, serialized, ex=keys.TTL_ACCOUNT)
    await redis.publish(channels.CH_ACCOUNT, serialized)


async def publish_vol_update(
    redis: Redis,
    symbol: str,
    surface_data: dict,
    signals_data: dict | list,
    timestamp: datetime | float | None = None,
) -> None:
    """Write latest_vol_surface + latest_signals and PUBLISH on vol_update."""
    ts_str = _iso_utc(timestamp)
    surface_payload = _dumps({
        "symbol": symbol,
        "timestamp": ts_str,
        "surface": surface_data,
    })
    signals_payload = _dumps({
        "symbol": symbol,
        "timestamp": ts_str,
        "signals": signals_data,
    })

    await redis.set(
        keys.LATEST_VOL_SURFACE.format(symbol=symbol),
        surface_payload,
        ex=keys.TTL_VOL_SURFACE,
    )
    await redis.set(
        keys.LATEST_SIGNALS.format(symbol=symbol),
        signals_payload,
        ex=keys.TTL_SIGNALS,
    )
    # A single PUBLISH carrying the surface — subscribers poll signals
    # from cache if needed. Keeps the channel payload small (< 1 KB).
    await redis.publish(channels.CH_VOL_UPDATE, surface_payload)


async def publish_risk_update(
    redis: Redis,
    greeks: dict,
    pnl_curve: dict | None = None,
    timestamp: datetime | float | None = None,
) -> None:
    """Write latest_greeks + latest_pnl_curve and PUBLISH on risk_update."""
    ts_str = _iso_utc(timestamp)
    greeks_payload = _dumps({"timestamp": ts_str, "greeks": greeks})
    await redis.set(
        keys.LATEST_GREEKS_PORTFOLIO, greeks_payload, ex=keys.TTL_GREEKS
    )
    if pnl_curve is not None:
        pnl_payload = _dumps({"timestamp": ts_str, "curve": pnl_curve})
        await redis.set(
            keys.LATEST_PNL_CURVE, pnl_payload, ex=keys.TTL_PNL_CURVE
        )
    await redis.publish(channels.CH_RISK_UPDATE, greeks_payload)


async def set_heartbeat(
    redis: Redis,
    engine_name: str,
    timestamp: datetime | float | None = None,
) -> None:
    """SET heartbeat:{engine_name} with a 30s TTL.

    The value is the ISO-8601 UTC timestamp — handy for monitoring
    to compute ``now - ts`` age explicitly. Auto-expires if the engine
    crashes : missing key = DOWN, stale key = monitoring sees the age.
    """
    ts_str = _iso_utc(timestamp)
    await redis.set(
        keys.HEARTBEAT.format(engine_name=engine_name),
        ts_str,
        ex=keys.TTL_HEARTBEAT,
    )


def reset_throttle_for_tests() -> None:
    """Clear the per-symbol tick throttle state — only for tests."""
    _tick_last_publish_ms.clear()
