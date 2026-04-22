"""Async VolEngine — standalone service version.

One cycle roughly every three minutes :

1. ``GET latest_spot:<symbol>`` on Redis. Skip if missing (market-data down).
2. Call the injected ``fetch_fop_chain(F)`` to get (delta, iv, strike)
   observations per tenor. Real impl reads IB FOP chain ; tests pass
   a fixture.
3. PCHIP-interpolate the smile via ``core.vol.pchip_smile``.
4. Call the injected ``fetch_ohlc()`` to get OHLC closes, compute
   ``yang_zhang_rv_pct`` + ``fit_and_project_garch`` from ``core.vol``.
5. ``SET latest_vol_surface:<symbol>`` + ``PUBLISH vol_update`` via
   ``bus.publisher.publish_vol_update``.
6. Emit a heartbeat.

All IB I/O stays behind callables so the engine is testable without
``ib_insync``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from bus import keys, publisher
from core.vol.garch import fit_and_project_garch
from core.vol.pchip_smile import interpolate_delta_pillars
from core.vol.yang_zhang import yang_zhang_rv_pct

logger = logging.getLogger(__name__)

CYCLE_SECONDS = 180.0  # three-minute vol scan cadence
DEFAULT_TENOR_T = {
    "1M": 1 / 12, "2M": 2 / 12, "3M": 3 / 12,
    "4M": 4 / 12, "5M": 5 / 12, "6M": 6 / 12,
}

# Threshold on |sigma_mid - sigma_fair| for the CHEAP / EXPENSIVE verdict.
# Unit : vol points (percent). 1.0 = 100 basis points.
SIGNAL_ECART_THRESHOLD_PCT: float = 1.0

# Approximate DTE per tenor label — used to populate the NOT NULL dte
# column on the signals table when a more precise value is not carried by
# the surface dict.
DTE_FROM_LABEL = {
    "1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180,
}


def _derive_signals(surface: dict[str, Any], underlying: str) -> list[dict[str, Any]]:
    """Turn the engine surface into a list of signal dicts, one per tenor.

    ``surface[tenor]["atm"]["iv"]`` is the market mid IV (decimal).
    ``surface["_garch"][tenor]["sigma_model_pct"]`` is the GARCH fair IV
    in percent. When both are present we compute ``ecart = mid - fair``
    (vol points) and label CHEAP / EXPENSIVE / FAIR against
    ``SIGNAL_ECART_THRESHOLD_PCT``. Tenors missing either side are
    silently skipped.
    """
    garch = surface.get("_garch") or {}
    out: list[dict[str, Any]] = []
    for tenor, node in surface.items():
        if tenor.startswith("_") or not isinstance(node, dict):
            continue
        atm_node = node.get("atm")
        if not isinstance(atm_node, dict):
            continue
        iv_mid = atm_node.get("iv")
        garch_node = garch.get(tenor)
        sigma_fair_pct = (
            garch_node.get("sigma_model_pct") if isinstance(garch_node, dict) else None
        )
        if iv_mid is None or sigma_fair_pct is None:
            continue
        sigma_mid_pct = float(iv_mid) * 100.0
        ecart = sigma_mid_pct - float(sigma_fair_pct)
        if abs(ecart) <= SIGNAL_ECART_THRESHOLD_PCT:
            signal_type = "FAIR"
        elif ecart > 0:
            signal_type = "EXPENSIVE"
        else:
            signal_type = "CHEAP"
        out.append({
            "underlying": underlying,
            "tenor": tenor,
            "dte": DTE_FROM_LABEL.get(tenor, 0),
            "sigma_mid": round(sigma_mid_pct, 4),
            "sigma_fair": round(float(sigma_fair_pct), 4),
            "ecart": round(ecart, 4),
            "signal_type": signal_type,
            "rv": round(float(surface.get("_rv_full_pct")), 4) if surface.get("_rv_full_pct") else None,
        })
    return out


class _RedisLike(Protocol):
    async def get(self, name: str) -> Any: ...
    async def set(self, name: str, value: str, ex: int | None = ...) -> Any: ...
    async def publish(self, channel: str, message: str) -> int: ...


class _IBLike(Protocol):
    def isConnected(self) -> bool: ...
    async def connectAsync(self, host: str, port: int, clientId: int, timeout: float = ...) -> Any: ...
    def disconnect(self) -> None: ...


class VolEngine:
    """Long-running async task : compute the vol surface, publish to Redis."""

    def __init__(
        self,
        *,
        ib: _IBLike,
        redis: _RedisLike,
        symbol: str,
        ib_host: str,
        ib_port: int,
        client_id: int,
        fetch_fop_chain: Any,
        fetch_ohlc: Any,
        tenor_t: dict[str, float] | None = None,
    ) -> None:
        self.ib = ib
        self.redis = redis
        self.symbol = symbol
        self.ib_host = ib_host
        self.ib_port = ib_port
        self.client_id = client_id
        # fetch_fop_chain : (F) -> {tenor -> [(delta, iv, strike)]}
        # fetch_ohlc     : () -> pd.DataFrame | None / np.ndarray | None
        self._fetch_fop_chain = fetch_fop_chain
        self._fetch_ohlc = fetch_ohlc
        self.tenor_t = tenor_t or DEFAULT_TENOR_T
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from shared.ib_connection import connect_ib_with_backoff

        await connect_ib_with_backoff(
            self.ib, host=self.ib_host, port=self.ib_port, client_id=self.client_id
        )
        logger.info("vol_engine_started", extra={"symbol": self.symbol})

        try:
            while not self._stop.is_set():
                await self.run_cycle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=CYCLE_SECONDS)
                    break
                except TimeoutError:
                    continue
        finally:
            self._teardown()

    async def run_cycle(self) -> bool:
        """Single pass. Returns True if a vol_update was published."""
        F = await self._read_spot()
        if F is None:
            logger.info("vol_cycle_skipped", extra={"reason": "no_spot"})
            return False

        surface = await self._compute_surface(F)
        if not surface:
            logger.info("vol_cycle_skipped", extra={"reason": "no_surface"})
            return False

        signals = _derive_signals(surface, self.symbol)
        try:
            await publisher.publish_vol_update(
                self.redis, symbol=self.symbol, surface_data=surface, signals_data=signals
            )
            await publisher.set_heartbeat(self.redis, keys.ENGINE_VOL)
        except Exception:
            logger.exception("publish_vol_update_failed")
            return False

        # Also fan the surface + signals to the db-writer via db_events so
        # rows land in Postgres — required by /api/v1/vol/smile (vol_surfaces)
        # and /api/v1/analytics/signals (signals).
        try:
            from datetime import UTC, datetime

            from shared.db_queue import publish_db_event

            ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            await publish_db_event(
                self.redis,
                table="vol_surfaces",
                payload={
                    "timestamp": ts_iso,
                    "underlying": self.symbol,
                    "spot": float(F),
                    "forward": float(F),
                    "surface_data": surface,
                },
            )
            for sig in signals:
                await publish_db_event(
                    self.redis, table="signals", payload={**sig, "timestamp": ts_iso},
                )
        except Exception:
            logger.exception("publish_db_event_failed")
        return True

    async def _read_spot(self) -> float | None:
        key = keys.LATEST_SPOT.format(symbol=self.symbol)
        try:
            raw = await self.redis.get(key)
        except Exception:
            logger.exception("redis_get_failed", extra={"key": key})
            return None
        if raw is None:
            return None
        # The publisher writes `str(mid)` (bus/publisher.py::publish_tick) so the
        # payload is a plain float string. Fall back to the legacy dict shape
        # `{"mid": x, "bid": y}` so callers that store JSON still work.
        try:
            text = raw.decode() if isinstance(raw, bytes) else raw
            return float(text)
        except (ValueError, TypeError):
            pass
        try:
            payload = json.loads(raw)
            return float(payload.get("mid") or payload.get("bid"))
        except (ValueError, TypeError, AttributeError):
            return None

    async def _compute_surface(self, F: float) -> dict[str, Any]:
        import inspect

        raw = self._fetch_fop_chain(F)
        if inspect.isawaitable(raw):
            raw = await raw
        pillars_by_tenor = raw or {}
        out: dict[str, Any] = {}
        for tenor, obs in pillars_by_tenor.items():
            pillars = interpolate_delta_pillars(obs)
            out[tenor] = {
                label: {"iv": p.iv, "strike": p.strike} for label, p in pillars.items()
            }

        import inspect

        ohlc = self._fetch_ohlc()
        if inspect.isawaitable(ohlc):
            ohlc = await ohlc
        if ohlc is not None:
            try:
                rv_full = yang_zhang_rv_pct(ohlc, window=len(ohlc) - 1)
            except Exception:
                rv_full = None
            if rv_full is not None:
                out["_rv_full_pct"] = rv_full
                try:
                    closes = ohlc["close"].to_numpy() if hasattr(ohlc, "close") else None
                    if closes is not None and len(closes) >= 5:
                        out["_garch"] = fit_and_project_garch(
                            closes, tenor_t=self.tenor_t, rv_full=rv_full
                        )
                except Exception:
                    logger.exception("garch_projection_failed")
        return out

    def _teardown(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.exception("ib_disconnect_failed")
        logger.info("vol_engine_stopped", extra={"symbol": self.symbol})
