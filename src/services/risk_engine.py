"""
Risk Engine — Thread 3.
Fetches positions from IB (10s), computes BS greeks + PnL chart (2s).
Own IB connection (client_id=3).
"""
from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
from datetime import datetime
from typing import Any

import numpy as np
from ib_insync import IB
from redis import asyncio as aioredis
from redis import exceptions as redis_exc
from scipy.stats import norm

from bus import keys
from bus.publisher import publish_risk_update, set_heartbeat
from services.bs_pricer import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vega,
    interpolate_iv,
)

logger = logging.getLogger("risk_engine")

_REDIS_SWALLOW: tuple[type[BaseException], ...] = (
    redis_exc.ConnectionError,
    redis_exc.TimeoutError,
    ConnectionError,
    TimeoutError,
    OSError,
)

SUPPRESS_ERRORS = {10090, 10197, 10167, 200, 2119, 2104, 2108, 354}
GREEKS_INTERVAL_S = 2.0
POSITIONS_INTERVAL_S = 10.0
PNL_CHART_POINTS = 31
PNL_CHART_RANGE_PCT = 0.03
FALLBACK_IV = 0.07
GAMMA_PIP_SCALE = 0.0001
FUT_MULTIPLIER_DEFAULT = 125_000
STARTUP_DELAY_S = 15


