"""Async RiskEngine — standalone service.

One cycle every two seconds (configurable via ``LIVE_LOOP_INTERVAL_S``) :

1. ``GET latest_spot:<symbol>`` on Redis. Skip if missing (market-data down).
2. ``GET latest_vol_surface:<symbol>``. Skip if missing (vol-engine cold).
3. Read OPEN positions from Postgres (replaces the stub ``fetch_positions``).
4. Aggregate Greeks (delta, gamma, vega, theta) at the current spot using
   scalar BS from ``core.pricing.bs``.
5. Persist a per-position row in ``position_snapshots`` (greeks columns) —
   single ownership of greeks compute, cf. ``container_risk.md`` and the
   PORTFOLIO_PANEL_LIVE.md L1 spec.
6. Build an optional PnL curve over a spot range using the vectorised
   ``bs_price_vec`` from ``core.risk.greeks`` — skipped when the book is
   empty to keep the cycle snappy.
7. ``publisher.publish_risk_update(...)`` + ``set_heartbeat("risk_engine")``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bus import keys, publisher
from bus.spot_cache import parse_spot
from core.contracts import INSTRUMENT_FUTURE, multiplier_for, parse_local_symbol
from core.pricing.bs import (
    bs_implied_vol,
    bs_price,
)
from core.risk.greek_scaling import future_greeks, option_greeks, sum_greeks
from core.risk.greeks import bs_price_vec
from core.risk.history_policy import GreeksPoint, snapshot_due
from core.vol.iv_lookup import iv_at_strike
from persistence.models import OpenPosition, OpenPositionHistory
from persistence.retention import next_retention_run


def _days_to_tenor_bucket(days: int) -> str:
    """Pick the closest pillar tenor for surface IV lookup."""
    if days <= 30:
        return "1M"
    if days <= 60:
        return "2M"
    if days <= 90:
        return "3M"
    if days <= 120:
        return "4M"
    if days <= 150:
        return "5M"
    return "6M"

logger = logging.getLogger(__name__)

CYCLE_SECONDS = 2.0
PNL_CHART_POINTS = 120
PNL_CHART_RANGE_PCT = 0.02  # ±2% around spot
FALLBACK_IV = 0.08          # fallback when the surface has no matching tenor
# open_position_history is read at minute-to-daily resolution (marginal-var,
# pnl-attribution, the daily rollups), so the persist pass runs every 60 s
# (#373: was 30 s) and writes a row only for a position whose greeks or P&L
# moved beyond core.risk.history_policy's thresholds, or whose last row is
# older than its heartbeat floor. The live row on ``open_position`` is still
# updated on every pass and the live greeks still publish to Redis every
# CYCLE_SECONDS. Retention prunes past the window once a night (02:30 UTC).
HISTORY_PERSIST_CYCLES = 30   # 30 × CYCLE_SECONDS = 60 s
HISTORY_RETENTION_DAYS = 90


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
        fetch_positions: Any | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.ib = ib
        self.redis = redis
        self.symbol = symbol
        self.ib_host = ib_host
        self.ib_port = ib_port
        self.client_id = client_id
        # ``fetch_positions`` (legacy callable injection — still honoured for
        # unit tests). When None and ``sessionmaker`` is provided, positions
        # are loaded from the DB at each cycle.
        self._fetch_positions = fetch_positions
        self._sessionmaker = sessionmaker
        self._stop = asyncio.Event()
        # history-write throttle + retention bookkeeping (see module constants)
        self._cycle = 0
        self._iv_fallback_legs = 0  # legs priced on FALLBACK_IV in the current pass (#310)
        self._next_prune_at = next_retention_run()        # nightly, off-peak (#373)
        # Last history row written per position: (when, the point), the input
        # of the write policy. Empty after a restart, so the first pass writes
        # every position once.
        self._last_written: dict[int, tuple[datetime, GreeksPoint]] = {}
        self._history_skipped = 0

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from shared.ib_connection import connect_ib_with_backoff
        from shared.observability import observed_cycle

        await connect_ib_with_backoff(
            self.ib, host=self.ib_host, port=self.ib_port, client_id=self.client_id
        )
        logger.info("risk_engine_started", extra={"symbol": self.symbol})
        try:
            while not self._stop.is_set():
                # Reconnect check (nightly IB Gateway restart). No hook needed :
                # positions come from Redis/DB, IB is only the session heartbeat
                # + the portfolio spot fallback — both self-heal on reconnect.
                if not self.ib.isConnected():
                    logger.warning("risk_engine_ib_disconnected_reconnecting")
                    await connect_ib_with_backoff(
                        self.ib, host=self.ib_host, port=self.ib_port,
                        client_id=self.client_id,
                    )
                await publisher.set_heartbeat(self.redis, keys.ENGINE_RISK)
                # Retention: once a night at 02:30 UTC, in batches (#373).
                if self._sessionmaker is not None and datetime.now(UTC) >= self._next_prune_at:
                    self._next_prune_at = next_retention_run()
                    try:
                        await self._prune_history()
                    except Exception:
                        logger.exception("prune_history_failed")
                # P0 obs : cycle_id propagated to structlog + metrics emitted.
                with observed_cycle("risk_engine"):
                    await self.run_cycle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=CYCLE_SECONDS)
                    break
                except TimeoutError:
                    continue
        finally:
            self._teardown()

    async def run_cycle(self) -> bool:
        """P2 obs : child spans per stage. service.name=risk_engine."""
        from opentelemetry import trace as _otel
        tracer = _otel.get_tracer(__name__)
        self._cycle += 1

        with tracer.start_as_current_span("risk_read_spot") as span:
            F = await self._read_spot()
            span.set_attribute("spot", F if F is not None else -1)
        if F is None:
            # Redis ticker may be empty when IB market-data subscription is
            # broken (Error 1100 / weekend). Fall back to the EUR FUTURE
            # ``marketPrice`` from this engine's own ``ib.portfolio()`` —
            # same trick as execution-engine.position_sync.
            F = self._spot_from_portfolio()
            if F is None:
                logger.debug("risk_cycle_skipped", extra={"reason": "no_spot"})
                return False
            logger.info("risk_spot_fallback_from_portfolio", extra={"spot": F})

        with tracer.start_as_current_span("risk_read_surface") as span:
            surface = await self._read_surface()
            if surface is None:
                surface = {}
                logger.debug("risk_cycle_no_surface_using_fallback")
            span.set_attribute("n_pillars", len(surface))

        with tracer.start_as_current_span("risk_compute_greeks") as span:
            positions = await self._load_positions()
            self._iv_fallback_legs = 0
            greeks = self._aggregate_greeks(positions, F, surface)
            # Flag, never hide: legs priced on FALLBACK_IV because the surface
            # had no usable pillar (#310, audit 03#5). Consumers can refuse or
            # badge the numbers; the log makes a schema drift loud.
            greeks["iv_fallback_legs"] = self._iv_fallback_legs
            if self._iv_fallback_legs:
                logger.warning(
                    "risk_iv_fallback legs=%s n_positions=%s n_pillars=%s",
                    self._iv_fallback_legs, len(positions), len(surface),
                )
            self._iv_fallback_legs = 0
            pnl_curve = self._compute_pnl_curve(positions, F, surface) if positions else None
            span.set_attribute("n_positions", len(positions))
            span.set_attribute("iv_fallback_legs", greeks["iv_fallback_legs"])

        # Persist per-position greeks to DB — THROTTLED: history is read only at
        # daily resolution, so snapshot every HISTORY_PERSIST_CYCLES (~30s), not
        # every 2s. The live greeks still publish to Redis below each cycle.
        if (
            positions
            and self._sessionmaker is not None
            and (self._cycle - 1) % HISTORY_PERSIST_CYCLES == 0  # 1st cycle, then every ~30s
        ):
            with tracer.start_as_current_span("risk_persist_snapshots") as span:
                try:
                    n = await self._persist_position_snapshots(positions, F, surface)
                    span.set_attribute("n_rows", n or 0)
                except Exception:
                    logger.exception("persist_position_snapshots_failed")

        with tracer.start_as_current_span("risk_redis_publish"):
            try:
                await publisher.publish_risk_update(
                    self.redis, greeks=greeks, pnl_curve=pnl_curve
                )
                await publisher.set_heartbeat(self.redis, keys.ENGINE_RISK)
                return True
            except Exception:
                logger.exception("publish_risk_update_failed")
                return False

    async def _prune_history(self) -> None:
        """Retention — delete history rows older than HISTORY_RETENTION_DAYS so the
        append-only tables don't grow unbounded. Nothing reads beyond this window
        (the daily series lives in ``portfolio_daily`` since #368). One
        transaction per table, failures counted (#330): one table's problem
        never stops the other's prune."""
        if self._sessionmaker is None:
            return
        from persistence.retention import RISK_ENGINE_TABLES, run_retention

        cutoff = datetime.now(UTC) - timedelta(days=HISTORY_RETENTION_DAYS)
        summary = await run_retention(
            self._sessionmaker, engine="risk_engine", cutoff=cutoff, tables=RISK_ENGINE_TABLES,
        )  # 50 k-row batches, each its own short transaction (#373)
        logger.info("history_prune_done", extra={"retention_days": HISTORY_RETENTION_DAYS, **summary})

    async def _load_positions(self) -> list[dict]:
        """Read OPEN positions from DB and shape them for the BS compute path.

        Falls back to the injected ``fetch_positions`` callable for unit
        tests (no DB session). Returns ``[]`` when neither path is wired.
        """
        if self._sessionmaker is not None:
            return await self._load_positions_from_db()
        if self._fetch_positions is not None:
            return self._fetch_positions() or []
        return []

    async def _load_positions_from_db(self) -> list[dict]:
        today = datetime.now(UTC).date()
        async with self._sessionmaker() as db:  # type: ignore[misc]
            rows = (await db.execute(
                select(OpenPosition)
            )).scalars().all()
        out: list[dict] = []
        for p in rows:
            spec = parse_local_symbol(p.structure)
            if spec is None:
                continue  # unparseable localSymbol — skip rather than mislead the BS path
            qty = float(p.quantity) if p.quantity is not None else 0.0
            signed_qty = qty if p.side == "BUY" else -qty
            right: str | None
            if spec.option_type == "CALL":
                right = "C"
            elif spec.option_type == "PUT":
                right = "P"
            else:
                right = None
            dte_days = (p.expiry - today).days if p.expiry else 0
            T = max(dte_days, 0) / 365.0
            cost_per_unit = (
                float(p.contract_price_entry)
                if p.contract_price_entry is not None else 0.0
            )
            out.append({
                "id": p.id,
                "symbol": spec.symbol,
                "instrument_type": spec.instrument_type,
                "quantity": signed_qty,
                "strike": spec.strike,
                "option_type": right,
                "T": T,
                "tenor": _days_to_tenor_bucket(dte_days),
                "cost_per_unit": cost_per_unit,
                "multiplier": spec.multiplier,
            })
        return out

    async def _persist_position_snapshots(
        self, positions: list[dict], F: float, surface: dict
    ) -> int:
        """One ``position_snapshots`` row per OPEN position, greeks computed
        at current spot/IV. ``pnl_usd`` left None here — execution-engine
        owns the canonical ``unrealizedPNL`` from IB ``updatePortfolio``.

        IV resolution order :
          1. Surface ATM at the position's tenor pillar (preferred).
          2. ``bs_implied_vol`` inversion of the option's mark price, read
             from the Redis hash ``option_marks:<symbol>`` populated by
             execution-engine. Saves us when the surface is empty (weekend
             / vol-engine gated).
        """
        if not positions or self._sessionmaker is None:
            return 0
        # One Redis HGETALL up-front avoids N round-trips inside the loop.
        option_marks = await self._read_option_marks()
        contract_marks = await self._read_contract_marks()
        unrealized_pnl = await self._read_unrealized_pnl()
        now = datetime.now(UTC)
        inserted = 0
        async with self._sessionmaker() as db:
            for pos in positions:
                qty = float(pos.get("quantity") or 0.0)
                instr = pos.get("instrument_type")
                K = float(pos.get("strike") or 0.0)
                T = float(pos.get("T") or 0.0)
                right = pos.get("option_type")
                tenor = pos.get("tenor", "1M")
                cost = float(pos.get("cost_per_unit") or 0.0)

                iv: float | None = None
                delta = gamma = vega = theta = pnl = None
                mult = float(pos.get("multiplier") or multiplier_for(pos.get("symbol")))

                vanna = volga = None
                if instr == INSTRUMENT_FUTURE:
                    g = future_greeks(qty, mult, F)
                    delta, gamma, vega, theta = g.delta_usd, 0.0, 0.0, 0.0
                    vanna = volga = 0.0
                    pnl = (F - cost) * qty * mult if cost else None
                elif right in ("C", "P") and K > 0 and T > 0:
                    iv = self._iv_for(surface, tenor, K, F=F, T=T)
                    if iv is None:
                        # Surface missing / pillar absent → invert BS on the
                        # option's market mark for an exact implied vol.
                        mark = option_marks.get(int(pos["id"]))
                        if mark is not None:
                            implied = bs_implied_vol(
                                price=mark, F=F, K=K, T=T, right=right,
                            )
                            if implied is not None:
                                iv = implied
                    if iv is None:
                        # Last resort, and counted: a constant vol is a guess.
                        self._iv_fallback_legs += 1
                        iv = FALLBACK_IV
                    # One scaling path with the published aggregate (#313):
                    # delta_usd is USD exposure (EUR exposure × spot), gamma
                    # $/pip, vega $/vol pt, theta $/day, vanna $/vol pt,
                    # volga $/vol pt². docs/vol-modeling/units.md.
                    g = option_greeks(qty=qty, mult=mult, F=F, K=K, T=T, iv=iv, right=right)
                    delta, gamma, vega, theta = (
                        g.delta_usd, g.gamma_usd_per_pip, g.vega_usd_per_volpt, g.theta_usd_per_day,
                    )
                    vanna, volga = g.vanna_usd_per_volpt, g.volga_usd_per_volpt2
                    mark_bs = bs_price(F, K, T, iv, right)
                    pnl = (mark_bs - cost) * qty * mult if cost else None

                # IB-canonical PnL beats our BS recompute when both available.
                ib_pnl = unrealized_pnl.get(int(pos["id"]))
                if ib_pnl is not None:
                    pnl = ib_pnl
                # Market price = the contract's own mark (futures price for
                # FUT, option premium for OPT). Stays None until position_sync
                # has published the IB ``marketPrice`` to ``contract_marks:EUR``
                # — falling back to spot would mislead panel E (option premium
                # ≠ spot, near-dated FUT ≈ spot but not equal).
                mark = contract_marks.get(int(pos["id"]))

                # Encoded once for both UPDATE (live row) and INSERT (snapshot copy).
                delta_dec = Decimal(str(round(delta, 2))) if delta is not None else None
                gamma_dec = Decimal(str(round(gamma, 2))) if gamma is not None else None
                vega_dec = Decimal(str(round(vega, 2))) if vega is not None else None
                theta_dec = Decimal(str(round(theta, 2))) if theta is not None else None
                vanna_dec = Decimal(str(round(vanna, 2))) if vanna is not None else None
                volga_dec = Decimal(str(round(volga, 2))) if volga is not None else None
                pnl_dec = Decimal(str(round(pnl, 2))) if pnl is not None else None
                mark_dec = Decimal(str(round(mark, 8))) if mark is not None else None
                iv_dec = Decimal(str(round(iv, 5))) if iv is not None else None

                # 1. UPDATE the live row on ``positions`` so the API can read
                #    everything from a single row (mirror of panel E).
                live_pos = await db.get(OpenPosition, int(pos["id"]))
                if live_pos is None:
                    continue
                live_pos.market_price = mark_dec
                live_pos.current_pnl_usd = pnl_dec
                live_pos.delta_usd = delta_dec
                live_pos.gamma_usd = gamma_dec
                live_pos.vega_usd = vega_dec
                live_pos.theta_usd = theta_dec
                live_pos.iv = iv_dec
                live_pos.vanna_usd = vanna_dec
                live_pos.volga_usd = volga_dec

                # 2. Snapshot = literal copy of every panel-E column at this
                #    timestamp, but only when it says something new (#373):
                #    the policy compares with the last row written for this
                #    position and forces one every HEARTBEAT_S regardless.
                point = GreeksPoint(delta=delta, gamma=gamma, vega=vega, theta=theta, pnl=pnl, iv=iv, mark=mark)
                prev = self._last_written.get(int(pos["id"]))
                age_s = (now - prev[0]).total_seconds() if prev is not None else None
                if not snapshot_due(prev[1] if prev is not None else None, point, last_written_age_s=age_s):
                    self._history_skipped += 1
                    continue
                snap = OpenPositionHistory(
                    position_id=live_pos.id,
                    timestamp=now,
                    structure=live_pos.structure,
                    product_label=live_pos.product_label,
                    contract_id=live_pos.contract_id,
                    trade_id=live_pos.trade_id,
                    package_id=live_pos.package_id,
                    side=live_pos.side,
                    tenor=live_pos.tenor,
                    expiry=live_pos.expiry,
                    quantity=live_pos.quantity,
                    nominal_eur=live_pos.nominal_eur,
                    contract_price_entry=live_pos.contract_price_entry,
                    market_price=mark_dec,
                    current_pnl_usd=pnl_dec,
                    delta_usd=delta_dec,
                    gamma_usd=gamma_dec,
                    vega_usd=vega_dec,
                    theta_usd=theta_dec,
                    iv=iv_dec,
                    vanna_usd=vanna_dec,
                    volga_usd=volga_dec,
                )
                db.add(snap)
                self._last_written[int(pos["id"])] = (now, point)
                inserted += 1
            await db.commit()
        if inserted or self._history_skipped:
            logger.debug("history_rows_written", extra={"written": inserted, "skipped_quiet": self._history_skipped})
            self._history_skipped = 0
        return inserted

    def _spot_from_portfolio(self) -> float | None:
        """Pick an EUR FUTURE ``marketPrice`` from ``ib.portfolio()`` as a
        spot proxy when Redis is empty. Returns None if no EUR future is
        held or the IB session is silent."""
        try:
            for p in (self.ib.portfolio() if hasattr(self.ib, "portfolio") else []):
                c = getattr(p, "contract", None)
                if c is None:
                    continue
                if getattr(c, "symbol", None) != "EUR":
                    continue
                if getattr(c, "secType", None) not in ("FUT", "CONTFUT"):
                    continue
                mp = getattr(p, "marketPrice", None)
                if mp:
                    return float(mp)
        except Exception:
            logger.exception("spot_from_portfolio_failed")
        return None

    async def _read_option_marks(self) -> dict[int, float]:
        return await self._read_redis_hash_floats(keys.OPTION_MARKS.format(symbol="EUR"))

    async def _read_contract_marks(self) -> dict[int, float]:
        return await self._read_redis_hash_floats(keys.CONTRACT_MARKS.format(symbol="EUR"))

    async def _read_unrealized_pnl(self) -> dict[int, float]:
        return await self._read_redis_hash_floats(keys.UNREALIZED_PNL.format(symbol="EUR"))

    async def _read_redis_hash_floats(self, key: str) -> dict[int, float]:
        """Read a Redis hash of ``{position_id: float_string}`` populated by
        execution-engine. Returns ``{}`` if absent / Redis errors."""
        try:
            raw = await self.redis.hgetall(key)
        except Exception:
            return {}
        if not raw:
            return {}
        out: dict[int, float] = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else k
            vv = v.decode() if isinstance(v, bytes) else v
            try:
                out[int(kk)] = float(vv)
            except (ValueError, TypeError):
                continue
        return out

    async def _read_spot(self) -> float | None:
        """Read the spot from Redis. Accepts both formats produced by
        the upstream engines:

        - **plain float string** (e.g. ``"1.17052"``) — this is what
          ``bus.publisher.publish_tick`` writes via ``str(mid)`` (cf.
          ``src/bus/publisher.py:83-85``). Current market-data format.
        - **JSON dict** (e.g. ``{"mid": 1.17, "bid": 1.169, ...}``) —
          alternative format possibly used elsewhere.

        Tolerating both forms avoids a bus contract mismatch between
        market-data (writer) and risk-engine (reader). Without this
        tolerance, a mismatch causes ``risk_cycle_skipped`` in a loop
        while the value actually sits in Redis (incident reproduced in
        sandbox).
        """
        key = keys.LATEST_SPOT.format(symbol=self.symbol)
        raw = await self.redis.get(key)
        # One parser for every shape the key ever had (#327): bus.spot_cache.
        return parse_spot(raw)[0]

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

    def _iv_for(
        self, surface: dict, tenor: str, strike: float,
        F: float | None = None, T: float | None = None,
    ) -> float | None:
        """IV at (tenor, strike) from the published surface via the one core
        accessor (linear in strike, wings clamped; a pillar without a strike
        is placed from its delta when F and T are given). None = unavailable:
        the caller decides what to do and COUNTS it (#310). The 8 % constant
        is no longer handed out from here."""
        res = iv_at_strike(surface, tenor, strike, F=F, T=T)
        return float(res.iv) if res is not None else None

    def _iv_or_fallback(
        self, surface: dict, tenor: str, strike: float, F: float, T: float,
    ) -> float:
        """``_iv_for`` with the FALLBACK_IV constant when the surface has
        nothing — counted in ``self._iv_fallback_legs`` so the cycle can
        publish how many legs were priced on a made-up vol."""
        iv = self._iv_for(surface, tenor, strike, F=F, T=T)
        if iv is None:
            self._iv_fallback_legs += 1
            return FALLBACK_IV
        return iv

    def _aggregate_greeks(
        self, positions: list[dict], F: float, surface: dict
    ) -> dict[str, float]:
        """Book-level greeks for ``risk_update`` / ``latest_greeks:portfolio``:
        the SUM of the per-position greeks the snapshot path persists, same
        helper, same units (#313, R3-07): delta in USD (``delta_eur`` alongside),
        gamma $/pip, vega $/vol pt, theta $/day, vanna $/vol pt, volga $/vol pt².
        ``n_positions`` / ``n_priced`` say how much of the book the numbers cover."""
        items = []
        n_positions = 0
        for pos in positions:
            n_positions += 1
            right = pos.get("option_type") or pos.get("right")
            instr = pos.get("instrument_type")
            qty = float(pos.get("quantity", 0))
            # NB: positions carry the canonical "FUTURE" (core.contracts),
            # not the IB secType "FUT" — comparing against "FUT" silently
            # zeroed every futures delta.
            mult = float(pos.get("multiplier") or multiplier_for(pos.get("symbol")))
            if instr == INSTRUMENT_FUTURE:
                items.append(future_greeks(qty, mult, F))
                continue
            if right not in ("C", "P"):
                continue
            K = float(pos.get("strike") or 0)
            T = float(pos.get("T") or 0)
            if K <= 0 or T <= 0:
                continue
            iv = self._iv_or_fallback(surface, pos.get("tenor", "1M"), K, F, T)
            items.append(option_greeks(qty=qty, mult=mult, F=F, K=K, T=T, iv=iv, right=right))
        s = sum_greeks(items)
        return {
            "delta": s.delta_usd,
            "delta_eur": s.delta_eur,
            "gamma": s.gamma_usd_per_pip,
            "vega": s.vega_usd_per_volpt,
            "theta": s.theta_usd_per_day,
            "vanna": s.vanna_usd_per_volpt,
            "volga": s.volga_usd_per_volpt2,
            "spot": F,
            "n_positions": n_positions,
            "n_priced": len(items),
            "units": "delta USD, delta_eur EUR, gamma $/pip, vega $/volpt, theta $/day, vanna $/volpt, volga $/volpt2",
        }

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
                if pos.get("instrument_type") == INSTRUMENT_FUTURE:
                    pnls += (spots - cost) * qty
                continue
            K = float(pos.get("strike") or 0)
            T = float(pos.get("T") or 0)
            if K <= 0 or T <= 0:
                continue
            iv = self._iv_or_fallback(surface, pos.get("tenor", "1M"), K, F, T)
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
