"""Async VolEngine — standalone service version.

One cycle roughly every three minutes :

1. ``GET latest_spot:<symbol>`` on Redis. Skip if missing (market-data down).
2. Call the injected ``fetch_fop_chain(F)`` to get (delta, iv, strike)
   observations per tenor. Real impl reads IB FOP chain ; tests pass
   a fixture.
3. PCHIP-interpolate the smile via ``core.vol.pchip_smile``.
4. Call the injected ``fetch_ohlc()`` to get OHLC closes, compute
   ``yang_zhang_rv_pct`` + the selected P-measure estimator
   (``core.vol.har_rv`` preferred, ``core.vol.garch`` legacy).
5. Convert σ_fair^P to σ_fair^Q via ``core.vol.vrp.q_measure_from_p``.
   Signals are always generated against the Q-measure value.
6. ``SET latest_vol_surface:<symbol>`` + ``PUBLISH vol_update`` via
   ``bus.publisher.publish_vol_update``.
7. Emit a heartbeat.

Measure convention (refactor plan P0.3) :

- Everything named ``rv_*`` / ``garch_*`` / ``har_*`` is under **P**
  (physical, realised, what has / will happen on average).
- Everything named ``iv_*`` / ``sigma_mid_*`` / ``sigma_fair_q_*`` is
  under **Q** (risk-neutral, what options are priced to).
- Comparing P to Q directly is **economically incorrect** — always go
  via the VRP conversion before generating a signal.

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


def _pick_sigma_fair_p(
    surface: dict[str, Any], tenor: str, preferred_estimator: str,
) -> float | None:
    """Return σ_fair^P in percent for ``tenor`` using ``preferred_estimator``.

    Falls back to the other estimator if the preferred one is absent.
    """
    har = surface.get("_har") or {}
    garch = surface.get("_garch") or {}
    order = (har, garch) if preferred_estimator == "har" else (garch, har)
    keys_order = (("sigma_har_pct", "sigma_model_pct"),)
    for bucket in order:
        node = bucket.get(tenor) if isinstance(bucket, dict) else None
        if not isinstance(node, dict):
            continue
        for keys_set in keys_order:
            for k in keys_set:
                v = node.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
    return None


def _build_fair_q(
    surface: dict[str, Any], preferred_estimator: str,
) -> dict[str, dict[str, float]]:
    """Attach σ_fair^Q per tenor by adding VRP to the P-measure estimator.

    Returns ``{tenor: {sigma_fair_p_pct, vrp_vol_pts, sigma_fair_q_pct,
    regime}}``. Tenors missing the P estimator are skipped.
    """
    from core.vol.vrp import detect_regime, q_measure_from_p

    # Rough regime inference from the surface itself.
    rv_pct = surface.get("_rv_full_pct")
    atm_1m = ((surface.get("1M") or {}).get("atm") or {}).get("iv")
    atm_6m = ((surface.get("6M") or {}).get("atm") or {}).get("iv")
    slope = None
    if isinstance(atm_1m, (int, float)) and isinstance(atm_6m, (int, float)):
        slope = (float(atm_6m) - float(atm_1m)) * 100.0
    regime = detect_regime(
        vol_level_pct=float(rv_pct) if isinstance(rv_pct, (int, float)) else None,
        vol_of_vol_pct=None,
        term_slope_pct=slope,
    )
    out: dict[str, dict[str, float]] = {}
    for tenor in surface:
        if tenor.startswith("_") or not isinstance(surface[tenor], dict):
            continue
        sigma_p = _pick_sigma_fair_p(surface, tenor, preferred_estimator)
        if sigma_p is None:
            continue
        sigma_q, vrp = q_measure_from_p(sigma_p, tenor=tenor, regime=regime)
        out[tenor] = {
            "sigma_fair_p_pct": round(sigma_p, 4),
            "vrp_vol_pts": round(vrp, 4),
            "sigma_fair_q_pct": round(sigma_q, 4),
            "regime": regime,
        }
    return out


def _fit_svi_per_tenor(
    surface: dict[str, Any], forward: float, tenor_years: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Run SVI fit on each tenor's observed pillars + butterfly arb check.

    Returns ``{tenor: {a, b, rho, m, sigma, rmse, butterfly_g_min}}``.
    Log WARNING per tenor whose ``butterfly_g_min`` is negative — these
    fits embed negative risk-neutral densities and must not drive
    trade decisions.
    """
    from core.vol.svi import butterfly_g_min, fit_svi

    out: dict[str, dict[str, float]] = {}
    for tenor, pillar in surface.items():
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue
        T = tenor_years.get(tenor)
        if T is None:
            continue
        strikes: list[float] = []
        ivs: list[float] = []
        for label in ("10dp", "25dp", "atm", "25dc", "10dc"):
            node = pillar.get(label)
            if not isinstance(node, dict):
                continue
            iv = node.get("iv")
            strike = node.get("strike")
            if isinstance(iv, (int, float)) and isinstance(strike, (int, float)):
                strikes.append(float(strike))
                ivs.append(float(iv))
        if len(strikes) < 3:
            continue
        params = fit_svi(strikes, ivs, forward=float(forward), tenor_years=T)
        if params is None:
            continue
        # Residual RMSE on the observed pillars (total variance).
        import numpy as np

        k = np.log(np.asarray(strikes) / float(forward))
        w_fit = params.a + params.b * (
            params.rho * (k - params.m)
            + np.sqrt((k - params.m) ** 2 + params.sigma**2)
        )
        w_obs = np.asarray(ivs) ** 2 * T
        rmse = float(np.sqrt(np.mean((w_fit - w_obs) ** 2)))
        g_min = butterfly_g_min(params)
        if g_min < 0:
            logger.warning(
                "svi_butterfly_violation",
                extra={"tenor": tenor, "g_min": g_min},
            )
        out[tenor] = {
            "a": round(params.a, 6),
            "b": round(params.b, 6),
            "rho": round(params.rho, 6),
            "m": round(params.m, 6),
            "sigma": round(params.sigma, 6),
            "rmse_fit": round(rmse, 6),
            "butterfly_g_min": round(g_min, 6),
        }
    return out


