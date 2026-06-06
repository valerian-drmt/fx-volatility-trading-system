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
from core.vol.pchip_smile import interpolate_delta_pillars
from shared.market_hours import is_fx_market_open, market_gate_active

logger = logging.getLogger(__name__)

CYCLE_S = 180.0        # hard 3-min cadence : work-then-sleep-to-deadline.
SKIP_BACKOFF_S = 5.0   # short sleep when a cycle skipped (no spot / no surface)
                       # — prevents CPU spin while the upstream feed catches up.
DEFAULT_TENOR_T = {
    "1M": 1 / 12, "2M": 2 / 12, "3M": 3 / 12,
    "4M": 4 / 12, "5M": 5 / 12, "6M": 6 / 12,
}


def _fit_svi_from_triples(
    triples: list[tuple[float, float, float]] | Any,
    forward: float,
    tenor_years: float,
) -> Any:
    """Fit SVI to raw (delta, iv, strike) observations. Returns ``SviParams`` or None."""
    from core.vol.svi import fit_svi

    try:
        items = list(triples)
    except TypeError:
        return None
    strikes: list[float] = []
    ivs: list[float] = []
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            continue
        _delta, iv, strike = item[0], item[1], item[2]
        if isinstance(iv, (int, float)) and isinstance(strike, (int, float)):
            strikes.append(float(strike))
            ivs.append(float(iv))
    if len(strikes) < 3:
        return None
    return fit_svi(strikes, ivs, forward=float(forward), tenor_years=float(tenor_years))


def _build_svi_fallback(
    params: Any,
    forward: float,
    tenor_years: float | None,
    observations: list[tuple[float, float, float]] | Any,
) -> Any:
    """Return a delta -> (iv, strike) callable backed by the calibrated SVI.

    Returns ``None`` (disables the fallback) when the SVI is mis-calibrated
    locally — i.e. ``butterfly_g_min < 0``. Propagating an arb-violating
    fit into wing pillars would be worse than leaving them None.

    The conversion delta -> log-moneyness uses the Black-76 forward delta
    relation ``δ = Φ(d1)`` with ``d1 = (-k + σ²T/2) / (σ√T)``. Since σ
    itself depends on k via SVI, we fixed-point iterate (3-5 sweeps from
    σ_atm) — this converges quickly across the practical wing range.
    """
    import math

    import numpy as np
    from scipy.stats import norm

    from core.vol.svi import butterfly_g_min, svi_iv

    if params is None or tenor_years is None or tenor_years <= 0:
        return None
    if butterfly_g_min(params) < 0:
        return None

    T = float(tenor_years)
    F = float(forward)

    # Seed σ for the delta -> k inversion : prefer the observed ATM IV when
    # available (best local first guess), else evaluate SVI at k=0.
    seed_iv = float(svi_iv(np.array([0.0]), params, T)[0])
    try:
        for d, iv, _k in observations:
            if 0.45 <= float(d) <= 0.55 and isinstance(iv, (int, float)) and iv > 0:
                seed_iv = float(iv)
                break
    except (TypeError, ValueError):
        pass

    def _fb(delta_target: float) -> tuple[float, float] | None:
        # Φ⁻¹ blows up at 0 / 1 — clamp inside the open interval.
        d_clamped = min(max(float(delta_target), 1e-4), 1.0 - 1e-4)
        d1 = float(norm.ppf(d_clamped))
        sigma = seed_iv
        k = 0.0
        for _ in range(6):
            k = 0.5 * sigma * sigma * T - sigma * math.sqrt(T) * d1
            sigma = float(svi_iv(np.array([k]), params, T)[0])
            if not math.isfinite(sigma) or sigma <= 0:
                return None
        if not math.isfinite(k):
            return None
        K = F * math.exp(k)
        return sigma, K

    return _fb


