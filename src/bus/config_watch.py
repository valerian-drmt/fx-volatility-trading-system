"""Convergent config watcher (#329, audit 05#12 / 05#4).

Every engine that reads the versioned ``VolTradingConfig`` used to subscribe
to ``config:changed`` once, from env defaults, and apply whatever arrived:
a missed message, a Redis reconnect or a restart left it on stale knobs for
good, and an old message replayed late overwrote a newer one.

``ConfigWatcher`` makes the engine converge on the latest version whatever
happens on the bus:

* ``load_once()`` reads the latest row through the injected ``loader`` (the
  DB on start and on every resubscribe; a periodic re-read every
  ``reread_interval_s`` catches anything pub/sub dropped);
* ``offer(version, config)`` applies by version number and ignores an older
  or equal one, so order on the wire no longer matters;
* ``run()`` never dies: a failed subscribe or a dropped connection is a
  logged retry with backoff, followed by a resubscribe and a re-read.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bus.channels import CH_CONFIG_CHANGED
from core.config import VolTradingConfig

logger = logging.getLogger(__name__)

Loader = Callable[[], Awaitable[tuple[int, VolTradingConfig] | None]]
Apply = Callable[[int, VolTradingConfig], None]

_BACKOFF_MIN_S = 1.0
_BACKOFF_MAX_S = 30.0


def parse_config_message(data: Any) -> tuple[int, VolTradingConfig] | None:
    """``{"version": int, "config": {...}}`` -> (version, config), None on junk."""
    try:
        if isinstance(data, bytes):
            data = data.decode()
        payload = json.loads(data) if isinstance(data, str) else data
        return int(payload["version"]), VolTradingConfig.model_validate(payload["config"])
    except Exception as exc:
        logger.warning("config_payload_parse_failed reason=%s", exc)
        return None


class ConfigWatcher:
    def __init__(
        self,
        redis: Any,
        *,
        apply: Apply,
        loader: Loader | None = None,
        name: str = "engine",
        reread_interval_s: float = 300.0,
        poll_timeout_s: float = 1.0,
    ) -> None:
        self._redis = redis
        self._apply = apply
        self._loader = loader
        self._name = name
        self._reread_interval_s = reread_interval_s
        self._poll_timeout_s = poll_timeout_s
        self.version: int = -1
        self.config: VolTradingConfig | None = None
        self.applied: int = 0
        self.skipped_old: int = 0

    # ---- apply by version -----------------------------------------------------------------
    def offer(self, version: int, config: VolTradingConfig) -> bool:
        if version <= self.version:
            self.skipped_old += 1
            logger.info(
                "config_version_ignored", extra={"engine": self._name, "offered": version, "current": self.version},
            )
            return False
        self.version = version
        self.config = config
        self.applied += 1
        try:
            self._apply(version, config)
        except Exception:
            logger.exception("config_apply_failed", extra={"engine": self._name, "version": version})
        logger.info("config_applied", extra={"engine": self._name, "version": version})
        return True

    async def load_once(self) -> bool:
        """Re-read the latest version through the loader; False when none / failed."""
        if self._loader is None:
            return False
        try:
            row = await self._loader()
        except Exception:
            logger.exception("config_reload_failed", extra={"engine": self._name})
            return False
        if row is None:
            return False
        return self.offer(*row)

    # ---- the loop -------------------------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        backoff = _BACKOFF_MIN_S
        while not stop.is_set():
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(CH_CONFIG_CHANGED)
                await self.load_once()           # what pub/sub may have missed while we were away
                backoff = _BACKOFF_MIN_S
                next_reread = time.monotonic() + self._reread_interval_s
                while not stop.is_set():
                    try:
                        msg = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True, timeout=self._poll_timeout_s),
                            timeout=self._poll_timeout_s + 1.0,
                        )
                    except TimeoutError:
                        msg = None
                    if msg is not None and msg.get("type") == "message":
                        parsed = parse_config_message(msg.get("data"))
                        if parsed is not None:
                            self.offer(*parsed)
                    if time.monotonic() >= next_reread:
                        await self.load_once()
                        next_reread = time.monotonic() + self._reread_interval_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "config_watcher_disconnected retry_in=%.0fs reason=%s", backoff, exc,
                    extra={"engine": self._name},
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                    return
                except TimeoutError:
                    pass
                backoff = min(_BACKOFF_MAX_S, backoff * 2)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(CH_CONFIG_CHANGED)
                        await pubsub.close()
                    except Exception:
                        pass