def _fit_ssvi_surface(
    surface: dict[str, Any], forward: float, tenor_years: dict[str, float],
) -> dict[str, float] | None:
    """Fit SSVI (Gatheral-Jacquier 2014) across every available tenor.

    Returns ``{eta, gamma, rho, rmse}`` or ``None`` if fewer than 2
    tenors have usable data. Stored surface-wide (1 row, not per-tenor).
    """
    from core.vol.ssvi import fit_ssvi

    observations: list[tuple[float, float, float]] = []
    atm_by_tenor: dict[str, float] = {}
    for tenor, pillar in surface.items():
        if tenor.startswith("_") or not isinstance(pillar, dict):
            continue
        T = tenor_years.get(tenor)
        if T is None:
            continue
        atm_node = pillar.get("atm") if isinstance(pillar, dict) else None
        if isinstance(atm_node, dict) and isinstance(atm_node.get("iv"), (int, float)):
            atm_by_tenor[tenor] = float(atm_node["iv"])
        for label in ("10dp", "25dp", "atm", "25dc", "10dc"):
            node = pillar.get(label)
            if not isinstance(node, dict):
                continue
            iv = node.get("iv")
            strike = node.get("strike")
            if isinstance(iv, (int, float)) and isinstance(strike, (int, float)):
                observations.append((T, float(strike), float(iv)))
    if len(atm_by_tenor) < 2 or len(observations) < 5:
        return None
    result = fit_ssvi(observations, forward=float(forward), atm_iv_by_tenor_years={
        tenor_years[t]: iv for t, iv in atm_by_tenor.items() if t in tenor_years
    })
    return result


