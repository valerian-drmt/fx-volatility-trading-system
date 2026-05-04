"""Redis → WebSocket bridge — SUBSCRIBE Redis pub/sub then broadcast to WS clients."""
from __future__ import annotations

import asyncio
import logging

from redis import asyncio as aioredis
from redis import exceptions as redis_exc

from api.ws.connection_manager import ConnectionManager
from bus import channels

logger = logging.getLogger("api.ws.bridge")

# Channels forwarded from Redis to WebSocket clients. Matches the producers
# in R3 PR #3 (publish_tick, publish_account, publish_vol_update,
# publish_risk_update) and the system_alerts channel reserved for engine
# errors. Keep this list in sync with bus.channels.
_FORWARDED: tuple[str, ...] = (
    channels.CH_TICKS,
    channels.CH_ACCOUNT,
    channels.CH_VOL_UPDATE,
    channels.CH_RISK_UPDATE,
    channels.CH_SYSTEM_ALERTS,
    channels.CH_POSITIONS,
    channels.CH_EXIT_ALERTS,
)

# Pattern subscriptions — fanned out by the Redis-WS bridge below. We
# rebroadcast pattern messages on the *concrete* channel name they were
# published to, so ConnectionManager keying stays straightforward.
_FORWARDED_PATTERNS: tuple[str, ...] = (
    channels.CH_ORDERS_PATTERN,
)

_RECONNECT_BACKOFF_S: float = 2.0


async def redis_to_ws_bridge(
    redis: aioredis.Redis, manager: ConnectionManager
) -> None:
    """Long-running task : SUBSCRIBE, dispatch each message to ``manager.broadcast``.

    Swallows transient Redis errors and retries after a short backoff.
    Cancelled by lifespan shutdown — the ``CancelledError`` propagates
    cleanly so the task terminates without leaking the pubsub connection.
    """
    while True:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(*_FORWARDED)
            if _FORWARDED_PATTERNS:
                await pubsub.psubscribe(*_FORWARDED_PATTERNS)
            logger.info(
                "ws_bridge_subscribed",
                extra={
                    "channels": list(_FORWARDED),
                    "patterns": list(_FORWARDED_PATTERNS),
                },
            )
            async for msg in pubsub.listen():
                msg_type = msg.get("type")
                # Both ``message`` (subscribe) and ``pmessage`` (psubscribe)
                # carry a payload — we rebroadcast on the concrete channel
                # so ConnectionManager keying is uniform.
                if msg_type not in ("message", "pmessage"):
                    continue
                channel = msg["channel"]
                await manager.broadcast(channel, msg["data"])
        except asyncio.CancelledError:
            raise
        except (redis_exc.ConnectionError, redis_exc.TimeoutError, OSError) as e:
            logger.warning("ws_bridge_disconnected, retrying", extra={"error": str(e)})
            await asyncio.sleep(_RECONNECT_BACKOFF_S)
        except Exception:
            logger.exception("ws_bridge_unexpected, retrying")
            await asyncio.sleep(_RECONNECT_BACKOFF_S)
        finally:
            try:
                await pubsub.aclose()
            except Exception:
                pass