class RiskEngine(threading.Thread):
    """Thread 3: fetches IB positions periodically and computes BS greeks + PnL."""

    def __init__(
        self,
        output_queue: queue.Queue[dict[str, Any]],
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 3,
        redis_url: str | None = None,
    ) -> None:
        """Initialize the risk engine.

        Args:
            output_queue: Queue to post computed greeks/PnL payloads for the UI thread.
            host: IB Gateway hostname.
            port: IB Gateway port.
            client_id: IB client ID for the dedicated risk connection.
            redis_url: Optional Redis URL. When set, each cycle publishes
                greeks + pnl curve to the bus and refreshes heartbeat:risk_engine.
        """
        super().__init__(name="RiskEngine", daemon=True)
        self._output_queue = output_queue
        self._host = host
        self._port = port
        self._client_id = client_id
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()

        # Shared inputs (written by other threads, read here)
        self.spot: float = 0.0
        self.iv_surface: dict[str, Any] = {}

        # Internal state
        self._positions: list[dict[str, Any]] = []

        # Redis bus (R3 PR #5) — initialized inside run() on this thread's loop.
        self._redis_url = redis_url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._redis_client: aioredis.Redis | None = None

    def stop(self) -> None:
        """Signal the engine thread to stop."""
        self._stop_event.set()

    def request_refresh(self) -> None:
        """Wake the engine to re-fetch positions immediately."""
        self._refresh_event.set()

    def run(self) -> None:
        """Main loop: fetch positions and compute greeks/PnL on a fixed cadence."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._init_redis_bus_if_configured()
        logger.info("RiskEngine thread started")
        if self._stop_event.wait(timeout=STARTUP_DELAY_S):
            self._teardown_redis_bus()
            return
        last_fetch = 0.0
        while not self._stop_event.is_set():
            try:
                now = __import__("time").monotonic()

                # Fetch positions from IB every POSITIONS_INTERVAL_S or on refresh
                if now - last_fetch >= POSITIONS_INTERVAL_S or self._refresh_event.is_set():
                    self._refresh_event.clear()
                    self._fetch_positions()
                    last_fetch = now

                # Compute greeks + PnL (or static view if market closed)
                F = self.spot
                result: dict[str, Any] | None = None
                if self._positions and F > 0:
                    result = self._compute(F)
                    self._output_queue.put(result)
                elif self._positions:
                    result = self._static_positions()
                    self._output_queue.put(result)

                # Redis bus — heartbeat every cycle, risk update when we
                # actually have a result to publish.
                if result is not None:
                    self._publish_risk_to_redis(result)
                self._set_heartbeat_to_redis()

            except Exception as exc:
                logger.exception("RiskEngine cycle failed")
                self._output_queue.put({"error": str(exc)})

            # Sleep until next cycle, but wake early on refresh or stop
            for _ in range(int(GREEKS_INTERVAL_S)):
                if self._stop_event.is_set():
                    break
                if self._refresh_event.is_set():
                    self._refresh_event.clear()
                    break
                self._stop_event.wait(timeout=1)
            if self._stop_event.is_set():
                break
        self._teardown_redis_bus()
        logger.info("RiskEngine thread stopped")

    # ── Redis bus wiring (R3 PR #5) ────────────────────────────────────────

    def _init_redis_bus_if_configured(self) -> None:
        if not self._redis_url:
            return
        try:
            pool = aioredis.ConnectionPool.from_url(
                self._redis_url, max_connections=10, decode_responses=True
            )
            self._redis_client = aioredis.Redis(connection_pool=pool)
            logger.info("RiskEngine connected to Redis at %s", self._redis_url)
        except Exception:
            logger.exception("RiskEngine Redis init failed, bus disabled")
            self._redis_client = None

    def _teardown_redis_bus(self) -> None:
        if self._loop is None:
            return
        try:
            if self._redis_client is not None:
                self._loop.run_until_complete(self._redis_client.aclose())
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        except Exception:
            logger.exception("RiskEngine Redis teardown error")
        finally:
            self._redis_client = None

    def _publish_risk_to_redis(self, result: dict[str, Any]) -> None:
        if self._redis_client is None or self._loop is None:
            return
        summary = result.get("summary") or {}
        if not summary:
            return
        pnl_curve = result.get("pnl_curve") or None
        try:
            self._loop.run_until_complete(
                publish_risk_update(
                    self._redis_client, greeks=dict(summary), pnl_curve=pnl_curve
                )
            )
        except _REDIS_SWALLOW as e:
            logger.warning("redis publish_risk_update failed (transient): %s", e)
        except Exception:
            logger.exception("redis publish_risk_update unexpected error")

    def _set_heartbeat_to_redis(self) -> None:
        if self._redis_client is None or self._loop is None:
            return
        try:
            self._loop.run_until_complete(
                set_heartbeat(self._redis_client, keys.ENGINE_RISK)
            )
        except _REDIS_SWALLOW as e:
            logger.warning("redis heartbeat (risk) failed (transient): %s", e)
        except Exception:
            logger.exception("redis heartbeat (risk) unexpected error")

    def _static_positions(self) -> dict[str, Any]:
        """Return positions with basic data only (no greeks, no PnL). Used when market is closed."""
        open_rows = []
        for pos in self._positions:
            abs_qty = pos.get("abs_qty", abs(pos.get("qty", 0)))
            strike = pos.get("strike", 0)
            right = pos.get("right", "")
            tenor = pos.get("tenor", "—")
            sec_type = pos.get("sec_type", "")
            open_rows.append({
                "symbol": pos.get("symbol", ""),
                "side": pos.get("side", ""),
                "qty": int(abs_qty),
                "tenor": tenor if sec_type == "FOP" else "—",
                "strike": f"{strike:.5f}" if strike and sec_type == "FOP" else "—",
                "right": right if sec_type == "FOP" else sec_type,
                "fill_price": pos.get("cost_per_unit", 0),
                "iv_now_pct": None,
                "delta": None, "vega": None, "gamma": None,
                "theta": None, "pnl": None,
                "sec_type": sec_type, "expiry": pos.get("expiry", ""),
            })
        return {
            "open_positions": open_rows,
            "summary": {},
            "pnl_curve": None,
            "spot": 0,
        }

    # ── Position fetch (IB blocking, ~2.5s) ──

    def _fetch_positions(self) -> None:
        """Connect to IB, fetch all open positions, and update internal state."""
        ib = IB()
        try:
            ib.connect(self._host, self._port, clientId=self._client_id, timeout=10)
            ib.errorEvent += lambda reqId, code, msg, contract: None
            positions = ib.reqPositions()
            ib.sleep(2)

            now = datetime.now()
            pos_list = []
            for pos in [p for p in positions if p.position != 0]:
                c = pos.contract
                qty = float(pos.position)
                avg_cost = float(pos.avgCost)
                multiplier = float(c.multiplier or 125000)

                tenor = "—"
                T = 0.0
                if c.lastTradeDateOrContractMonth:
                    try:
                        exp_date = datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d")
                        dte = (exp_date - now).days
                        T = max(dte / 365.0, 0.001)
                        if dte <= 45:
                            tenor = "1M"
                        elif dte <= 75:
                            tenor = "2M"
                        elif dte <= 105:
                            tenor = "3M"
                        elif dte <= 135:
                            tenor = "4M"
                        elif dte <= 165:
                            tenor = "5M"
                        else:
                            tenor = "6M"
                    except ValueError:
                        pass

                pos_list.append({
                    "symbol": c.localSymbol or f"{c.symbol} {c.secType}",
                    "sec_type": c.secType,
                    "side": "BUY" if qty > 0 else "SELL",
                    "qty": qty,
                    "abs_qty": abs(qty),
                    "strike": float(c.strike) if c.strike else 0.0,
                    "right": c.right if c.right else "",
                    "tenor": tenor,
                    "T": T,
                    "multiplier": multiplier,
                    "avg_cost": avg_cost,
                    "cost_per_unit": avg_cost / multiplier if multiplier > 0 else avg_cost,
                    "expiry": c.lastTradeDateOrContractMonth or "",
                    "contract": c,
                })

            # Fetch mark prices for all positions
            tickers = {}
            for p_data in pos_list:
                c = p_data["contract"]
                c.exchange = "CME"
                tk = ib.reqMktData(c, "", False, False)
                tickers[p_data["symbol"]] = tk
            if tickers:
                ib.sleep(3)
            for p_data in pos_list:
                tk = tickers.get(p_data["symbol"])
                if tk is None:
                    p_data["mark_price"] = None
                    continue
                bid = getattr(tk, "bid", None)
                ask = getattr(tk, "ask", None)
                valid_bid = bid is not None and isinstance(bid, (int, float)) and not math.isnan(bid) and bid > 0
                valid_ask = ask is not None and isinstance(ask, (int, float)) and not math.isnan(ask) and ask > 0
                if valid_bid and valid_ask:
                    p_data["mark_price"] = (bid + ask) / 2.0
                elif valid_bid:
                    p_data["mark_price"] = bid
                elif valid_ask:
                    p_data["mark_price"] = ask
                else:
                    p_data["mark_price"] = None
                ib.cancelMktData(c)

            # Remove contract objects (not serializable, not needed downstream)
            for p_data in pos_list:
                del p_data["contract"]

            self._positions = pos_list
            logger.info("%d open positions fetched", len(pos_list))
        except Exception as exc:
            logger.warning(f"RiskEngine fetch failed: {exc}")
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    # ── Greeks + PnL computation ──

    def _compute(self, F: float) -> dict[str, Any]:
        """Compute per-position BS greeks, aggregate summary, and PnL curve."""
        iv_surface = self.iv_surface  # atomic dict read
        open_rows = []
        bs_pnl_at_spot = 0.0  # for chart anchoring (#2)
        fallback_iv_count = 0  # (#3)
        net_premium_paid = 0.0  # (#8)

        for pos in self._positions:
            sec_type = pos.get("sec_type", "")
            qty = pos.get("qty", 0)
            abs_qty = pos.get("abs_qty", abs(qty))
            strike = pos.get("strike", 0)
            right = pos.get("right", "")
            tenor = pos.get("tenor", "—")
            T = pos.get("T", 0)
            multiplier = pos.get("multiplier", 125000)
            cost_per_unit = pos.get("cost_per_unit", 0)
            mark = pos.get("mark_price")
            dte = round(T * 365) if T > 0 else None  # (#6)

            if sec_type == "FUT":
                price_for_pnl = mark if mark is not None else F
                delta_usd = qty * F * multiplier
                pnl = (price_for_pnl - cost_per_unit) * qty * multiplier
                bs_pnl_at_spot += (F - cost_per_unit) * qty * multiplier
                open_rows.append({
                    "symbol": pos.get("symbol", ""), "side": pos.get("side", ""),
                    "qty": int(abs_qty), "tenor": "—", "dte": dte,
                    "strike": f"F={F:.5f}", "right": "FUT",
                    "fill_price": cost_per_unit, "mark_price": mark,
                    "iv_now_pct": None,
                    "delta": round(delta_usd, 2), "vega": None,
                    "gamma": None, "theta": None, "pnl": round(pnl, 2),
                    "break_even": None, "using_fallback_iv": False,
                    "sec_type": "FUT", "expiry": pos.get("expiry", ""),
                })

            elif sec_type == "FOP" and right in ("C", "P") and T > 0 and strike > 0:
                iv_raw = interpolate_iv(iv_surface, tenor, strike, F)
                using_fallback = iv_raw is None or iv_raw <= 0
                iv = FALLBACK_IV if using_fallback else iv_raw
                if using_fallback:
                    fallback_iv_count += 1

                d = bs_delta(F, strike, T, iv, right)
                g = bs_gamma(F, strike, T, iv)
                v = bs_vega(F, strike, T, iv)
                th = bs_theta(F, strike, T, iv, right)
                bs_px = bs_price(F, strike, T, iv, right)

                delta_usd = d * qty * multiplier
                gamma_usd = g * qty * multiplier * GAMMA_PIP_SCALE
                vega_usd = v * qty * multiplier
                theta_usd = th * qty * multiplier
                price_for_pnl = mark if mark is not None else bs_px
                pnl = (price_for_pnl - cost_per_unit) * qty * multiplier
                bs_pnl_at_spot += (bs_px - cost_per_unit) * qty * multiplier
                net_premium_paid += cost_per_unit * abs_qty * multiplier

                # Break-even (#7)
                be = (strike + cost_per_unit) if right == "C" else (strike - cost_per_unit)

                open_rows.append({
                    "symbol": pos.get("symbol", ""), "side": pos.get("side", ""),
                    "qty": int(abs_qty), "tenor": tenor, "dte": dte,
                    "strike": f"{strike:.5f}", "right": right,
                    "fill_price": cost_per_unit, "mark_price": mark,
                    "iv_now_pct": round(iv * 100, 2),
                    "delta": round(delta_usd, 2), "vega": round(vega_usd, 2),
                    "gamma": round(gamma_usd, 2), "theta": round(theta_usd, 2),
                    "pnl": round(pnl, 2),
                    "break_even": round(be, 5), "using_fallback_iv": using_fallback,
                    "sec_type": "FOP", "expiry": pos.get("expiry", ""),
                })
            else:
                open_rows.append({
                    "symbol": pos.get("symbol", ""), "side": pos.get("side", ""),
                    "qty": int(abs_qty), "tenor": tenor, "dte": dte,
                    "strike": str(strike) if strike else "—", "right": right or "—",
                    "fill_price": cost_per_unit, "mark_price": None,
                    "iv_now_pct": None,
                    "delta": None, "vega": None, "gamma": None,
                    "theta": None, "pnl": None,
                    "break_even": None, "using_fallback_iv": False,
                    "sec_type": sec_type, "expiry": pos.get("expiry", ""),
                })

        # Summary (#8, #9, #10)
        pnl_total = round(sum(r.get("pnl") or 0 for r in open_rows), 2)
        vega_net = round(sum(r.get("vega") or 0 for r in open_rows), 2)
        theta_net = round(sum(r.get("theta") or 0 for r in open_rows), 2)
        vega_theta_ratio = round(vega_net / abs(theta_net)) if theta_net != 0 else None
        pnl_pct_premium = round(pnl_total / net_premium_paid * 100, 1) if net_premium_paid != 0 else None

        summary = {
            "delta_net": round(sum(r.get("delta") or 0 for r in open_rows), 2),
            "vega_net": vega_net,
            "gamma_net": round(sum(r.get("gamma") or 0 for r in open_rows), 2),
            "theta_net": theta_net,
            "pnl_total": pnl_total,
            "net_premium_paid": round(net_premium_paid, 2),
            "vega_theta_ratio": vega_theta_ratio,
            "pnl_pct_premium": pnl_pct_premium,
            "fallback_iv_count": fallback_iv_count,
        }

        # PnL chart with mark-anchor shift (#2)
        mark_pnl_total = pnl_total
        pnl_shift = mark_pnl_total - bs_pnl_at_spot
        pnl_curve = self._compute_pnl_chart(F, iv_surface, pnl_shift=pnl_shift)

        return {
            "open_positions": open_rows,
            "summary": summary,
            "pnl_curve": pnl_curve,
            "spot": F,
        }

    # ── PnL chart (vectorized where possible) ──

    def _compute_pnl_chart(self, F: float, iv_surface: dict[str, Any],
                           pnl_shift: float = 0.0) -> dict[str, Any]:
        """Build PnL curves over a spot range for the current portfolio."""
        lo = F * (1 - PNL_CHART_RANGE_PCT)
        hi = F * (1 + PNL_CHART_RANGE_PCT)
        spots = np.linspace(lo, hi, PNL_CHART_POINTS)
        pnls = np.zeros(PNL_CHART_POINTS)
        pnls_expiry = np.zeros(PNL_CHART_POINTS)  # (#11)

        for pos in self._positions:
            sec_type = pos.get("sec_type", "")
            qty = pos.get("qty", 0)
            strike = pos.get("strike", 0)
            right = pos.get("right", "")
            T = pos.get("T", 0)
            tenor = pos.get("tenor", "—")
            multiplier = pos.get("multiplier", 125000)
            cost_per_unit = pos.get("cost_per_unit", 0)

            if sec_type == "FUT":
                pnls += (spots - cost_per_unit) * qty * multiplier
                pnls_expiry += (spots - cost_per_unit) * qty * multiplier

            elif sec_type == "FOP" and right in ("C", "P") and T > 0 and strike > 0:
                iv = interpolate_iv(iv_surface, tenor, strike, F)
                if iv is None or iv <= 0:
                    iv = FALLBACK_IV
                prices = self._bs_price_vec(spots, strike, T, iv, right)
                pnls += (prices - cost_per_unit) * qty * multiplier

                # Expiry payoff (#11)
                if right == "C":
                    intrinsic = np.maximum(spots - strike, 0)
                else:
                    intrinsic = np.maximum(strike - spots, 0)
                pnls_expiry += (intrinsic - cost_per_unit) * qty * multiplier

        # Anchor to mark PnL (#2)
        pnls += pnl_shift
        pnls_expiry += pnl_shift

        # Break-even spots from expiry payoff (#12)
        break_evens = []
        for i in range(len(pnls_expiry) - 1):
            if pnls_expiry[i] * pnls_expiry[i + 1] < 0:
                # Linear interpolation for zero-crossing
                x0, x1, y0, y1 = spots[i], spots[i + 1], pnls_expiry[i], pnls_expiry[i + 1]
                be = x0 - y0 * (x1 - x0) / (y1 - y0)
                break_evens.append(round(float(be), 5))

        return {
            "spots": spots.tolist(), "pnls": pnls.tolist(), "spot": F,
            "pnls_expiry": pnls_expiry.tolist(),
            "break_evens": break_evens,
        }

    @staticmethod
    def _bs_price_vec(F_arr: np.ndarray, K: float, T: float,
                      sigma: float, right: str) -> np.ndarray:
        """Vectorized BS price over an array of forwards."""
        if sigma <= 0 or T <= 0 or K <= 0:
            return np.zeros_like(F_arr)
        sqrt_T = math.sqrt(T)
        log_FK = np.log(F_arr / K)
        d1 = (log_FK + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        if right == "C":
            return F_arr * norm.cdf(d1) - K * norm.cdf(d2)
        return K * norm.cdf(-d2) - F_arr * norm.cdf(-d1)
