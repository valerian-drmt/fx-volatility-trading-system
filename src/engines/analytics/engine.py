"""Async AnalyticsEngine — standalone 6th engine (no IB).

Extracts the vol-engine's heavy hourly batch analytics into a dedicated
service that can be cpu-capped on its own. Each cycle (default hourly) runs 3
best-effort steps — reading Redis / Postgres and publishing results back so
the vol-engine can serve them at the edge:

1. ``_collect_snapshot``   — build the 30-dim PCA surface snapshot from the
   latest vol surface in Redis, gated on the last ``SurfaceSnapshotHourly``
   timestamp, and fan it to the db-writer via ``db_events`` →
   ``pca_surface_snapshot_history``.
2. ``_refit_gmm``          — fit the 3-component GMM regime model on 90d of
   feature history, serialize it to Redis for the vol-engine to
   ``infer_proba`` on the live obs (train centrally, infer at the edge).
3. ``_recompute_pc3_history`` — recompute the PC3 skew/convex rolling arrays
   from the latest ≤200 surface snapshots and publish them to Redis.

A second, faster loop (``ROLLUP_INTERVAL_S``, default 5 min) materialises the
portfolio rollups the api used to compute per request (#368, hot/cold T4):
``portfolio_daily`` (one row per UTC day of account history) and
``pnl_attribution_latest`` (one row per lookback window). See
``persistence.rollups``.

Heartbeat is **decoupled** from the compute (db-writer split-loop idea): a
background task pings ``heartbeat:analytics_engine`` every ~60s so the
container reads healthy between the hourly compute cycles. Each step is
wrapped in try/except so one failure never blocks the others.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import numpy as np
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bus import keys, publisher
from bus.spot_cache import parse_spot, spot_fallbacks
from core.config import CapacityConfig, VolTradingConfig
from core.vol.gmm_regime import MIN_OBS_GMM, fit_gmm, serialize_gmm
from core.vol.pca_engine import (
    DELTAS,
    TENORS,
    feature_vector_from_surface,
    pc3_sub_metrics,
)
from persistence.db import get_sessionmaker
from persistence.models import FeatureHistory, SurfaceSnapshotHourly
from persistence.rollups import refresh_attribution, refresh_portfolio_daily
from shared.db_events import publish_db_event
from shared.tasks import spawn_supervised

logger = logging.getLogger(__name__)

# Heartbeat cadence — decoupled from the hourly compute so the container reads
# healthy between runs (same trick as db-writer).
HEARTBEAT_INTERVAL_S: float = 60.0
# Default compute interval when none is injected (main derives it from
# ANALYTICS_INTERVAL_MIN).
DEFAULT_INTERVAL_S: int = 3600
# Feature-history window for the GMM training matrix.
FEATURE_HISTORY_WINDOW_DAYS: int = 90
# PC3 rolling window : latest N snapshots (z-score window, not the fit window).
PC3_SNAPSHOT_LIMIT: int = 200
# Portfolio rollups cadence (#368). 5 min keeps the daily close and the 24 h
# attribution within one badge window of live; the endpoints read one row.
DEFAULT_ROLLUP_INTERVAL_S: float = 300.0


def _any_butterfly_violation(surface: dict[str, Any]) -> bool:
    """A butterfly no-arb violation exists when any SVI tenor node reports a
    negative ``butterfly_g_min`` (g(k) < 0). Replicated verbatim from the
    vol-engine (trivial, no core dependency)."""
    svi = surface.get("_svi") or {}
    for tenor_node in svi.values():
        if isinstance(tenor_node, dict):
            g = tenor_node.get("butterfly_g_min")
            if isinstance(g, (int, float)) and g < 0:
                return True
    return False


@contextmanager
def _thread_cap(limit: int) -> Iterator[None]:
    """Cap BLAS / OpenMP / scikit-learn worker threads for the block
    (``threadpoolctl`` ships with scikit-learn); a no-op where it is absent."""
    try:
        from threadpoolctl import threadpool_limits
    except Exception:  # pragma: no cover - threadpoolctl missing in a slim env
        yield
        return
    with threadpool_limits(limits=max(1, int(limit))):
        yield


class _RedisLike(Protocol):
    async def get(self, name: str) -> Any: ...
    async def set(self, name: str, value: str, ex: int | None = ...) -> Any: ...
    async def publish(self, channel: str, message: str) -> int: ...


class AnalyticsEngine:
    """Long-running async task : hourly batch analytics, no IB."""

    def __init__(
        self,
        redis: _RedisLike,
        symbol: str = "EURUSD",
        interval_s: int = DEFAULT_INTERVAL_S,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        rollup_interval_s: float = DEFAULT_ROLLUP_INTERVAL_S,
    ) -> None:
        self.redis = redis
        self.symbol = symbol
        self.interval_s = interval_s
        self.rollup_interval_s = rollup_interval_s
        # The capacity dial (#371): applied by the config watcher, read at the
        # start of every cycle. The constructor values are the pre-config
        # defaults; ``apply_config`` overrides them.
        self.capacity: CapacityConfig = CapacityConfig()
        self.config_version: int = -1
        # Injected for tests ; falls back to the process-wide async sessionmaker.
        self._sessionmaker = sessionmaker or get_sessionmaker()
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def apply_config(self, config: VolTradingConfig, version: int = -1) -> None:
        """Hot-reload the capacity dial (#371, #329): cadence, training window,
        PCA snapshot gap, rollup cadence and the thread cap all take effect on
        the next loop iteration, no restart."""
        cap = config.capacity
        changed = cap != self.capacity
        self.capacity = cap
        self.config_version = version
        self.interval_s = int(cap.analytics_interval_min * 60)
        self.rollup_interval_s = float(cap.rollup_interval_s)
        logger.info(
            "analytics_config_reloaded",
            extra={
                "version": version, "preset": cap.preset, "interval_s": self.interval_s,
                "training_window_days": cap.training_window_days, "thread_cap": cap.thread_cap,
                "rollup_interval_s": self.rollup_interval_s, "changed": changed,
            },
        )

    async def run(self) -> None:
        """Start the decoupled heartbeat, then run the compute loop until stop."""
        from shared.observability import observed_cycle

        logger.info(
            "analytics_engine_started",
            extra={"symbol": self.symbol, "interval_s": self.interval_s},
        )
        # Supervised (#337): a side loop that dies is logged, counted and restarted.
        heartbeat_task = spawn_supervised(self._heartbeat_loop, name="analytics_heartbeat", engine="analytics_engine")
        rollup_task = spawn_supervised(self._rollup_loop, name="analytics_rollups", engine="analytics_engine")
        try:
            while not self._stop.is_set():
                # P0 obs : one cycle_id per compute run + metrics emitted.
                with observed_cycle("analytics_engine"):
                    await self.run_cycle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
                    break
                except TimeoutError:
                    continue
        finally:
            heartbeat_task.cancel()
            rollup_task.cancel()
            for t in (heartbeat_task, rollup_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            logger.info("analytics_engine_stopped", extra={"symbol": self.symbol})

    async def _rollup_loop(self) -> None:
        """Materialise the portfolio rollups every ``rollup_interval_s`` (#368).
        Runs on its own cadence so a slow GMM fit never delays the daily close
        the Portfolio tab reads; counted as an engine cycle so the stall alert
        covers it."""
        from shared.observability import observed_cycle

        while not self._stop.is_set():
            with observed_cycle("analytics_engine"):
                await self.run_rollup_cycle()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.rollup_interval_s)
                break
            except TimeoutError:
                continue

    async def run_rollup_cycle(self) -> dict[str, Any]:
        """One pass: ``portfolio_daily`` (backfill when empty, rolling 3 days
        after) then ``pnl_attribution_latest`` for every lookback. Each step is
        best-effort; the summary says which ran."""
        now = datetime.now(UTC)
        out: dict[str, Any] = {}
        try:
            async with self._sessionmaker() as session:
                out["portfolio_daily"] = await refresh_portfolio_daily(session, now=now)
        except Exception:
            logger.exception("analytics_rollup_daily_failed")
            out["portfolio_daily_error"] = True

        async def _fallback(cutoff: datetime) -> tuple[float | None, float | None]:
            return await spot_fallbacks(self.redis, cutoff, self.symbol)

        try:
            async with self._sessionmaker() as session:
                out["attribution"] = await refresh_attribution(session, now=now, spot_fallback_for=_fallback)
        except Exception:
            logger.exception("analytics_rollup_attribution_failed")
            out["attribution_error"] = True
        logger.info("analytics_rollups_done", extra={"summary": out})
        return out

    async def _heartbeat_loop(self) -> None:
        """Ping ``heartbeat:analytics_engine`` every HEARTBEAT_INTERVAL_S so the
        container is healthy between the (much slower) compute cycles."""
        while not self._stop.is_set():
            try:
                await publisher.set_heartbeat(self.redis, keys.ENGINE_ANALYTICS)
            except Exception:
                logger.warning("analytics_heartbeat_publish_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
                break
            except TimeoutError:
                continue

    async def run_cycle(self) -> None:
        """Run the 3 analytics steps best-effort — one failure never blocks the
        others, and the cycle always returns without raising. The BLAS /
        scikit-learn thread pools are capped to ``capacity.thread_cap`` for the
        whole cycle (#371): the dial decides how wide the burst is."""
        with _thread_cap(self.capacity.thread_cap):
            for step in (
                self._collect_snapshot,
                self._refit_gmm,
                self._recompute_pc3_history,
            ):
                try:
                    await step()
                except Exception:
                    logger.exception(
                        "analytics_step_failed", extra={"step": step.__name__}
                    )

    # ------------------------------------------------------------------ step 1
    async def _collect_snapshot(self) -> None:
        """Build the hourly 30-dim PCA surface snapshot from the latest vol
        surface in Redis and publish it to ``pca_surface_snapshot_history`` —
        gated so at most one snapshot per ``PCA_SNAPSHOT_INTERVAL_MIN`` lands.

        Ported from ``VolEngine._maybe_collect_hourly_snapshot`` : instead of
        receiving the live surface, GET it from Redis.
        """
        raw = await self.redis.get(keys.LATEST_VOL_SURFACE.format(symbol=self.symbol))
        if raw is None:
            logger.debug("analytics_snapshot_skipped_no_surface")
            return
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, TypeError):
            logger.warning("analytics_snapshot_bad_surface_json")
            return
        surface = payload.get("surface") if isinstance(payload, dict) else None
        if not isinstance(surface, dict):
            logger.debug("analytics_snapshot_skipped_no_surface_body")
            return

        x = feature_vector_from_surface(surface)
        if x is None:
            return  # incomplete surface — skip the cycle
        now = datetime.now(UTC)
        async with self._sessionmaker() as session:
            last = (await session.execute(
                select(SurfaceSnapshotHourly.timestamp)
                .where(SurfaceSnapshotHourly.symbol == self.symbol)
                .order_by(desc(SurfaceSnapshotHourly.timestamp))
                .limit(1)
            )).scalar_one_or_none()
        # Min gap between PCA snapshots: the capacity dial's
        # ``pca_snapshot_interval_min`` (default hourly = decorrelated samples
        # for a clean fit; a low value bootstraps the PCA fast). The 0.9 factor
        # avoids missing a tick when the cycle lands just under the interval.
        gap_min = float(self.capacity.pca_snapshot_interval_min)  # the dial (#371)
        if last is not None and (now - last) < timedelta(minutes=gap_min * 0.9):
            return  # not yet time

        spot = await self._read_spot()
        row: dict[str, Any] = {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "symbol": self.symbol,
            "source": "analytics_engine",
            "spot_at_snapshot": float(spot) if spot is not None else None,
            "n_strikes_present": len(x),
            "has_no_arb_violation": _any_butterfly_violation(surface),
        }
        i = 0
        for t in TENORS:
            for d in DELTAS:
                row[f"iv_{t.lower()}_{d}"] = float(x[i])
                i += 1
        await publish_db_event(
            self.redis, table="pca_surface_snapshot_history", payload=row
        )
        logger.info("analytics_snapshot_published", extra={"symbol": self.symbol})

    # ------------------------------------------------------------------ step 2
    async def _refit_gmm(self) -> None:
        """Fit the 3-component GMM regime model on 90d of feature history and
        serialize it to Redis for the vol-engine to ``infer_proba`` at the edge.

        The training matrix is 2-column ``(vol_level, vol_of_vol)`` — this
        MATCHES the vol-engine's edge inference exactly (it feeds a 2-D live
        obs ``[vol_level_pct, vov_pct_live]``). ``term_slope`` is mostly NULL
        during bootstrap and is intentionally excluded; fitting 3 columns here
        would make ``predict_proba`` reject the 2-D obs at the edge.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=self.capacity.training_window_days)  # the dial (#371)
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(
                    FeatureHistory.iv_atm_3m_pct,
                    FeatureHistory.vol_of_vol_30d_pct,
                    FeatureHistory.term_slope_pct,
                )
                .where(FeatureHistory.symbol == self.symbol)
                .where(FeatureHistory.timestamp > cutoff)
                .order_by(FeatureHistory.timestamp)
            )).all()
        feature_rows = [
            {
                "vol_level": float(r[0]) if r[0] is not None else None,
                "vol_of_vol": float(r[1]) if r[1] is not None else None,
                "term_slope": float(r[2]) if r[2] is not None else None,
            }
            for r in rows
        ]
        # Training matrix : rows with both features present (2 columns).
        train = [
            (r["vol_level"], r["vol_of_vol"])
            for r in feature_rows
            if r["vol_level"] is not None and r["vol_of_vol"] is not None
        ]
        if len(train) < MIN_OBS_GMM:
            logger.debug(
                "analytics_gmm_skipped_insufficient_obs", extra={"n": len(train)}
            )
            return
        X = np.asarray(train, dtype=float)
        gmm, fit = await asyncio.to_thread(fit_gmm, X)
        if gmm is None or fit is None:
            logger.warning("analytics_gmm_fit_returned_none")
            return
        await self.redis.set(
            keys.LATEST_REGIME_MODEL.format(symbol=self.symbol),
            json.dumps(serialize_gmm(gmm, fit)),
            ex=keys.TTL_ANALYTICS,
        )
        logger.info(
            "analytics_gmm_refit",
            extra={"n_obs": fit.n_obs, "converged": fit.converged},
        )

    # ------------------------------------------------------------------ step 3
    async def _recompute_pc3_history(self) -> None:
        """Recompute the PC3 skew/convex rolling arrays from the latest ≤200
        surface snapshots and publish them to Redis for the vol-engine to
        z-score the live snapshot against."""
        now = datetime.now(UTC)
        snap_iv_cols = [f"iv_{t.lower()}_{d}" for t in TENORS for d in DELTAS]
        async with self._sessionmaker() as session:
            snap_rows = (await session.execute(
                select(SurfaceSnapshotHourly)
                .where(SurfaceSnapshotHourly.symbol == self.symbol)
                .order_by(desc(SurfaceSnapshotHourly.timestamp))
                .limit(PC3_SNAPSHOT_LIMIT)
            )).scalars().all()
        latest_ts = getattr(snap_rows[0], "timestamp", None) if snap_rows else None
        hist_skew: list[float] = []
        hist_convex: list[float] = []
        for r in snap_rows:
            vec = [getattr(r, c) for c in snap_iv_cols]
            if any(v is None for v in vec):
                continue
            xv = np.asarray([float(v) for v in vec])
            s, c = pc3_sub_metrics(xv)
            hist_skew.append(s)
            hist_convex.append(c)
        latest_snapshot_ts = (
            latest_ts.isoformat().replace("+00:00", "Z")
            if latest_ts is not None else None
        )
        await self.redis.set(
            keys.LATEST_PC3_HISTORY.format(symbol=self.symbol),
            json.dumps({
                "fit_timestamp": now.isoformat().replace("+00:00", "Z"),
                "latest_snapshot_ts": latest_snapshot_ts,
                "skew": hist_skew,
                "convex": hist_convex,
            }),
            ex=keys.TTL_ANALYTICS,
        )
        logger.info(
            "analytics_pc3_history_published",
            extra={"symbol": self.symbol, "n": len(hist_skew)},
        )

    async def _read_spot(self) -> float | None:
        """Read the spot from Redis (``latest_spot:<symbol>``) through the one
        parser (#327). Returns None when absent — ``spot_at_snapshot`` is nullable."""
        try:
            raw = await self.redis.get(keys.LATEST_SPOT.format(symbol=self.symbol))
        except Exception:
            return None
        return parse_spot(raw)[0]
