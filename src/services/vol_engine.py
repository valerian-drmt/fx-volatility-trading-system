"""
Vol Engine — Thread 2.
Consumes raw option chain data from input queue,
runs CPU-bound Step 1 calculations (BS inversion, delta pillars),
sends results to output queue.

Contains both the thread class and the pure math functions.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

MIN_VOLUME = 0  # relaxed for delayed/paper data (no live volume)
MAX_BA_SPREAD_PCT = 0.20

TARGET_DELTAS = {
    "10dp": -0.10,
    "25dp": -0.25,
    "atm":   0.00,
    "25dc": +0.25,
    "10dc": +0.10,
}

TENOR_LABELS = {
    "20250321": "3M",
    "20250620": "6M",
    "20250919": "9M",
    "20251219": "1Y",
}

# ══════════════════════════════════════════════════════════════════════════════
# Pure math functions (no IB dependency, thread-safe)
# ══════════════════════════════════════════════════════════════════════════════


def bs_delta(s: float, k: float, t: float, sigma: float, right: str) -> float:
    """Delta Black-Scholes (zero rates, simplified for FX futures)."""
    if sigma <= 0 or t <= 0:
        return np.nan
    d1 = (np.log(s / k) + 0.5 * sigma ** 2 * t) / (sigma * np.sqrt(t))
    phi = 1 if right == "C" else -1
    return float(phi * norm.cdf(phi * d1))


def strike_to_delta(s: float, k: float, t: float, sigma: float) -> float:
    """Call delta (positive) for a given strike."""
    return bs_delta(s, k, t, sigma, "C")


def filter_liquid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep rows with:
    - iv_raw > 0 and not NaN
    - bid > 0 and ask > 0
    - bid-ask spread < MAX_BA_SPREAD_PCT * ask
    - volume >= MIN_VOLUME
    """
    df = df.copy()
    has_iv = df["iv_raw"].notna() & (df["iv_raw"] > 0)
    has_ba = df["bid"].notna() & df["ask"].notna() & (df["bid"] > 0) & (df["ask"] > 0)
    ba_pct = (df["ask"] - df["bid"]) / df["ask"].replace(0, np.nan)
    tight_ba = ba_pct < MAX_BA_SPREAD_PCT
    liq_vol = df["volume"] >= MIN_VOLUME

    df["ba_spread_pct"] = ba_pct.round(4)
    df["liquid"] = has_iv & has_ba & tight_ba & liq_vol
    return df


def compute_mid_iv(df: pd.DataFrame) -> pd.DataFrame:
    """Average call and put IV at each strike to get sigma_mid."""
    liq = df[df["liquid"]].copy()

    calls = liq[liq["right"] == "C"][
        ["expiry", "tenor", "T", "strike", "moneyness", "iv_raw", "delta_ib"]
    ].rename(columns={"iv_raw": "iv_call", "delta_ib": "delta_call"})

    puts = liq[liq["right"] == "P"][
        ["expiry", "strike", "iv_raw", "delta_ib"]
    ].rename(columns={"iv_raw": "iv_put", "delta_ib": "delta_put"})

    merged = calls.merge(puts, on=["expiry", "strike"], how="inner")
    merged["iv_mid"] = ((merged["iv_call"] + merged["iv_put"]) / 2).round(5)
    merged["iv_spread"] = (merged["iv_call"] - merged["iv_put"]).abs().round(5)
    return merged.sort_values(["expiry", "strike"]).reset_index(drop=True)


def reconstruct_pillars(df_mid: pd.DataFrame, spot: float) -> pd.DataFrame:
    """
    For each tenor, classify strikes into standard delta buckets
    and derive RR25/BF25/RR10/BF10.
    """
    rows = []
    for expiry, grp in df_mid.groupby("expiry"):
        t = grp["T"].iloc[0]
        tenor = grp["tenor"].iloc[0]

        grp = grp.copy()
        grp["delta_bs"] = grp.apply(
            lambda r: strike_to_delta(spot, r["strike"], t, r["iv_mid"]), axis=1
        )

        pillar_row: dict = {"expiry": expiry, "tenor": tenor, "T": t, "spot": spot}
        for label, target_d in TARGET_DELTAS.items():
            if label == "atm":
                idx = (grp["strike"] - spot).abs().idxmin()
            else:
                idx = (grp["delta_bs"] - target_d).abs().idxmin()

            row_found = grp.loc[idx]
            pillar_row[f"K_{label}"] = row_found["strike"]
            pillar_row[f"iv_{label}"] = row_found["iv_mid"]
            pillar_row[f"d_{label}"] = round(row_found["delta_bs"], 4)

        pillar_row["RR25"] = round(pillar_row["iv_25dc"] - pillar_row["iv_25dp"], 5)
        pillar_row["BF25"] = round(
            0.5 * (pillar_row["iv_25dc"] + pillar_row["iv_25dp"]) - pillar_row["iv_atm"], 5
        )
        pillar_row["RR10"] = round(pillar_row["iv_10dc"] - pillar_row["iv_10dp"], 5)
        pillar_row["BF10"] = round(
            0.5 * (pillar_row["iv_10dc"] + pillar_row["iv_10dp"]) - pillar_row["iv_atm"], 5
        )
        rows.append(pillar_row)

    return pd.DataFrame(rows)


