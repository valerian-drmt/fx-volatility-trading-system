"""
vol_mid_step1.py — scan C+P, merge IV, PCHIP on call-delta axis
6 tenors × strikes adaptatifs | output: 3 DataFrames (vol_mid, diagnostics, arb_flags)
"""
import math, csv, time
import pandas as pd
from datetime import datetime
from scipy.interpolate import PchipInterpolator
import numpy as np
from ib_insync import IB, Contract, util

util.patchAsyncio()

# ── Config ──
PORT, CLIENT_ID = 4002, 14
TARGET_DTES     = [30, 60, 90, 120, 150, 180]
WAIT_GREEKS     = 8
IV_ARB_THRESHOLD = 0.005

PARAMS = {
    "short": {"n_side": 20, "rr25_max": 10.0, "bf25_min": -6.0, "min_strikes": 5},
    "long":  {"n_side": 30, "rr25_max":  6.0, "bf25_min": -4.0, "min_strikes": 7},
}

def safe(val):
    return val if val is not None and not (isinstance(val, float) and math.isnan(val)) else None

def get_params(dte):
    return PARAMS["short"] if dte <= 45 else PARAMS["long"]

def step(msg):
    print(f"  [{time.perf_counter() - T0:.1f}s] {msg}", flush=True)

T0 = time.perf_counter()

# ── Step 1 : Connect + Forward ──
step("1/5  connect + forward")
ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)
_SUPPRESS = {10090, 10197, 10167, 200, 2119, 2104, 2108}
ib.errorEvent += lambda reqId, code, msg, contract: (
    print(f"IB Error {code}: {msg}") if code not in _SUPPRESS else None
)
ib.reqMarketDataType(3)

fut = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
details = ib.reqContractDetails(fut)
now = datetime.now()

futures = []
for d in details:
    c = d.contract
    exp = c.lastTradeDateOrContractMonth
    try:
        exp_date = datetime.strptime(exp, "%Y%m%d") if len(exp) == 8 else datetime.strptime(exp, "%Y%m")
    except ValueError:
        continue
    dte = (exp_date - now).days
    if dte >= 7:
        futures.append((dte, c))

futures.sort(key=lambda x: x[0])
front_fut = futures[0][1]

fut_ticker = ib.reqMktData(front_fut, "", False, False)
ib.sleep(3)
bid_f, ask_f = safe(fut_ticker.bid), safe(fut_ticker.ask)
F_global = (bid_f + ask_f) / 2.0 if bid_f and ask_f else safe(fut_ticker.close)
ib.cancelMktData(front_fut)
ib.sleep(1)
step(f"1/5  done — F={F_global:.5f}")

# ── Step 2 : Chains EUU (all multipliers) ──
step("2/5  chains EUU")
# Collect per expiry: union of strikes from all multipliers
chain_data = {}  # {expiry: {"dte": int, "strikes": set, "multipliers": set, "exchange": str, ...}}
seen_exp = set()

for dte, fut_c in futures[:8]:
    chains = ib.reqSecDefOptParams("EUR", "CME", "FUT", fut_c.conId)
    for ch in chains:
        if ch.tradingClass != "EUU":
            continue
        for exp in sorted(ch.expirations):
            exp_date = datetime.strptime(exp, "%Y%m%d") if len(exp) == 8 else datetime.strptime(exp, "%Y%m")
            dte_fop = (exp_date - now).days
            if dte_fop < 10:
                continue
            if exp not in chain_data:
                chain_data[exp] = {
                    "expiry": exp, "dte": dte_fop,
                    "strikes": set(), "multipliers": set(),
                    "exchange": ch.exchange,
                    "fut_conId": fut_c.conId, "fut_symbol": fut_c.localSymbol,
                }
            chain_data[exp]["strikes"].update(ch.strikes)
            chain_data[exp]["multipliers"].add(str(ch.multiplier))

euu_chains = []
for exp, data in chain_data.items():
    data["strikes"] = sorted(data["strikes"])
    data["multipliers"] = sorted(data["multipliers"])
    euu_chains.append(data)
euu_chains.sort(key=lambda x: x["dte"])

selected = []
for target in TARGET_DTES:
    best = min(euu_chains, key=lambda x: abs(x["dte"] - target))
    if best not in selected:
        selected.append(best)
