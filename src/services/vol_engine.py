"""
Vol Engine — Thread 2.
Runs the full vol pipeline: Step 1 (IV mid) + Step 2 (σ_fair).
Own IB connection, loop every 3 minutes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
from arch import arch_model
from ib_insync import IB, Contract
from scipy.interpolate import PchipInterpolator

warnings.filterwarnings("ignore", category=FutureWarning)
logger = logging.getLogger("vol_engine")

# ══════════════════════════════════════════════════════════════════════════════
# Config loader — reads config/vol_config.json at each scan cycle
# ══════════════════════════════════════════════════════════════════════════════

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "vol_config.json"


def _load_vol_config(section: str) -> dict:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get(section, {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Fixed constants
PILLAR_TARGETS = {
    "10dp": 0.10, "25dp": 0.25, "atm": 0.50, "25dc": 0.75, "10dc": 0.90,
}
SUPPRESS_ERRORS = {10090, 10197, 10167, 200, 2119, 2104, 2108, 354}
STARTUP_DELAY_S = 10
TENOR_T = {"1M": 1/12, "2M": 2/12, "3M": 3/12,
           "4M": 4/12, "5M": 5/12, "6M": 6/12}


def _safe(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    return float(val)


def _tenor_label(dte: int) -> str:
    if dte <= 45:
        return "1M"
    if dte <= 75:
        return "2M"
    if dte <= 105:
        return "3M"
    if dte <= 135:
        return "4M"
    if dte <= 165:
        return "5M"
    return "6M"


# ══════════════════════════════════════════════════════════════════════════════
# Scanner row builder
# ══════════════════════════════════════════════════════════════════════════════

def pillars_to_scanner_rows(pillar_rows: list[dict]) -> list[dict]:
    """6 rows — 1 per tenor, ATM only + RR25/BF25."""
    rows = []
    for p in pillar_rows:
        atm = p.get("sigma_ATM_pct")
        if atm is None:
            continue
        rows.append({
            "tenor": p.get("tenor_label", ""),
            "dte": p.get("dte", 0),
            "sigma_mid_pct": round(atm, 2),
            "sigma_fair_pct": round(p["sigma_fair_pct"], 2) if p.get("sigma_fair_pct") is not None else None,
            "ecart_pct": round(p["ecart_pct"], 2) if p.get("ecart_pct") is not None else None,
            "signal": p.get("signal"),
            "RV_pct": round(p["RV_pct"], 2) if p.get("RV_pct") is not None else None,
            "RR25_pct": round(p.get("RR25_pct", 0) or 0, 2) if p.get("RR25_pct") is not None else None,
            "BF25_pct": round(p.get("BF25_pct", 0) or 0, 2) if p.get("BF25_pct") is not None else None,
        })
    return rows


def pillars_to_smile_data(pillar_rows: list[dict]) -> dict[str, dict]:
    """Per-tenor smile data for the smile chart + drill-down table."""
    smiles: dict[str, dict] = {}
    delta_labels = ["10Δp", "25Δp", "ATM", "25Δc", "10Δc"]
    delta_values = [10, 25, 50, 75, 90]
    iv_keys = ["iv_10dp_pct", "iv_25dp_pct", "sigma_ATM_pct", "iv_25dc_pct", "iv_10dc_pct"]
    k_keys = ["strike_10dp", "strike_25dp", "strike_atm", "strike_25dc", "strike_10dc"]

    for p in pillar_rows:
        tenor = p.get("tenor_label", "")
        iv_atm = p.get("sigma_ATM_pct")
        if iv_atm is None:
            continue

        iv_values = []
        strike_values = []
        skew_values = []
        valid = True
        for iv_key, k_key in zip(iv_keys, k_keys, strict=False):
            iv = p.get(iv_key)
            k = p.get(k_key)
            if iv is None or k is None:
                valid = False
                break
            iv_values.append(round(iv, 2))
            strike_values.append(round(k, 5))
            skew_values.append(round(iv - iv_atm, 2))

        if not valid:
            continue

        smiles[tenor] = {
            "deltas": delta_values,
            "delta_labels": delta_labels,
            "iv_market": iv_values,
            "strikes": strike_values,
            "skew": skew_values,
        }
    return smiles


# ══════════════════════════════════════════════════════════════════════════════
# VolEngine thread
# ══════════════════════════════════════════════════════════════════════════════

class VolEngine(threading.Thread):
    def __init__(self, output_queue: queue.Queue,
                 host: str = "127.0.0.1", port: int = 4002, client_id: int = 2) -> None:
        super().__init__(name="VolEngine", daemon=True)
        self._output_queue = output_queue
        self._host = host
        self._port = port
        self._client_id = client_id
        self._stop_event = threading.Event()
        self._risk_engine = None

    def set_risk_engine(self, risk_engine) -> None:
        self._risk_engine = risk_engine

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        asyncio.set_event_loop(asyncio.new_event_loop())
        logger.info("VolEngine thread started")
        if self._stop_event.wait(timeout=STARTUP_DELAY_S):
            return
        while not self._stop_event.is_set():
            cfg1 = _load_vol_config("step1")
            loop_interval = cfg1.get("LOOP_INTERVAL_S", 180)
            try:
                result = self._run_scan()
                self._output_queue.put(result)
                # Push IV surface to risk engine for greeks computation
                if self._risk_engine is not None:
                    pillar_rows = result.get("pillar_rows", [])
                    if pillar_rows:
                        self._risk_engine.iv_surface = {
                            p["tenor_label"]: p for p in pillar_rows
                        }
            except Exception as exc:
                logger.exception("VolEngine scan failed")
                self._output_queue.put({"type": "vol_result", "scanner_rows": [],
                                         "error": str(exc)})
            if self._stop_event.wait(timeout=loop_interval):
                break
        logger.info("VolEngine thread stopped")

    def _run_scan(self) -> dict:
        # Load config at each scan cycle
        cfg1 = _load_vol_config("step1")
        cfg2 = _load_vol_config("step2")

        wait_greeks = cfg1.get("WAIT_GREEKS", 8)
        target_dtes = cfg1.get("TARGET_DTES", [30, 60, 90, 120, 150, 180])
        params = {
            "short": {"n_side": cfg1.get("n_side_short", 20), "min_strikes": 5},
            "long": {"n_side": cfg1.get("n_side_long", 30), "min_strikes": 7},
        }

        ib = IB()
        try:
            ib.connect(self._host, self._port, clientId=self._client_id, timeout=10)
            ib.errorEvent += lambda reqId, code, msg, contract: (
                None if code in SUPPRESS_ERRORS else logger.warning("IB error %d: %s", code, msg))
            ib.reqMarketDataType(3)

            # ── Step 1: IV mid ──
            F, _ = self._get_forward(ib)
            if F is None:
                return self._error("Cannot get forward price")

            selected = self._discover_chains(ib, target_dtes)
            if not selected:
                return self._error("No EUU chains found")

            qualified = self._qualify_contracts(ib, selected, F, params)
            pillar_rows = self._scan_iv(ib, selected, qualified, F, wait_greeks, params)
            if not pillar_rows:
                return self._error("No pillars after IV scan")

            # Step 2 config values
            w1_base = cfg2.get("W1", 0.65)
            signal_threshold = cfg2.get("SIGNAL_THRESHOLD", 0.20)
            alpha_book = cfg2.get("ALPHA_BOOK", 0.20)
            rp_floor = cfg2.get("RP_FLOOR", 0.20)
            vrp_shift = cfg2.get("VRP_SHIFT", 0.50)
            rp_fallback = cfg2.get("RISK_PREMIUM", {})
            w1_ratio_thresh = cfg2.get("W1_RATIO_THRESHOLD", 1.15)
            w1_ratio_sens = cfg2.get("W1_RATIO_SENSITIVITY", 0.10)
            w1_floor = cfg2.get("W1_FLOOR", 0.40)
            garch_blend = cfg2.get("GARCH_EMPIRICAL_BLEND", 0.50)
            emp_kappa = cfg2.get("EMPIRICAL_KAPPA", 2.0)
            garch_duration = cfg2.get("GARCH_DURATION", "1 Y")

            iv_atm_by_tenor = {p["tenor_label"]: p.get("sigma_ATM_pct")
                               for p in pillar_rows}

            # ── Step 2A: Yang-Zhang RV + dynamic RP ──
            rv_map, rv_full = self._compute_rv(
                ib, iv_atm_by_tenor, rp_floor, vrp_shift, rp_fallback, garch_duration)

            # ── Step 2B: GARCH + empirical blend ──
            garch_map = self._compute_garch(
                ib, rv_map, rv_full, garch_blend, emp_kappa, garch_duration)

            # ── Step 2C: Conditional W1 ──
            rv_1m = rv_map.get("1M", {}).get("RV_pct")
            rv_6m = rv_map.get("6M", {}).get("RV_pct")
            if rv_1m and rv_6m and rv_6m > 0:
                rv_ratio = rv_1m / rv_6m
                w1 = max(w1_floor, w1_base - w1_ratio_sens * (rv_ratio - 1.0)) \
                    if rv_ratio > w1_ratio_thresh else w1_base
            else:
                w1 = w1_base
            w2 = 1.0 - w1

            # ── Step 2D: δ_book (placeholder — real δ_book from Thread 3) ──
            book_map = self._compute_book(alpha_book)

            for p in pillar_rows:
                label = p["tenor_label"]
                sigma_mid = p.get("sigma_ATM_pct")
                rv_data = rv_map.get(label, {})
                garch_data = garch_map.get(label, {})
                book_data = book_map.get(label, {})

                anchor = rv_data.get("anchor_pct")
                s_model = garch_data.get("sigma_model_pct")
                db = book_data.get("delta_book_pct", 0.0)

                if anchor is not None and s_model is not None and sigma_mid is not None:
                    sigma_fair = round(w1 * anchor + w2 * s_model + db, 4)
                    ecart = round(sigma_fair - sigma_mid, 4)
                    if ecart > +signal_threshold:
                        signal = "CHEAP"
                    elif ecart < -signal_threshold:
                        signal = "EXPENSIVE"
                    else:
                        signal = "FAIR"
                    p["sigma_fair_pct"] = sigma_fair
                    p["ecart_pct"] = ecart
                    p["signal"] = signal
                    p["RV_pct"] = rv_data.get("RV_pct")

            scanner_rows = pillars_to_scanner_rows(pillar_rows)
            smile_data = pillars_to_smile_data(pillar_rows)

            return {
                "type": "vol_result",
                "timestamp": time.time(),
                "spot": F,
                "pillar_rows": pillar_rows,
                "scanner_rows": scanner_rows,
                "smile_data": smile_data,
                "error": None,
            }
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    # ── Step 1 helpers ──

    @staticmethod
    def _get_forward(ib: IB) -> tuple[float | None, object | None]:
        fut = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
        details = ib.reqContractDetails(fut)
        now = datetime.now()
        futures = []
        for d in details:
            exp = d.contract.lastTradeDateOrContractMonth
            try:
                exp_date = datetime.strptime(exp, "%Y%m%d") if len(exp) == 8 else datetime.strptime(exp, "%Y%m")
            except ValueError:
                continue
            dte = (exp_date - now).days
            if dte >= 7:
                futures.append((dte, d.contract))
        if not futures:
            return None, None
        futures.sort(key=lambda x: x[0])
        front = futures[0][1]
        ticker = ib.reqMktData(front, "", False, False)
        ib.sleep(3)
        bid, ask = _safe(ticker.bid), _safe(ticker.ask)
        F = (bid + ask) / 2.0 if bid and ask else _safe(ticker.close)
        ib.cancelMktData(front)
        ib.sleep(0.5)
        if F and F > 0:
            logger.info("Forward: %s F=%.5f", front.localSymbol, F)
        return F, front

    @staticmethod
    def _discover_chains(ib: IB, target_dtes: list[int] | None = None) -> list[dict]:
        if target_dtes is None:
            target_dtes = [30, 60, 90, 120, 150, 180]
        fut = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
        details = ib.reqContractDetails(fut)
        now = datetime.now()
        futures = []
        for d in details:
            exp = d.contract.lastTradeDateOrContractMonth
            try:
                exp_date = datetime.strptime(exp, "%Y%m%d") if len(exp) == 8 else datetime.strptime(exp, "%Y%m")
            except ValueError:
                continue
            dte = (exp_date - now).days
            if dte >= 7:
                futures.append((dte, d.contract))
        futures.sort(key=lambda x: x[0])

        chain_data: dict[str, dict] = {}
        for _dte, fut_c in futures[:8]:
            chains = ib.reqSecDefOptParams("EUR", "CME", "FUT", fut_c.conId)
            for ch in chains:
                if ch.tradingClass != "EUU":
                    continue
                for exp in sorted(ch.expirations):
                    try:
                        exp_date = datetime.strptime(exp, "%Y%m%d")
                    except ValueError:
                        continue
                    dte_fop = (exp_date - now).days
                    if dte_fop < 10:
                        continue
                    if exp not in chain_data:
                        chain_data[exp] = {"expiry": exp, "dte": dte_fop,
                                           "strikes": set(), "multipliers": set(),
                                           "exchange": ch.exchange}
                    chain_data[exp]["strikes"].update(ch.strikes)
                    chain_data[exp]["multipliers"].add(str(ch.multiplier))

        euu_chains = []
        for data in chain_data.values():
            data["strikes"] = sorted(data["strikes"])
            data["multipliers"] = sorted(data["multipliers"])
            euu_chains.append(data)
        euu_chains.sort(key=lambda x: x["dte"])

        selected = []
        for target in target_dtes:
            best = min(euu_chains, key=lambda x: abs(x["dte"] - target))
            if best not in selected:
                selected.append(best)
        logger.info("%d tenors: %s", len(selected),
                    ", ".join(f"{_tenor_label(ch['dte'])}({ch['expiry']})" for ch in selected))
        return selected

    @staticmethod
    def _qualify_contracts(ib: IB, selected: list[dict], F: float,
                           params: dict | None = None) -> dict:
        if params is None:
            params = {"short": {"n_side": 20}, "long": {"n_side": 30}}
        qualified: dict[str, dict] = {}
        for ch in selected:
            strikes, expiry, dte = ch["strikes"], ch["expiry"], ch["dte"]
            multipliers = ch["multipliers"]
            p = params["short"] if dte <= 45 else params["long"]
            n_side = p["n_side"]
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - F))
            lo = max(0, atm_idx - n_side)
            hi = min(len(strikes) - 1, atm_idx + n_side)
            scan_strikes = strikes[lo:hi + 1]

            qualified[expiry] = {}
            for K in scan_strikes:
                qualified[expiry][K] = {}
                for right in ("C", "P"):
                    for mult in multipliers:
                        fop = Contract(symbol="EUR", secType="FOP", exchange=ch["exchange"],
                                       currency="USD", lastTradeDateOrContractMonth=expiry,
                                       strike=K, right=right, multiplier=mult,
                                       tradingClass="EUU")
                        det = ib.reqContractDetails(fop)
                        if det:
                            qualified[expiry][K][right] = det[0].contract
                            break
            n_ok = sum(1 for k_data in qualified[expiry].values() for _ in k_data.values())
            logger.info("Qualify %s DTE=%d: %d strikes, %d contracts", expiry, dte, len(scan_strikes), n_ok)
        return qualified

    @staticmethod
    def _scan_iv(ib: IB, selected: list[dict], qualified: dict, F: float,
                 wait_greeks: int = 8, params: dict | None = None) -> list[dict]:
        if params is None:
            params = {"short": {"min_strikes": 5}, "long": {"min_strikes": 7}}
        pillar_rows = []
        for ch in selected:
            expiry, dte = ch["expiry"], ch["dte"]
            p = params["short"] if dte <= 45 else params["long"]
            contracts = qualified.get(expiry, {})
            if not contracts:
                continue

            tickers = {}
            for K, rights in contracts.items():
                for right, contract in rights.items():
                    tickers[(K, right)] = (contract, ib.reqMktData(contract, "100", False, False))
            ib.sleep(wait_greeks)

            raw: dict[tuple, dict] = {}
            for (K, right), (contract, ticker) in tickers.items():
                greeks = ticker.modelGreeks
                iv = _safe(greeks.impliedVol) if greeks else None
                delta = _safe(greeks.delta) if greeks else None
                if iv and iv > 0:
                    raw[(K, right)] = {"iv": iv, "delta": delta}
                ib.cancelMktData(contract)
            ib.sleep(0.5)

            iv_by_strike: dict[float, float] = {}
            delta_by_strike: dict[float, float] = {}
            for K in sorted({k for (k, _) in raw}):
                c_data, p_data = raw.get((K, "C")), raw.get((K, "P"))
                iv_c = c_data["iv"] if c_data else None
                iv_p = p_data["iv"] if p_data else None
                d_c = c_data["delta"] if c_data else None
                d_p = p_data["delta"] if p_data else None
                iv_merged = ((iv_c + iv_p) / 2.0 if iv_c and iv_p
                             else iv_c or iv_p)
                if not iv_merged:
                    continue
                delta = d_c if d_c is not None else (1.0 + d_p if d_p is not None else None)
                if delta is not None:
                    iv_by_strike[K] = iv_merged
                    delta_by_strike[K] = delta

            if len(iv_by_strike) < p["min_strikes"]:
                continue

            pairs = sorted([(delta_by_strike[k], iv_by_strike[k], k) for k in iv_by_strike])
            deltas = np.array([t[0] for t in pairs])
            ivs = np.array([t[1] for t in pairs])
            ks = np.array([t[2] for t in pairs])
            mask = np.diff(deltas, prepend=-999) > 1e-6
            deltas, ivs, ks = deltas[mask], ivs[mask], ks[mask]
            if len(deltas) < 3:
                continue

            d_min, d_max = float(deltas[0]), float(deltas[-1])
            interp_iv = PchipInterpolator(deltas, ivs)
            interp_k = PchipInterpolator(deltas, ks)

            def _get(d):
                if d < d_min or d > d_max:
                    return None, None
                try:
                    return float(interp_iv(d)), float(interp_k(d))
                except Exception:
                    return None, None

            iv_atm, k_atm = _get(0.50)
            iv_25dc, k_25dc = _get(0.25)
            iv_25dp, k_25dp = _get(0.75)
            iv_10dc, k_10dc = _get(0.10)
            iv_10dp, k_10dp = _get(0.90)

            # RR25 / BF25
            rr25 = None
            bf25 = None
            if iv_25dc and iv_25dp:
                rr25 = round((iv_25dc - iv_25dp) * 100, 4)
            if iv_25dc and iv_25dp and iv_atm:
                bf25 = round(((iv_25dc + iv_25dp) / 2 - iv_atm) * 100, 4)

            label = _tenor_label(dte)
            pillar_rows.append({
                "tenor_label": label, "expiry": expiry, "dte": dte, "F": round(F, 5),
                "sigma_ATM_pct": round(iv_atm * 100, 4) if iv_atm else None,
                "iv_10dp_pct": round(iv_10dp * 100, 4) if iv_10dp else None,
                "iv_25dp_pct": round(iv_25dp * 100, 4) if iv_25dp else None,
                "iv_25dc_pct": round(iv_25dc * 100, 4) if iv_25dc else None,
                "iv_10dc_pct": round(iv_10dc * 100, 4) if iv_10dc else None,
                "strike_atm": round(k_atm, 5) if k_atm else None,
                "strike_10dp": round(k_10dp, 5) if k_10dp else None,
                "strike_25dp": round(k_25dp, 5) if k_25dp else None,
                "strike_25dc": round(k_25dc, 5) if k_25dc else None,
                "strike_10dc": round(k_10dc, 5) if k_10dc else None,
                "RR25_pct": rr25,
                "BF25_pct": bf25,
            })
            if iv_atm:
                logger.info("%s(%s) ATM=%.2f%% K=%.5f", label, expiry, iv_atm * 100, k_atm)
        return pillar_rows

    # ── Step 2A: Yang-Zhang RV + dynamic RP ──

    @staticmethod
    def _yang_zhang_rv(df_ohlc, window: int) -> float | None:
        dw = df_ohlc.tail(window).copy()
        n = len(dw)
        if n < 3:
            return None
        o = np.log(dw["open"].values)
        h = np.log(dw["high"].values)
        lo = np.log(dw["low"].values)
        c = np.log(dw["close"].values)
        overnight = o[1:] - c[:-1]
        oc = c[1:] - o[1:]
        rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (lo[1:] - c[1:]) * (lo[1:] - o[1:])
        s2_on = np.var(overnight, ddof=1)
        s2_oc = np.var(oc, ddof=1)
        s2_rs = np.mean(rs)
        k_yz = 0.34 / (1.34 + (n + 1) / (n - 1))
        s2_yz = s2_on + k_yz * s2_oc + (1 - k_yz) * s2_rs
        return float(np.sqrt(max(s2_yz, 0) * 252) * 100)

    @staticmethod
    def _fetch_ohlc(ib: IB, duration: str = "1 Y"):
        import pandas as pd
        fut_cont = Contract(symbol="EUR", secType="CONTFUT", exchange="CME", currency="USD")
        bars = ib.reqHistoricalData(
            fut_cont, endDateTime="", durationStr=duration,
            barSizeSetting="1 day", whatToShow="ADJUSTED_LAST",
            useRTH=True, formatDate=1,
        )
        if not bars:
            return None
        df = pd.DataFrame([{"date": b.date, "open": b.open, "high": b.high,
                            "low": b.low, "close": b.close} for b in bars])
        return df.sort_values("date").reset_index(drop=True)

    def _compute_rv(self, ib: IB, iv_atm_by_tenor: dict,
                    rp_floor: float = 0.20, vrp_shift: float = 0.50,
                    rp_fallback: dict | None = None,
                    garch_duration: str = "1 Y") -> tuple[dict[str, dict], float | None]:
        if rp_fallback is None:
            rp_fallback = {}
        df_ohlc = self._fetch_ohlc(ib, garch_duration)
        if df_ohlc is None or len(df_ohlc) < 5:
            logger.warning("No historical data for RV")
            return {}, None

        rv_full = self._yang_zhang_rv(df_ohlc, len(df_ohlc) - 1)

        rv_map = {}
        for label, T in TENOR_T.items():
            window = max(21, int(T * 252))
            window = min(window, len(df_ohlc) - 1)
            rv = self._yang_zhang_rv(df_ohlc, window)
            if rv is None:
                continue

            # Dynamic RP: based on observed VRP (IV - RV)
            iv_atm = iv_atm_by_tenor.get(label)
            if iv_atm is not None:
                vrp_spot = iv_atm - rv
                rp = max(rp_floor, vrp_spot + vrp_shift)
            else:
                rp = rp_fallback.get(label, 1.50)

            rv_map[label] = {
                "RV_pct": round(rv, 4), "RP_pct": round(rp, 4),
                "anchor_pct": round(rv + rp, 4),
            }

        logger.info("RV: %s", ", ".join(f"{k}={v['RV_pct']:.2f}%" for k, v in rv_map.items()))
        return rv_map, rv_full

    # ── Step 2B: GARCH(1,1) + empirical blend ──

    def _compute_garch(self, ib: IB, rv_map: dict, rv_full: float | None,
                       garch_blend: float = 0.50, emp_kappa: float = 2.0,
                       garch_duration: str = "1 Y") -> dict[str, dict]:
        df_ohlc = self._fetch_ohlc(ib, garch_duration)
        if df_ohlc is None or len(df_ohlc) < 5:
            logger.warning("No historical data for GARCH")
            return {}

        closes = df_ohlc["close"].values
        returns = np.diff(np.log(closes)) * 100

        try:
            fit = arch_model(returns, vol="Garch", p=1, q=1,
                             mean="Constant", dist="normal").fit(disp="off")
        except Exception as exc:
            logger.warning("GARCH fit failed: %s", exc)
            return {}

        omega = fit.params["omega"]
        alpha = fit.params["alpha[1]"]
        beta = fit.params["beta[1]"]
        persistence = min(alpha + beta, 0.9999)
        kappa = -np.log(persistence)

        cond_vol = fit.conditional_volatility
        cond_var = (cond_vol[-1] if hasattr(cond_vol, "__getitem__") else float(cond_vol)) ** 2
        var_c = (np.sqrt(cond_var * 252) / 100) ** 2
        var_lr = (np.sqrt(omega / (1 - persistence) * 252) / 100) ** 2

        garch_map = {}
        for label, T in TENOR_T.items():
            # GARCH forward projection
            var_T = var_lr + (var_c - var_lr) * np.exp(-kappa * T)
            vol_garch = float(np.sqrt(max(var_T, 0)) * 100)

            # Empirical mean-reversion: RV(tenor) → RV_full at speed EMPIRICAL_KAPPA
            rv_tenor = rv_map.get(label, {}).get("RV_pct")
            if rv_tenor is not None and rv_full is not None:
                vol_empirical = rv_full + (rv_tenor - rv_full) * np.exp(-emp_kappa * T)
            else:
                vol_empirical = vol_garch

            # Blend
            vol_model = garch_blend * vol_garch + (1 - garch_blend) * vol_empirical

            garch_map[label] = {"sigma_model_pct": round(vol_model, 4)}

        logger.info("GARCH: a=%.4f b=%.4f persist=%.4f", alpha, beta, persistence)
        return garch_map

    # ── Step 2C: δ_book ──

    @staticmethod
    def _compute_book(alpha_book: float = 0.20) -> dict[str, dict]:
        """Return empty δ_book — positions are handled by PositionsEngine (Thread 3)."""
        return {label: {"delta_book_pct": 0.0} for label in TENOR_T}

    @staticmethod
    def _error(msg: str) -> dict:
        return {"type": "vol_result", "scanner_rows": [], "pillar_rows": [], "error": msg}