def _derive_signals(
    surface: dict[str, Any], underlying: str,
    threshold_vol_pts: float = SIGNAL_ECART_THRESHOLD_PCT,
) -> list[dict[str, Any]]:
    """Per-tenor signals : compare σ_mid (Q) to σ_fair^Q (Q), NOT to σ^P.

    Uses ``surface['_fair_q'][tenor]['sigma_fair_q_pct']`` when present
    (the refactor-plan P1 path) ; falls back to raw GARCH fair for
    back-compat when ``_fair_q`` is absent (e.g. OHLC fetch failed).
    """
    fair_q = surface.get("_fair_q") or {}
    legacy_garch = surface.get("_garch") or {}
    out: list[dict[str, Any]] = []
    for tenor, node in surface.items():
        if tenor.startswith("_") or not isinstance(node, dict):
            continue
        atm_node = node.get("atm")
        if not isinstance(atm_node, dict):
            continue
        iv_mid = atm_node.get("iv")
        if iv_mid is None:
            continue
        fair_q_node = fair_q.get(tenor)
        if isinstance(fair_q_node, dict) and isinstance(
            fair_q_node.get("sigma_fair_q_pct"), (int, float)
        ):
            sigma_fair_q = float(fair_q_node["sigma_fair_q_pct"])
            sigma_fair_p = float(fair_q_node.get("sigma_fair_p_pct", sigma_fair_q))
            vrp = float(fair_q_node.get("vrp_vol_pts", 0.0))
        else:
            # Back-compat : no _fair_q aggregate (OHLC missing). Fall back
            # to the legacy GARCH value interpreted as if it were Q — same
            # behaviour as R9 sandbox pre-VRP, still useful for smoke.
            garch_node = legacy_garch.get(tenor)
            if not isinstance(garch_node, dict) or not isinstance(
                garch_node.get("sigma_model_pct"), (int, float)
            ):
                continue
            sigma_fair_q = float(garch_node["sigma_model_pct"])
            sigma_fair_p = sigma_fair_q
            vrp = 0.0
        sigma_mid_pct = float(iv_mid) * 100.0
        ecart = sigma_mid_pct - sigma_fair_q
        if abs(ecart) <= threshold_vol_pts:
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
            "sigma_fair": round(sigma_fair_q, 4),
            "ecart": round(ecart, 4),
            "signal_type": signal_type,
            "rv": round(float(surface.get("_rv_full_pct")), 4)
            if isinstance(surface.get("_rv_full_pct"), (int, float))
            else None,
            "sigma_fair_p": round(sigma_fair_p, 4),
            "vrp_vol_pts": round(vrp, 4),
        })
    return out


def _atm_pct_helper(surface: dict[str, Any], tenor: str) -> float | None:
    """Re-implementation of regime_engine._atm_pct (avoid private import)."""
    node = surface.get(tenor)
    if not isinstance(node, dict):
        return None
    atm = node.get("atm")
    if not isinstance(atm, dict):
        return None
    iv = atm.get("iv")
    if not isinstance(iv, (int, float)):
        return None
    return round(float(iv) * 100.0, 4)