step(f"2/5  done — {len(selected)} tenors")

# ── Step 3 : Qualify C+P contracts (try all multipliers) ──
step("3/5  qualify contracts (C+P × multipliers)")
qualified_contracts = {}
for ch in selected:
    strikes, expiry, dte = ch["strikes"], ch["expiry"], ch["dte"]
    multipliers = ch["multipliers"]
    n_side = get_params(dte)["n_side"]
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - F_global))
    lo = max(0, atm_idx - n_side)
    hi = min(len(strikes) - 1, atm_idx + n_side)
    scan_strikes = strikes[lo:hi+1]

    qualified_contracts[expiry] = {}
    for K in scan_strikes:
        qualified_contracts[expiry][K] = {}
        for right in ("C", "P"):
            # Try each multiplier, keep first valid contract
            for mult in multipliers:
                fop = Contract(symbol="EUR", secType="FOP", exchange=ch["exchange"],
                               currency="USD", lastTradeDateOrContractMonth=expiry,
                               strike=K, right=right, multiplier=mult,
                               tradingClass="EUU")
                det = ib.reqContractDetails(fop)
                if det:
                    qualified_contracts[expiry][K][right] = det[0].contract
                    break  # got one, no need to try other multipliers
step("3/5  done")

# ── Step 4 : Scan IV ──
step("4/5  scan IV (reqMktData)")
rows, diag_rows, all_arb_flags = [], [], []

