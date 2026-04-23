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

    def _teardown(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.exception("ib_disconnect_failed")
        logger.info("vol_engine_stopped", extra={"symbol": self.symbol})