def _any_butterfly_violation(surface: dict[str, Any]) -> bool:
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
        signal_threshold_vol_pts: float | None = None,
        signal_model_p: str = "har",
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
        self._signal_threshold = (
            signal_threshold_vol_pts
            if signal_threshold_vol_pts is not None
            else SIGNAL_ECART_THRESHOLD_PCT
        )
        self._signal_model_p = signal_model_p  # 'har' or 'garch'
        self._stop = asyncio.Event()

    def apply_config(self, config: Any) -> None:
        """Hot-reload signal thresholds from a VolTradingConfig instance.

        Called by the config watcher when ``config:changed`` is published
        on Redis. Only fields actually consumed by the engine today
        (threshold, model_p) are applied -- future fields land here as
        their phases go live.
        """
        self._signal_threshold = float(config.signal.threshold_vol_pts)
        self._signal_model_p = str(config.signal.model_p)
        logger.info(
            "vol_engine_config_reloaded threshold=%.3f model=%s",
            self._signal_threshold, self._signal_model_p,
        )

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

        signals = _derive_signals(
            surface, self.symbol, threshold_vol_pts=self._signal_threshold,
        )
        # Step 1 — regime gating : compute _regime payload + persist via db_events.
        regime_rows = await self._compute_regime(surface)
        if regime_rows is not None:
            surface["_regime"] = regime_rows["payload"]

        # Step 2 — PCA signals : project surface on active model, generate signals.
        pca_rows = await self._compute_pca_signals(surface)
        if pca_rows is not None:
            surface["_pca_signals"] = pca_rows["payload"]

        # Step 2 — hourly snapshot for PCA fit history.
        hourly_snapshot = await self._maybe_collect_hourly_snapshot(surface, F)

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

            from shared.db_events import publish_db_event

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
            # SVI per-tenor params (P2.1).
            svi_all = surface.get("_svi") or {}
            for tenor, p in svi_all.items():
                await publish_db_event(
                    self.redis, table="svi_params",
                    payload={
                        "timestamp": ts_iso, "underlying": self.symbol,
                        "tenor": tenor, **p,
                    },
                )
            # SSVI surface-level (P2.2).
            ssvi = surface.get("_ssvi")
            if isinstance(ssvi, dict):
                await publish_db_event(
                    self.redis, table="ssvi_params",
                    payload={
                        "timestamp": ts_iso, "underlying": self.symbol,
                        "spot": float(F), **ssvi,
                    },
                )
            # Step 1 : persist regime_snapshots + feature_history.
            if regime_rows is not None:
                await publish_db_event(
                    self.redis, table="regime_snapshots",
                    payload={**regime_rows["snapshot_row"], "timestamp": ts_iso},
                )
                await publish_db_event(
                    self.redis, table="feature_history",
                    payload={**regime_rows["feature_row"], "timestamp": ts_iso},
                )
            # Step 2 : persist pca_signals (1 row per PC) + hourly snapshot.
            if pca_rows is not None:
                for sig_row in pca_rows["signal_rows"]:
                    await publish_db_event(
                        self.redis, table="pca_signals",
                        payload={**sig_row, "timestamp": ts_iso},
                    )
            if hourly_snapshot is not None:
                await publish_db_event(
                    self.redis, table="surface_snapshots_hourly",
                    payload=hourly_snapshot,
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
                out["_rv_full_pct"] = rv_full  # P-measure, percent
                closes = ohlc["close"].to_numpy() if hasattr(ohlc, "close") else None
                # Always compute GARCH for parity + legacy consumers.
                if closes is not None and len(closes) >= 5:
                    try:
                        out["_garch"] = fit_and_project_garch(
                            closes, tenor_t=self.tenor_t, rv_full=rv_full
                        )
                    except Exception:
                        logger.exception("garch_projection_failed")
                # HAR-RV (preferred P-measure estimator, Corsi 2009).
                if closes is not None and len(closes) >= 30:
                    try:
                        from core.vol.har_rv import fit_and_project_har

                        tenor_days = {
                            k: round(v * 365) for k, v in self.tenor_t.items()
                        }
                        out["_har"] = fit_and_project_har(closes, tenor_days)
                    except Exception:
                        logger.exception("har_projection_failed")
                # Q-measure conversion : σ_fair^Q = σ_fair^P + VRP(tenor, regime).
                try:
                    out["_fair_q"] = _build_fair_q(
                        surface=out,
                        preferred_estimator=self._signal_model_p,
                    )
                except Exception:
                    logger.exception("q_measure_conversion_failed")
        # SVI fit per tenor (Phase P2.1) + butterfly arbitrage health.
        try:
            out["_svi"] = _fit_svi_per_tenor(
                surface=out, forward=F, tenor_years=self.tenor_t,
            )
        except Exception:
            logger.exception("svi_fit_per_tenor_failed")
        # SSVI surface-level fit (Phase P2.2).
        try:
            ssvi = _fit_ssvi_surface(surface=out, forward=F, tenor_years=self.tenor_t)
            if ssvi is not None:
                out["_ssvi"] = ssvi
        except Exception:
            logger.exception("ssvi_fit_failed")
        return out

    async def _compute_regime(self, surface: dict[str, Any]) -> dict[str, Any] | None:
        """Read history from Postgres + compute Step 1 regime payload.

        Best-effort : returns None on any DB error so the cycle still publishes
        the surface. The first ~30 cycles will have null vol_of_vol/z-scores
        while feature_history fills up (cf. STEP1 §9 bootstrap).
        """
        try:
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import select

            from persistence.db import get_sessionmaker
            from persistence.models import (
                Event,
                FeatureHistory,
                VrpTableDefault,
            )

            surface["_symbol"] = self.symbol
            now = datetime.now(UTC)
            async with get_sessionmaker()() as session:
                cutoff_30d = now - timedelta(days=30)
                cutoff_90d = now - timedelta(days=90)
                iv_3m_rows = (await session.execute(
                    select(FeatureHistory.iv_atm_3m_pct)
                    .where(FeatureHistory.symbol == self.symbol)
                    .where(FeatureHistory.timestamp > cutoff_30d)
                    .order_by(FeatureHistory.timestamp)
                )).scalars().all()
                iv_3m_history = [float(x) for x in iv_3m_rows if x is not None]

                hist_rows = (await session.execute(
                    select(
                        FeatureHistory.iv_atm_3m_pct,
                        FeatureHistory.vol_of_vol_30d_pct,
                        FeatureHistory.term_slope_pct,
                        FeatureHistory.vol_level_z90,
                        FeatureHistory.vol_of_vol_z90,
                        FeatureHistory.term_slope_z90,
                    )
                    .where(FeatureHistory.symbol == self.symbol)
                    .where(FeatureHistory.timestamp > cutoff_90d)
                    .order_by(FeatureHistory.timestamp)
                )).all()
                feature_history_rows = [
                    {
                        "vol_level": float(r[0]) if r[0] is not None else None,
                        "vol_of_vol": float(r[1]) if r[1] is not None else None,
                        "term_slope": float(r[2]) if r[2] is not None else None,
                    } for r in hist_rows
                ]
                # E3 enrichment inputs : value-history (for pct) and
                # z-history (for bucket).
                value_history_for_pct = {
                    "vol_level": [float(r[0]) for r in hist_rows if r[0] is not None],
                    "vol_of_vol": [float(r[1]) for r in hist_rows if r[1] is not None],
                    "term_slope": [float(r[2]) for r in hist_rows if r[2] is not None],
                }
                z_history_for_bucket = {
                    "vol_level": [float(r[3]) for r in hist_rows if r[3] is not None],
                    "vol_of_vol": [float(r[4]) for r in hist_rows if r[4] is not None],
                    "term_slope": [float(r[5]) for r in hist_rows if r[5] is not None],
                }

                next_event_obj = (await session.execute(
                    select(Event)
                    .where(Event.impact == "high")
                    .where(Event.region.in_(["EU", "US"]))
                    .where(Event.scheduled_at > now)
                    .order_by(Event.scheduled_at)
                    .limit(1)
                )).scalar_one_or_none()
                next_event = None
                if next_event_obj is not None:
                    days = (next_event_obj.scheduled_at - now).total_seconds() / 86400.0
                    next_event = {
                        "event_type": next_event_obj.event_type,
                        "scheduled_at_iso": next_event_obj.scheduled_at.isoformat().replace("+00:00", "Z"),
                        "days_remaining": round(days, 4),
                    }

                vrp_rows = (await session.execute(
                    select(VrpTableDefault.regime, VrpTableDefault.tenor, VrpTableDefault.vrp_vol_pts)
                )).all()
                vrp_lookup = {(r[0], r[1]): float(r[2]) for r in vrp_rows}

            from core.vol.regime_engine import compute_regime_snapshot

            # GMM proba inference (Step 1 §3 zone 2). Fits on 2 features
            # (vol_level, vol_of_vol) using the entire feature_history we
            # already pulled. Returns None if < MIN_OBS or features missing.
            gmm_probas = self._fit_and_infer_gmm(
                feature_history_rows=feature_history_rows,
                vol_level_pct=_atm_pct_helper(surface, "3M"),
                vov_pct_live=None,  # computed inside compute_regime_snapshot
                iv_3m_history_pct=iv_3m_history,
            )

            result = compute_regime_snapshot(
                surface=surface,
                iv_3m_history_pct=iv_3m_history,
                feature_history_rows=feature_history_rows,
                next_event=next_event,
                vrp_lookup=vrp_lookup,
                now_utc_iso=now.isoformat().replace("+00:00", "Z"),
                gmm_probabilities=gmm_probas,
            )
            # E3 enrichment : load last-hour regime_snapshots z-scores per
            # feature for the OLS slope, then stamp bucket/pct/signal/Δz on
            # the snapshot_row before persisting.
            from core.vol.feature_enrichment_stamp import stamp_enrichment
            from persistence.models import RegimeSnapshot as _RS
            cutoff_1h = now - timedelta(hours=1)
            async with get_sessionmaker()() as session2:
                recent_rows = (await session2.execute(
                    select(
                        _RS.timestamp, _RS.vol_level_z, _RS.vol_of_vol_z, _RS.term_slope_z,
                    )
                    .where(_RS.symbol == self.symbol)
                    .where(_RS.timestamp >= cutoff_1h)
                    .order_by(_RS.timestamp)
                )).all()
            recent_z_for_slope = {
                "vol_level": [(r[0], float(r[1])) for r in recent_rows if r[1] is not None],
                "vol_of_vol": [(r[0], float(r[2])) for r in recent_rows if r[2] is not None],
                "term_slope": [(r[0], float(r[3])) for r in recent_rows if r[3] is not None],
            }
            result["snapshot_row"] = stamp_enrichment(
                result["snapshot_row"],
                z_history=z_history_for_bucket,
                value_history=value_history_for_pct,
                recent_z=recent_z_for_slope,
                now=now,
            )
            # Fetch last 2 labels to project the gate decision for audit.
            payload = result["payload"]
            label_now = payload.get("label")
            from sqlalchemy import desc as _desc

            from core.vol.regime_engine import gate_decision
            from persistence.models import RegimeSnapshot
            async with get_sessionmaker()() as session:
                last_labels = (await session.execute(
                    select(RegimeSnapshot.label)
                    .where(RegimeSnapshot.symbol == self.symbol)
                    .order_by(_desc(RegimeSnapshot.timestamp))
                    .limit(2)
                )).scalars().all()
            history_labels = [label_now, *last_labels]  # [t, t-1, t-2]
            gate = gate_decision(
                label_now or "calm",
                payload.get("event_dampener", False),
                history_labels,
            )

            # Structured per-cycle log (cf. STEP1 §12 acceptance).
            logger.info(
                "regime_cycle symbol=%s label=%s method=%s event_dampener=%s "
                "next_event_type=%s days_to_event=%s "
                "vol_level=%s vol_of_vol=%s term_slope=%s "
                "gmm_active=%s n_feature_history=%d "
                "gate_authorized=%s gate_reason=%s gate_size_mult=%.2f",
                self.symbol,
                payload.get("label"),
                payload.get("method"),
                payload.get("event_dampener"),
                (payload.get("next_event") or {}).get("type"),
                (payload.get("next_event") or {}).get("days_remaining"),
                (payload.get("features") or {}).get("vol_level", {}).get("value"),
                (payload.get("features") or {}).get("vol_of_vol", {}).get("value"),
                (payload.get("features") or {}).get("term_slope", {}).get("value"),
                gmm_probas is not None,
                len(feature_history_rows),
                gate.authorized, gate.reason, gate.size_mult,
            )
            return result
        except Exception:
            logger.exception("compute_regime_failed")
            return None

    def _fit_and_infer_gmm(
        self, *,
        feature_history_rows: list[dict[str, float | None]],
        vol_level_pct: float | None,
        vov_pct_live: float | None,
        iv_3m_history_pct: list[float],
    ) -> dict[str, float] | None:
        """Fit a 3-component GMM on (vol_level, vol_of_vol) historical pairs
        and project the live observation. Returns ``{calm, stressed, pre_event}``
        probas, or None if insufficient data.

        We only use 2 of the 3 features because :term_slope is mostly NULL
        in feature_history during bootstrap (single-tenor IV index from the
        IB historical backfill). The 2D model still cleanly separates calm
        (low vol+low vov) from stressed (high vol+high vov), with pre_event
        falling in the middle of the vol_level distribution.
        """
        try:
            import numpy as np

            from core.vol.gmm_regime import MIN_OBS_GMM, fit_gmm, infer_proba

            # Build training matrix from history rows that have both features.
            train: list[tuple[float, float]] = [
                (r["vol_level"], r["vol_of_vol"])
                for r in feature_history_rows
                if r.get("vol_level") is not None and r.get("vol_of_vol") is not None
            ]
            if len(train) < MIN_OBS_GMM:
                return None
            X = np.asarray(train, dtype=float)

            # Live obs : need a vov estimate. If not passed, compute on the fly.
            if vov_pct_live is None and len(iv_3m_history_pct) >= 20:
                arr = np.asarray(iv_3m_history_pct, dtype=float)
                vov_pct_live = float(arr.std(ddof=1))
            if vol_level_pct is None or vov_pct_live is None:
                return None

            gmm, fit = fit_gmm(X)
            if gmm is None or fit is None or not fit.converged:
                return None

            x = np.asarray([vol_level_pct, vov_pct_live], dtype=float)
            res = infer_proba(gmm, x, fit)
            return {
                "calm": res.p_calm,
                "stressed": res.p_stressed,
                "pre_event": res.p_pre_event,
            }
        except Exception:
            logger.exception("gmm_inference_failed")
            return None

    async def _compute_pca_signals(self, surface: dict[str, Any]) -> dict[str, Any] | None:
        """Project surface on active PCA model + emit 3 signals.

        Returns None on no active model (bootstrap), incomplete surface, or
        DB error. The frontend shows a 'bootstrap' state when payload absent.
        """
        try:
            from datetime import UTC, datetime, timedelta

            import numpy as np
            from sqlalchemy import desc, select

            from core.vol.pca_engine import (
                DELTAS,
                TENORS,
                actionable_check,
                check_coherence,
                classify_label,
                feature_vector_from_surface,
                is_persistent,
                pc3_sub_metrics,
                project,
                zscore_against,
            )
            from persistence.db import get_sessionmaker
            from persistence.models import (
                PcaModel,
                PcaSignal,
                SignalRecommendationsMap,
                SurfaceSnapshotHourly,
            )

            x = feature_vector_from_surface(surface)
            if x is None:
                return None

            now = datetime.now(UTC)
            async with get_sessionmaker()() as session:
                model = (await session.execute(
                    select(PcaModel).where(PcaModel.is_active.is_(True)).limit(1)
                )).scalar_one_or_none()
                if model is None:
                    return {
                        "payload": {
                            "model_version": None, "state": "bootstrap",
                            "signals": {}, "diagnostics": {"reason": "no_active_pca_model"},
                        },
                        "signal_rows": [],
                    }

                means = np.asarray(model.means, dtype=float)
                stds = np.asarray(model.stds, dtype=float)
                loadings = np.asarray(model.loadings, dtype=float)
                var_ratio = list(model.variance_explained_ratio or [])
                raw_scores = project(x, means, stds, loadings)

                # History per PC for z-score + persistence.
                cutoff = now - timedelta(days=90)
                hist_rows = (await session.execute(
                    select(PcaSignal.pc_id, PcaSignal.raw_score, PcaSignal.z_score, PcaSignal.timestamp)
                    .where(PcaSignal.pca_model_id == model.id)
                    .where(PcaSignal.timestamp > cutoff)
                    .order_by(desc(PcaSignal.timestamp))
                )).all()
                hist_raw: dict[int, list[float]] = {1: [], 2: [], 3: []}
                hist_z: dict[int, list[float]] = {1: [], 2: [], 3: []}
                for r in hist_rows:
                    if r[0] in hist_raw:
                        hist_raw[r[0]].append(float(r[1]))
                        hist_z[r[0]].append(float(r[2]))

                rec_rows = (await session.execute(
                    select(
                        SignalRecommendationsMap.pc_id, SignalRecommendationsMap.signal_label,
                        SignalRecommendationsMap.recommended_structure,
                        SignalRecommendationsMap.default_tenor,
                    ).where(SignalRecommendationsMap.is_active.is_(True))
                )).all()
                rec_map = {(r[0], r[1]): f"{r[2]}_{r[3]}" for r in rec_rows}

                # PC3 sub-signals : skew + convex history from snapshot_hourly.
                # We cap at 200 latest rows — rolling z-score window, not the
                # PCA fit window. Cheap : 200 × 30 floats.
                snap_iv_cols = [
                    f"iv_{t.lower()}_{d}" for t in TENORS for d in DELTAS
                ]
                snap_rows = (await session.execute(
                    select(SurfaceSnapshotHourly)
                    .where(SurfaceSnapshotHourly.symbol == self.symbol)
                    .order_by(desc(SurfaceSnapshotHourly.timestamp))
                    .limit(200)
                )).scalars().all()
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
                cur_skew, cur_convex = pc3_sub_metrics(x)
                skew_z = zscore_against(cur_skew, hist_skew)
                convex_z = zscore_against(cur_convex, hist_convex)
                pc3_sub = {
                    "skew_z": round(skew_z, 2),
                    "convex_z": round(convex_z, 2),
                }

            signals_payload: dict[str, dict] = {}
            signal_rows: list[dict] = []
            for pc_id in (1, 2, 3):
                idx = pc_id - 1
                raw = float(raw_scores[idx]) if idx < len(raw_scores) else 0.0
                z = zscore_against(raw, hist_raw[pc_id])
                label = classify_label(z)
                # Stability proxy : cosine_sim_pcN vs previous fit.
                cos_sim = getattr(model, f"cosine_similarity_pc{pc_id}", None)
                cos_sim_f = float(cos_sim) if cos_sim is not None else 1.0
                stable = cos_sim_f >= 0.85
                ve = float(var_ratio[idx]) if idx < len(var_ratio) else 0.0
                persistent = is_persistent([z, *hist_z[pc_id]])
                cum_var = float(sum(var_ratio[:3])) if var_ratio else 0.0
                n_obs = int(model.n_obs_used)
                flag = actionable_check(
                    pc_id=pc_id, z_score=z, label=label,
                    loadings_stable=stable, variance_explained=ve,
                    persistent=persistent,
                    n_obs=n_obs, cumulative_variance=cum_var,
                )
                # Always compute the would-be recommended structure so the
                # UI shows it greyed-out when not actionable.
                rec = rec_map.get((pc_id, label))
                sub = pc3_sub if pc_id == 3 else None
                node = {
                    "z_score": round(z, 2),
                    "raw_score": round(raw, 4),
                    "label": label,
                    "actionable": flag.actionable,
                    "actionable_reason": flag.reason,
                    "recommended_structure": rec,
                }
                if sub is not None:
                    node["sub_signals"] = sub
                signals_payload[f"pc{pc_id}"] = node
                signal_rows.append({
                    "symbol": self.symbol, "pca_model_id": int(model.id),
                    "pc_id": pc_id, "raw_score": raw, "z_score": z,
                    "label": label, "actionable": flag.actionable,
                    "actionable_reason": flag.reason,
                    "recommended_structure": rec, "sub_signals": sub,
                })

            payload = {
                "model_version": model.version,
                "fit_timestamp": model.fit_timestamp.isoformat().replace("+00:00", "Z"),
                "fit_window_start": model.fit_window_start.isoformat().replace("+00:00", "Z"),
                "fit_window_end": model.fit_window_end.isoformat().replace("+00:00", "Z"),
                "n_obs_in_fit": model.n_obs_used,
                "state": "stable" if all(
                    (getattr(model, f"cosine_similarity_pc{i}", None) is None)
                    or (float(getattr(model, f"cosine_similarity_pc{i}")) >= 0.85)
                    for i in (1, 2, 3)
                ) else "unstable",
                "variance_explained": {
                    "pc1": round(var_ratio[0], 3) if len(var_ratio) > 0 else 0.0,
                    "pc2": round(var_ratio[1], 3) if len(var_ratio) > 1 else 0.0,
                    "pc3": round(var_ratio[2], 3) if len(var_ratio) > 2 else 0.0,
                    "cumulative": round(sum(var_ratio[:3]), 3) if var_ratio else 0.0,
                },
                "loadings_stable": {
                    f"pc{i}": (
                        getattr(model, f"cosine_similarity_pc{i}", None) is None
                        or float(getattr(model, f"cosine_similarity_pc{i}")) >= 0.85
                    ) for i in (1, 2, 3)
                },
                "signals": signals_payload,
                "coherence": check_coherence(signals_payload),
            }
            return {"payload": payload, "signal_rows": signal_rows}
        except Exception:
            logger.exception("compute_pca_signals_failed")
            return None

    async def _maybe_collect_hourly_snapshot(
        self, surface: dict[str, Any], spot: float,
    ) -> dict[str, Any] | None:
        """If the last snapshot is older than 1 hour, build a new 30-dim row."""
        try:
            from datetime import UTC, datetime, timedelta

            from sqlalchemy import desc, select

            from core.vol.pca_engine import DELTAS, TENORS, feature_vector_from_surface
            from persistence.db import get_sessionmaker
            from persistence.models import SurfaceSnapshotHourly

            x = feature_vector_from_surface(surface)
            if x is None:
                return None
            now = datetime.now(UTC)
            async with get_sessionmaker()() as session:
                last = (await session.execute(
                    select(SurfaceSnapshotHourly.timestamp)
                    .where(SurfaceSnapshotHourly.symbol == self.symbol)
                    .order_by(desc(SurfaceSnapshotHourly.timestamp))
                    .limit(1)
                )).scalar_one_or_none()
            if last is not None and (now - last) < timedelta(minutes=55):
                return None  # not yet time

            row: dict[str, Any] = {
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "symbol": self.symbol, "source": "live_engine",
                "spot_at_snapshot": float(spot),
                "n_strikes_present": len(x),
                "has_no_arb_violation": _any_butterfly_violation(surface),
            }
            i = 0
            for t in TENORS:
                for d in DELTAS:
                    row[f"iv_{t.lower()}_{d}"] = float(x[i])
                    i += 1
            return row
        except Exception:
            logger.exception("maybe_collect_hourly_snapshot_failed")
            return None

    def _teardown(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.exception("ib_disconnect_failed")
        logger.info("vol_engine_stopped", extra={"symbol": self.symbol})