for ch in selected:
    expiry, dte = ch["expiry"], ch["dte"]
    p = get_params(dte)
    contracts = qualified_contracts.get(expiry, {})
    if not contracts:
        diag_rows.append({"expiry": expiry, "dte": dte, "status": "NO_CONTRACTS",
                          "n_strikes": 0, "delta_min": None, "delta_max": None})
        continue

    tickers = {}
    for K, rights in contracts.items():
        for right, contract in rights.items():
            tickers[(K, right)] = (contract, ib.reqMktData(contract, "100", False, False))
    ib.sleep(WAIT_GREEKS)

    raw = {}
    for (K, right), (contract, ticker) in tickers.items():
        greeks = ticker.modelGreeks
        iv    = safe(greeks.impliedVol) if greeks else None
        delta = safe(greeks.delta)      if greeks else None
        if iv and iv > 0:
            raw[(K, right)] = {"iv": iv, "delta": delta}
        ib.cancelMktData(contract)
    ib.sleep(0.5)

    # Merge C+P
    iv_by_strike, delta_by_strike = {}, {}
    all_strikes = sorted(set(K for (K, _) in raw.keys()))

    for K in all_strikes:
        c_data, p_data = raw.get((K, "C")), raw.get((K, "P"))
        iv_c = c_data["iv"] if c_data else None
        iv_p = p_data["iv"] if p_data else None
        d_c  = c_data["delta"] if c_data else None
        d_p  = p_data["delta"] if p_data else None

        if iv_c and iv_p:
            diff = abs(iv_c - iv_p)
            if diff > IV_ARB_THRESHOLD:
                all_arb_flags.append({"expiry": expiry, "strike": K,
                                      "iv_call": round(iv_c*100, 4),
                                      "iv_put": round(iv_p*100, 4),
                                      "diff_pct": round(diff*100, 4)})
            iv_merged = (iv_c + iv_p) / 2.0
        elif iv_c:
            iv_merged = iv_c
        elif iv_p:
            iv_merged = iv_p
        else:
            continue

        if d_c is not None:
            delta = d_c
        elif d_p is not None:
            delta = 1.0 + d_p
        else:
            delta = None

        if delta is not None:
            iv_by_strike[K] = iv_merged
            delta_by_strike[K] = delta

    if len(iv_by_strike) < p["min_strikes"]:
        diag_rows.append({"expiry": expiry, "dte": dte, "status": "TOO_FEW_STRIKES",
                          "n_strikes": len(iv_by_strike), "delta_min": None, "delta_max": None})
        continue

    # PCHIP
    pairs = sorted([(delta_by_strike[k], iv_by_strike[k], k) for k in iv_by_strike])
    deltas = np.array([t[0] for t in pairs])
    ivs    = np.array([t[1] for t in pairs])
    ks     = np.array([t[2] for t in pairs])

    delta_min, delta_max = float(deltas[0]), float(deltas[-1])
    interp_iv     = PchipInterpolator(deltas, ivs)
    interp_strike = PchipInterpolator(deltas, ks)

    def get_iv(d):
        if d < delta_min or d > delta_max:
            return None, None
        try:
            return float(interp_iv(d)), float(interp_strike(d))
        except Exception:
            return None, None

    iv_atm,  k_atm  = get_iv(0.50)
    iv_25dc, k_25dc = get_iv(0.25)
    iv_25dp, k_25dp = get_iv(0.75)
    iv_10dc, k_10dc = get_iv(0.10)
    iv_10dp, k_10dp = get_iv(0.90)

    rr25 = (iv_25dc - iv_25dp) * 100 if iv_25dc and iv_25dp else None
    bf25 = ((iv_25dc + iv_25dp) / 2 - iv_atm) * 100 if iv_25dc and iv_25dp and iv_atm else None

    tp = get_params(dte)
    skip_reason = None
    if rr25 is not None and abs(rr25) > tp["rr25_max"]:
        skip_reason = f"RR25={rr25:.2f}"
    if bf25 is not None and bf25 < tp["bf25_min"]:
        skip_reason = f"BF25={bf25:.2f}"

    diag_rows.append({"expiry": expiry, "dte": dte,
                       "status": f"SKIP({skip_reason})" if skip_reason else "OK",
                       "n_strikes": len(iv_by_strike),
                       "delta_min": round(delta_min, 4),
                       "delta_max": round(delta_max, 4)})
    if skip_reason:
        continue

    if   dte <= 45:  label = "1M"
    elif dte <= 75:  label = "2M"
    elif dte <= 105: label = "3M"
    elif dte <= 135: label = "4M"
    elif dte <= 165: label = "5M"
    else:            label = "6M"

    rows.append({
        "tenor_label":   label,
        "expiry":        expiry,
        "dte":           dte,
        "F":             round(F_global, 5),
        "sigma_ATM_pct": round(iv_atm  * 100, 4) if iv_atm  else None,
        "RR25_pct":      round(rr25,      4)      if rr25    else None,
        "BF25_pct":      round(bf25,      4)      if bf25    else None,
        "iv_10dp_pct":   round(iv_10dp * 100, 4)  if iv_10dp else None,
        "iv_25dp_pct":   round(iv_25dp * 100, 4)  if iv_25dp else None,
        "iv_25dc_pct":   round(iv_25dc * 100, 4)  if iv_25dc else None,
        "iv_10dc_pct":   round(iv_10dc * 100, 4)  if iv_10dc else None,
        "strike_10dp":   round(k_10dp, 4)          if k_10dp  else None,
        "strike_25dp":   round(k_25dp, 4)          if k_25dp  else None,
        "strike_atm":    round(k_atm,  4)          if k_atm   else None,
        "strike_25dc":   round(k_25dc, 4)          if k_25dc  else None,
        "strike_10dc":   round(k_10dc, 4)          if k_10dc  else None,
    })
step("4/5  done")

# ── Step 5 : Output ──
step("5/5  output")
ib.disconnect()

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
pd.set_option("display.float_format", "{:.4f}".format)

ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

print(f"\n{'═' * 120}")
print(f"  VOL_MID  —  {ts}  —  F={F_global:.5f}")
print(f"{'═' * 120}")
if rows:
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
else:
    print("  (empty)")

print(f"\n{'═' * 120}")
print(f"  DIAGNOSTICS")
print(f"{'═' * 120}")
if diag_rows:
    df_diag = pd.DataFrame(diag_rows)
    print(df_diag.to_string(index=False))
else:
    print("  all tenors OK")

print(f"\n{'═' * 120}")
print(f"  ARB FLAGS  (|iv_C - iv_P| > {IV_ARB_THRESHOLD*100:.1f}%)")
print(f"{'═' * 120}")
if all_arb_flags:
    df_arb = pd.DataFrame(all_arb_flags)
    print(df_arb.to_string(index=False))
else:
    print("  none")

print(f"{'═' * 120}")
step("done")
print()

# ── CSV ──
if rows:
    for r in rows:
        r["timestamp"] = ts
    fields = ["timestamp"] + [k for k in rows[0].keys() if k != "timestamp"]
    with open("vol_mid_output.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)