def _svi_payload_from_params(
    params_by_tenor: dict[str, Any],
    forward: float,
    tenor_years: dict[str, float],
    observations_by_tenor: dict[str, list[tuple[float, float, float]]],
) -> dict[str, dict[str, float]]:
    """Build the ``_svi`` payload from already-calibrated SviParams.

    Mirrors the shape produced by the legacy ``_fit_svi_per_tenor`` so
    downstream consumers (db-writer, regime engine) keep working : same
    keys, same rounding, same butterfly warning.
    """
    import numpy as np

    from core.vol.svi import butterfly_g_min

    out: dict[str, dict[str, float]] = {}
    for tenor, params in params_by_tenor.items():
        T = tenor_years.get(tenor)
        if T is None or params is None:
            continue
        triples = observations_by_tenor.get(tenor) or []
        strikes: list[float] = []
        ivs: list[float] = []
        for item in triples:
            if not isinstance(item, (tuple, list)) or len(item) < 3:
                continue
            _d, iv, strike = item[0], item[1], item[2]
            if isinstance(iv, (int, float)) and isinstance(strike, (int, float)):
                strikes.append(float(strike))
                ivs.append(float(iv))
        if not strikes:
            continue
        k = np.log(np.asarray(strikes) / float(forward))
        diff = k - params.m
        sq = np.sqrt(diff * diff + params.sigma * params.sigma)
        w_fit = params.a + params.b * (params.rho * diff + sq)
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
        tenor_t: dict[str, float] | None = None,
    ) -> None:
        self.ib = ib
        self.redis = redis
        self.symbol = symbol
        self.ib_host = ib_host
        self.ib_port = ib_port
        self.client_id = client_id
        # fetch_fop_chain : (F) -> {tenor -> [(delta, iv, strike)]}
        self._fetch_fop_chain = fetch_fop_chain
        self.tenor_t = tenor_t or DEFAULT_TENOR_T
        self._stop = asyncio.Event()
        # Cycle-progress instrumentation : reset at the start of every
        # run_cycle, accumulates completed (stage,task) markers as the cycle
        # walks through its 5 pipelines. Published to Redis hash
        # ``cycle_progress:vol_engine`` and consumed by the dev panel.
        self._progress_completed: set[str] = set()
        self._progress_cycle_started_iso: str | None = None

    def apply_config(self, config: Any) -> None:
        """Hot-reload from a VolTradingConfig instance.

        Called by the config watcher when ``config:changed`` is published
        on Redis. The pricing-signals fields (``signal.threshold_vol_pts``,
        ``signal.model_p``) were dropped along with the per-tenor pricing
        signals — there's nothing engine-side left to reload at this point,
        but the hook stays so future tunables (PCA threshold, regime gate
        knobs) can land here.
        """
        del config  # currently unused — kept for forward compatibility
        logger.info("vol_engine_config_reloaded")

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from shared.ib_connection import connect_ib_with_backoff
        from shared.observability import observed_cycle

        await connect_ib_with_backoff(
            self.ib, host=self.ib_host, port=self.ib_port, client_id=self.client_id
        )
        logger.info("vol_engine_started", extra={"symbol": self.symbol})

        try:
            import time as _time

            while not self._stop.is_set():
                await publisher.set_heartbeat(self.redis, keys.ENGINE_VOL)
                deadline = _time.monotonic() + CYCLE_S
                # P0 obs : cycle_id auto-bound to structlog + metrics emitted.
                with observed_cycle("vol_engine"):
                    published = await self.run_cycle()
                elapsed_until_deadline = deadline - _time.monotonic()
                # When the cycle completed early (typical — work takes ~60-120 s,
                # cadence is 180 s), sleep the remainder so each cycle starts
                # exactly CYCLE_S apart. If the cycle ran long, loop instantly.
                # Skipped cycles (no spot / no surface) back off briefly to
                # avoid CPU spin while the upstream feed catches up.
                wait_s: float
                if not published:
                    wait_s = SKIP_BACKOFF_S
                elif elapsed_until_deadline > 0:
                    wait_s = elapsed_until_deadline
                else:
                    wait_s = 0.0
                if wait_s > 0:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=wait_s)
                        break
                    except TimeoutError:
                        pass
        finally:
            self._teardown()

    async def _reset_progress(self) -> None:
        """Start a fresh cycle : clear completed-task set, stamp start time."""
        from datetime import UTC, datetime

        self._progress_completed = set()
        self._progress_cycle_started_iso = (
            datetime.now(UTC).isoformat().replace("+00:00", "Z")
        )

    async def _publish_progress(self, stage: str, task: str) -> None:
        """Mark ``(stage, task)`` as currently active in Redis.

        Tasks marked previously (in this cycle) are kept in
        ``self._progress_completed`` and shipped alongside the active marker
        so the dev panel can render done/active/pending without having to
        reconstruct the order itself. Failures here are silently swallowed —
        progress publishing must never crash a cycle.
        """
        try:
            payload = {
                "cycle_started_at": self._progress_cycle_started_iso or "",
                "stage": stage,
                "task": task,
                "completed": ",".join(sorted(self._progress_completed)),
            }
            await self.redis.hset("cycle_progress:vol_engine", mapping=payload)
            await self.redis.expire("cycle_progress:vol_engine", 600)
        except Exception:
            logger.debug("publish_progress_failed", exc_info=True)
        # Anything we just published is finished as soon as the next mark arrives.
        self._progress_completed.add(f"{stage}:{task}")

    async def _publish_cycle_done(self) -> None:
        """Flush the final task into ``completed`` and clear the active marker.

        Without this, the LAST ``_publish_progress`` call (currently
        ``publish:db_events``) would stay flagged as active in the Redis hash
        for the entire deadline-sleep at the end of the cycle, and the dev
        panel would render that bullet amber instead of green.
        """
        try:
            payload = {
                "cycle_started_at": self._progress_cycle_started_iso or "",
                "stage": "",
                "task": "",
                "completed": ",".join(sorted(self._progress_completed)),
            }
            await self.redis.hset("cycle_progress:vol_engine", mapping=payload)
            await self.redis.expire("cycle_progress:vol_engine", 600)
        except Exception:
            logger.debug("publish_cycle_done_failed", exc_info=True)

    async def run_cycle(self) -> bool:
        """Single pass. Returns True if a vol_update was published.

        P2 obs : each stage wrapped in a child OTel span so the flame
        graph in Grafana shows fetch_spot → compute_surface → compute_regime
        → compute_pca → publish breakdown with per-stage duration and
        attributes (n_strikes, n_pillars, etc.).
        """
        from opentelemetry import trace as _otel
        tracer = _otel.get_tracer(__name__)

        await self._reset_progress()
        if market_gate_active() and not is_fx_market_open():
            logger.info("vol_cycle_skipped", extra={"reason": "market_closed"})
            return False

        with tracer.start_as_current_span("vol_read_spot") as span:
            F = await self._read_spot()
            span.set_attribute("spot", F if F is not None else -1)
        if F is None:
            logger.info("vol_cycle_skipped", extra={"reason": "no_spot"})
            return False

        with tracer.start_as_current_span("vol_compute_surface") as span:
            surface = await self._compute_surface(F)
            span.set_attribute("n_pillars", len(surface) if surface else 0)
        if not surface:
            logger.info("vol_cycle_skipped", extra={"reason": "no_surface"})
            return False

        # Step 1 — regime gating : compute _regime payload + persist via db_events.
        with tracer.start_as_current_span("vol_compute_regime") as span:
            regime_rows = await self._compute_regime(surface)
            if regime_rows is not None:
                surface["_regime"] = regime_rows["payload"]
                span.set_attribute("regime_label", regime_rows["payload"].get("label", "?"))

        # Step 2 — PCA signals : project surface on active model, generate signals.
        with tracer.start_as_current_span("vol_compute_pca") as span:
            pca_rows = await self._compute_pca_signals(surface)
            if pca_rows is not None:
                surface["_pca_signals"] = pca_rows["payload"]
                signals = pca_rows["payload"].get("signals", [])
                span.set_attribute("n_signals", len(signals))

        # Step 2 — hourly snapshot for PCA fit history.
        hourly_snapshot = await self._maybe_collect_hourly_snapshot(surface, F)

        await self._publish_progress("publish", "redis_set")
        with tracer.start_as_current_span("vol_redis_publish") as span:
            try:
                await publisher.publish_vol_update(
                    self.redis, symbol=self.symbol, surface_data=surface, signals_data=[],
                )
                await self._publish_progress("publish", "pubsub")
                await publisher.set_heartbeat(self.redis, keys.ENGINE_VOL)
                await self._publish_progress("publish", "heartbeat")
                span.set_attribute("symbol", self.symbol)
            except Exception:
                logger.exception("publish_vol_update_failed")
                return False

        # Fan the surface + step1/step2 outputs to the db-writer via db_events.
        try:
            from datetime import UTC, datetime

            from shared.db_events import publish_db_event

            ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            await self._publish_progress("publish", "db_events")
            await publish_db_event(
                self.redis,
                table="vol_surface_history",  # renamed in migration 023
                payload={
                    "timestamp": ts_iso,
                    "underlying": self.symbol,
                    "spot": float(F),
                    "forward": float(F),
                    "surface_data": surface,
                },
            )
            # SVI / SSVI params live in vol_surface_history.surface_data
            # (_svi / _ssvi) ; dedicated svi_params / ssvi_params tables
            # are gone. Per-tenor pricing signals also gone with the
            # vol_pricing_signal_snapshot table.
            # Step 1 : persist regime_snapshot + feature_history.
            if regime_rows is not None:
                await publish_db_event(
                    self.redis, table="regime_snapshot",  # renamed in migration 023
                    payload={**regime_rows["snapshot_row"], "timestamp": ts_iso},
                )
                await publish_db_event(
                    self.redis, table="feature_history",  # renamed in migration 023
                    payload={**regime_rows["feature_row"], "timestamp": ts_iso},
                )
            # Step 2 : persist pca_signal_history (1 row per PC) + hourly snapshot.
            if pca_rows is not None:
                for sig_row in pca_rows["signal_rows"]:
                    await publish_db_event(
                        self.redis, table="pca_signal_history",  # renamed in migration 023
                        payload={**sig_row, "timestamp": ts_iso},
                    )
            if hourly_snapshot is not None:
                await publish_db_event(
                    self.redis, table="surface_pca_snapshot_history",  # renamed in migration 023
                    payload=hourly_snapshot,
                )
        except Exception:
            logger.exception("publish_db_event_failed")
        # Mark the cycle finished : flush ``db_events`` into completed so the
        # last bullet flips green, and clear ``(stage, task)`` so no pipeline
        # sits as "active" during the deadline-sleep period that follows.
        await self._publish_cycle_done()
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
        # Defense in depth : reject any non-positive value — IB returns -1 as
        # a "no quote" sentinel during market-closed periods. The market-data
        # engine already filters at the source, this guard catches any leftover
        # rows in Redis from a previous run.
        spot: float | None = None
        try:
            text = raw.decode() if isinstance(raw, bytes) else raw
            spot = float(text)
        except (ValueError, TypeError):
            try:
                payload = json.loads(raw)
                spot = float(payload.get("mid") or payload.get("bid"))
            except (ValueError, TypeError, AttributeError):
                return None
        if spot is None or spot <= 0:
            return None
        return spot

    async def _compute_surface(self, F: float) -> dict[str, Any]:
        import inspect

        await self._publish_progress("vol_surface", "ib_chain_fetch")
        raw = self._fetch_fop_chain(F)
        if inspect.isawaitable(raw):
            raw = await raw
        pillars_by_tenor = raw or {}
        out: dict[str, Any] = {}

        await self._publish_progress("vol_surface", "svi_per_tenor")
        # Pre-fit SVI per tenor on the raw triples — used both as the wing
        # fallback for interpolate_delta_pillars (filling 10dc/10dp when the
        # observed delta support stops short) and stored as out["_svi"] for
        # downstream consumers. Skip the fallback for tenors whose fit
        # implies negative risk-neutral density (butterfly_g_min < 0) —
        # propagating that noise would be worse than leaving the pillar None.
        svi_params_by_tenor: dict[str, Any] = {}
        for tenor, obs in pillars_by_tenor.items():
            T = self.tenor_t.get(tenor)
            if T is None:
                continue
            params = _fit_svi_from_triples(obs, forward=F, tenor_years=T)
            if params is not None:
                svi_params_by_tenor[tenor] = params

        await self._publish_progress("vol_surface", "pchip_smile")
        pillar_source_counts: dict[str, int] = {"pchip": 0, "svi_fallback": 0, "none": 0}
        for tenor, obs in pillars_by_tenor.items():
            T = self.tenor_t.get(tenor)
            params = svi_params_by_tenor.get(tenor)
            fallback = (
                _build_svi_fallback(params, forward=F, tenor_years=T, observations=obs)
                if (params is not None and T is not None)
                else None
            )
            pillars = interpolate_delta_pillars(obs, fallback=fallback)
            out[tenor] = {
                label: {
                    "iv": p.iv, "strike": p.strike, "source": p.source,
                }
                for label, p in pillars.items()
            }
            for p in pillars.values():
                pillar_source_counts[p.source] = pillar_source_counts.get(p.source, 0) + 1
        if pillars_by_tenor:
            logger.info(
                "pillar_source_distribution",
                extra={"counts": pillar_source_counts, "n_tenors": len(pillars_by_tenor)},
            )

        # SVI fit per tenor (Phase P2.1) + butterfly arbitrage health.
        # Reuse params already fit from raw triples for the wing fallback.
        try:
            out["_svi"] = _svi_payload_from_params(
                svi_params_by_tenor, forward=F, tenor_years=self.tenor_t,
                observations_by_tenor=pillars_by_tenor,
            )
        except Exception:
            logger.exception("svi_fit_per_tenor_failed")
        # SSVI surface-level fit (Phase P2.2).
        await self._publish_progress("vol_surface", "ssvi_surface")
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
        await self._publish_progress("regime_features", "z_score")
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

            await self._publish_progress("regime_features", "bucket_signal")
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
            await self._publish_progress("regime_features", "joint_pattern")
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

            await self._publish_progress("regime_features", "regime_lookup")
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
        await self._publish_progress("pca_projection", "read_model")
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
                await self._publish_progress("pca_projection", "project")
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

            await self._publish_progress("pca_projection", "gen_z_label")
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
            await self._publish_progress("pca_projection", "coherence")
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