def build_output_table(pillars: pd.DataFrame) -> pd.DataFrame:
    """Format pillars with vols in % and standard column names."""
    out = pillars.copy()
    vol_cols = ["iv_10dp", "iv_25dp", "iv_atm", "iv_25dc", "iv_10dc", "RR25", "BF25", "RR10", "BF10"]
    for c in vol_cols:
        if c in out.columns:
            out[c] = (out[c] * 100).round(3)

    display_cols = [
        "tenor", "spot",
        "iv_atm", "RR25", "BF25", "RR10", "BF10",
        "iv_10dp", "iv_25dp", "iv_25dc", "iv_10dc",
        "K_10dp", "K_25dp", "K_atm", "K_25dc", "K_10dc",
    ]
    display_cols = [c for c in display_cols if c in out.columns]
    return out[display_cols].rename(columns={
        "iv_atm": "σ_ATM%",
        "iv_10dp": "σ_10Δp%",
        "iv_25dp": "σ_25Δp%",
        "iv_25dc": "σ_25Δc%",
        "iv_10dc": "σ_10Δc%",
        "RR25": "RR25%",
        "BF25": "BF25%",
        "RR10": "RR10%",
        "BF10": "BF10%",
    })


def pillars_to_scanner_rows(pillars: pd.DataFrame, spot: float) -> list[dict]:
    """Convert pillars DF to flat list of dicts for the scanner panel."""
    rows = []
    for _, row in pillars.iterrows():
        tenor = row.get("tenor", "")
        for label in TARGET_DELTAS:
            iv_key = f"iv_{label}"
            k_key = f"K_{label}"
            d_key = f"d_{label}"
            if iv_key not in row or k_key not in row:
                continue
            delta_label = label.upper().replace("DP", "Δp").replace("DC", "Δc")
            if label == "atm":
                delta_label = "ATM"
            rows.append({
                "tenor": tenor,
                "delta_label": delta_label,
                "strike": round(float(row[k_key]), 5),
                "iv_market_pct": round(float(row[iv_key]) * 100, 2),
                "delta_bs": round(float(row.get(d_key, 0)), 4),
            })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# VolEngine thread class
# ══════════════════════════════════════════════════════════════════════════════


class VolEngine(threading.Thread):
    def __init__(
        self,
        input_queue: queue.Queue,
        output_queue: queue.Queue,
    ) -> None:
        super().__init__(name="VolEngine", daemon=True)
        self._input_queue = input_queue
        self._output_queue = output_queue
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("VolEngine thread started")
        while not self._stop_event.is_set():
            try:
                msg = self._input_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                result = self._process(msg)
                self._output_queue.put(result)
            except Exception as exc:
                self._output_queue.put({
                    "type": "step1_result",
                    "timestamp": time.time(),
                    "spot": msg.get("spot", 0),
                    "pillars": [],
                    "scanner_rows": [],
                    "error": str(exc),
                })
        logger.info("VolEngine thread stopped")

    def _process(self, msg: dict) -> dict:
        spot = float(msg["spot"])
        chains = msg.get("chains", {})

        all_rows = []
        for expiry, chain in chains.items():
            tenor = chain.get("tenor", expiry)
            t = float(chain.get("T", 0.25))
            for row in chain.get("rows", []):
                moneyness = np.log(float(row["strike"]) / spot) if spot > 0 else 0.0
                all_rows.append({
                    "expiry": expiry,
                    "tenor": tenor,
                    "T": t,
                    "strike": float(row["strike"]),
                    "right": row["right"],
                    "moneyness": round(moneyness, 5),
                    "iv_raw": float(row.get("iv_raw", 0)) if row.get("iv_raw") else np.nan,
                    "bid": float(row.get("bid", 0)) if row.get("bid") else np.nan,
                    "ask": float(row.get("ask", 0)) if row.get("ask") else np.nan,
                    "volume": int(row.get("volume", 0)),
                    "delta_ib": row.get("delta_ib"),
                })

        if not all_rows:
            return {
                "type": "step1_result",
                "timestamp": time.time(),
                "spot": spot,
                "pillars": [],
                "scanner_rows": [],
                "error": "No chain data received",
            }

        df_raw = pd.DataFrame(all_rows)
        df_filtered = filter_liquid(df_raw)
        df_mid = compute_mid_iv(df_filtered)

        if df_mid.empty:
            return {
                "type": "step1_result",
                "timestamp": time.time(),
                "spot": spot,
                "pillars": [],
                "scanner_rows": [],
                "error": "No liquid strikes after filtering",
            }

        pillars = reconstruct_pillars(df_mid, spot)
        scanner_rows = pillars_to_scanner_rows(pillars, spot)

        return {
            "type": "step1_result",
            "timestamp": time.time(),
            "spot": spot,
            "pillars": pillars.to_dict("records"),
            "scanner_rows": scanner_rows,
            "error": None,
        }
