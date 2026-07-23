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

Heartbeat is **decoupled** from the compute (db-writer split-loop idea): a
background task pings ``heartbeat:analytics_engine`` every ~60s so the
container reads healthy between the hourly compute cycles. Each step is
wrapped in try/except so one failure never blocks the others.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import numpy as np
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bus import keys, publisher
from core.vol.gmm_regime import MIN_OBS_GMM, fit_gmm, serialize_gmm
from core.vol.pca_engine import (
    DELTAS,
    TENORS,
    feature_vector_from_surface,
    pc3_sub_metrics,
)
from persistence.db import get_sessionmaker
from persistence.models import FeatureHistory, SurfaceSnapshotHourly
from shared.db_events import publish_db_event

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
    ) -> None:
        self.redis = redis
        self.symbol = symbol
        self.interval_s = interval_s
        # Injected for tests ; falls back to the process-wide async sessionmaker.
        self._sessionmaker = sessionmaker or get_sessionmaker()
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Start the decoupled heartbeat, then run the compute loop until stop."""
        from shared.observability import observed_cycle

        logger.info(
            "analytics_engine_started",
            extra={"symbol": self.symbol, "interval_s": self.interval_s},
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="analytics_heartbeat"
        )
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
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.info("analytics_engine_stopped", extra={"symbol": self.symbol})

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
        others, and the cycle always returns without raising."""
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
        # Min gap between PCA snapshots. Default ~hourly (decorrelated samples
        # for a clean fit); override with PCA_SNAPSHOT_INTERVAL_MIN (e.g. 2) to
        # bootstrap fast locally. The 0.9 factor avoids missing a tick when the
        # cycle lands just under the interval.
        gap_min = float(os.environ.get("PCA_SNAPSHOT_INTERVAL_MIN", "60"))
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
        cutoff = now - timedelta(days=FEATURE_HISTORY_WINDOW_DAYS)
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
        """Read the spot from Redis (``latest_spot:<symbol>``). Tolerates both
        the plain-float string and the JSON-dict form, mirroring the risk
        engine. Returns None when absent — ``spot_at_snapshot`` is nullable."""
        try:
            raw = await self.redis.get(keys.LATEST_SPOT.format(symbol=self.symbol))
        except Exception:
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if isinstance(payload, (int, float)):
                return float(payload)
            if isinstance(payload, dict):
                v = payload.get("mid") or payload.get("bid")
                return float(v) if v is not None else None
            return None
        except (ValueError, TypeError, AttributeError):
            return None
