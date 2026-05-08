"""Async RiskEngine — standalone service (R7 PR #5).

One cycle every two seconds :

1. ``GET latest_spot:<symbol>`` on Redis. Skip if missing (market-data down).
2. ``GET latest_vol_surface:<symbol>``. Skip if missing (vol-engine cold).
3. Evaluate the current position book via the injected ``fetch_positions``
   callable → list of dicts ({qty, strike, option_type, T, tenor, ...}).
4. Aggregate Greeks (delta, gamma, vega, theta) at the current spot using
   scalar BS from ``core.pricing.bs``.
5. Build an optional PnL curve over a spot range using the vectorised
   ``bs_price_vec`` from ``core.risk.greeks`` — skipped when the book is
   empty to keep the cycle snappy.
6. ``publisher.publish_risk_update(...)`` + ``set_heartbeat("risk_engine")``.

All IB I/O stays behind callables so the engine has no ``ib_insync``
import and can be unit-tested with pure dicts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

import numpy as np

from bus import keys, publisher
from core.pricing.bs import bs_delta, bs_gamma, bs_theta, bs_vega
from core.risk.greeks import bs_price_vec

logger = logging.getLogger(__name__)

CYCLE_SECONDS = 2.0
PNL_CHART_POINTS = 120
PNL_CHART_RANGE_PCT = 0.02  # ±2% around spot
FALLBACK_IV = 0.08          # fallback when the surface has no matching tenor


class _RedisLike(Protocol):
    async def get(self, name: str) -> Any: ...
    async def set(self, name: str, value: str, ex: int | None = ...) -> Any: ...
    async def publish(self, channel: str, message: str) -> int: ...


class _IBLike(Protocol):
    def isConnected(self) -> bool: ...
    async def connectAsync(self, host: str, port: int, clientId: int, timeout: float = ...) -> Any: ...
    def disconnect(self) -> None: ...


class RiskEngine:
    def __init__(
        self,
        *,
        ib: _IBLike,
        redis: _RedisLike,
        symbol: str,
        ib_host: str,
        ib_port: int,
        client_id: int,
        fetch_positions: Any,
    ) -> None:
        self.ib = ib
        self.redis = redis
        self.symbol = symbol
        self.ib_host = ib_host
        self.ib_port = ib_port
        self.client_id = client_id
        # fetch_positions : () -> list[dict]
        self._fetch_positions = fetch_positions
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from shared.ib_connection import connect_ib_with_backoff

        await connect_ib_with_backoff(
            self.ib, host=self.ib_host, port=self.ib_port, client_id=self.client_id
        )
        logger.info("risk_engine_started", extra={"symbol": self.symbol})
        try:
            while not self._stop.is_set():
                await publisher.set_heartbeat(self.redis, keys.ENGINE_RISK)
                await self.run_cycle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=CYCLE_SECONDS)
                    break
                except TimeoutError:
                    continue
        finally:
            self._teardown()

    async def run_cycle(self) -> bool:
        F = await self._read_spot()
        if F is None:
            logger.debug("risk_cycle_skipped", extra={"reason": "no_spot"})
            return False

        surface = await self._read_surface()
        if surface is None:
            logger.debug("risk_cycle_skipped", extra={"reason": "no_surface"})
            return False

        positions = self._fetch_positions() or []
        greeks = self._aggregate_greeks(positions, F, surface)
        pnl_curve = self._compute_pnl_curve(positions, F, surface) if positions else None

        try:
            await publisher.publish_risk_update(
                self.redis, greeks=greeks, pnl_curve=pnl_curve
            )
            await publisher.set_heartbeat(self.redis, keys.ENGINE_RISK)
            return True
        except Exception:
            logger.exception("publish_risk_update_failed")
            return False

    async def _read_spot(self) -> float | None:
        """Lit le spot depuis Redis. Accepte les deux formats produits par
        les engines en upstream :

        - **plain float string** (ex: ``"1.17052"``) — c'est ce que
          ``bus.publisher.publish_tick`` écrit via ``str(mid)`` (cf.
          ``src/bus/publisher.py:83-85``). Format actuel de market-data.
        - **dict JSON** (ex: ``{"mid": 1.17, "bid": 1.169, ...}``) —
          format alternatif possiblement utilisé ailleurs.

        Le tolerant des deux formes évite un mismatch de contrat bus
        entre market-data (writer) et risk-engine (reader). Bug R9
        sandbox 28/04/2026 : le mismatch entraînait ``risk_cycle_skipped``
        en boucle alors que la valeur était bien dans Redis.
        """
        key = keys.LATEST_SPOT.format(symbol=self.symbol)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            # Plain numeric (str(mid) côté market-data) → cast direct.
            if isinstance(payload, (int, float)):
                return float(payload)
            # Dict avec "mid" / "bid" → fallback historique.
            if isinstance(payload, dict):
                return float(payload.get("mid") or payload.get("bid"))
            return None
        except (ValueError, TypeError, AttributeError):
            return None

    async def _read_surface(self) -> dict | None:
        key = keys.LATEST_VOL_SURFACE.format(symbol=self.symbol)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            return payload.get("surface") if isinstance(payload, dict) else None
        except (ValueError, TypeError):
            return None

    def _iv_for(self, surface: dict, tenor: str, strike: float) -> float:
        """Pick the closest pillar's IV for a given tenor/strike — matches the
        behaviour of the legacy ``interpolate_iv`` but against the new
        surface shape ``{tenor: {label: {iv, strike}}}``."""
        pillars = surface.get(tenor) if isinstance(surface, dict) else None
        if not isinstance(pillars, dict):
            return FALLBACK_IV
        candidates = [
            (p["strike"], p["iv"]) for p in pillars.values()
            if isinstance(p, dict) and p.get("iv") and p.get("strike")
        ]
        if not candidates:
            return FALLBACK_IV
        _, iv = min(candidates, key=lambda k_iv: abs(k_iv[0] - strike))
        return float(iv)

    def _aggregate_greeks(
        self, positions: list[dict], F: float, surface: dict
    ) -> dict[str, float]:
        totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        for pos in positions:
            right = pos.get("option_type") or pos.get("right")
            if right not in ("C", "P"):
                # FUTs contribute 1 per unit of delta, others skipped here.
                if pos.get("instrument_type") == "FUT":
                    totals["delta"] += float(pos.get("quantity", 0))
                continue
            qty = float(pos.get("quantity", 0))
            K = float(pos.get("strike") or 0)
            T = float(pos.get("T") or 0)
            if K <= 0 or T <= 0:
                continue
            tenor = pos.get("tenor", "1M")
            iv = self._iv_for(surface, tenor, K)
            totals["delta"] += qty * bs_delta(F, K, T, iv, right)
            totals["gamma"] += qty * bs_gamma(F, K, T, iv)
            totals["vega"] += qty * bs_vega(F, K, T, iv)
            totals["theta"] += qty * bs_theta(F, K, T, iv, right)
        totals["spot"] = F
        return totals

    def _compute_pnl_curve(
        self, positions: list[dict], F: float, surface: dict
    ) -> dict[str, Any]:
        lo = F * (1 - PNL_CHART_RANGE_PCT)
        hi = F * (1 + PNL_CHART_RANGE_PCT)
        spots = np.linspace(lo, hi, PNL_CHART_POINTS)
        pnls = np.zeros(PNL_CHART_POINTS)
        for pos in positions:
            right = pos.get("option_type") or pos.get("right")
            qty = float(pos.get("quantity", 0))
            cost = float(pos.get("cost_per_unit") or 0)
            if right not in ("C", "P"):
                if pos.get("instrument_type") == "FUT":
                    pnls += (spots - cost) * qty
                continue
            K = float(pos.get("strike") or 0)
            T = float(pos.get("T") or 0)
            if K <= 0 or T <= 0:
                continue
            iv = self._iv_for(surface, pos.get("tenor", "1M"), K)
            prices = bs_price_vec(spots, K, T, iv, right)
            pnls += (prices - cost) * qty
        return {"spots": spots.tolist(), "pnls": pnls.tolist(), "spot": F}

    def _teardown(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.exception("ib_disconnect_failed")
        logger.info("risk_engine_stopped", extra={"symbol": self.symbol})
