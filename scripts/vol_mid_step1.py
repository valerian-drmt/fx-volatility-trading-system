"""
vol_mid_step1.py — version asynchrone optimisée
6 tenors × strikes adaptatifs | refresh 30min | output DataFrame
"""
import math
import pandas as pd
from datetime import datetime
from scipy.interpolate import PchipInterpolator
import numpy as np
from ib_insync import IB, Contract, util

util.patchAsyncio()

# ── Config globale ──
PORT      = 4002
CLIENT_ID = 14

TARGET_DTES  = [30, 60, 90, 120, 150, 180]
WAIT_GREEKS  = 8

# Paramètres par tenor (court vs long)
PARAMS = {
    "short": {"n_side": 6,  "rr25_max": 10.0, "bf25_min": -6.0, "min_strikes": 5},  # DTE <= 45
    "long":  {"n_side": 10, "rr25_max":  6.0, "bf25_min": -4.0, "min_strikes": 7},  # DTE > 45
}

# ── Connexion ──
ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)

def on_error(reqId, errorCode, errorString, contract):
    if errorCode in (10090, 10197, 10167, 200, 2119, 2104):
        return
    print(f"Error {errorCode}, reqId {reqId}: {errorString}")

ib.errorEvent += on_error
ib.reqMarketDataType(3)

def safe(val):
    return val if val is not None and not (isinstance(val, float) and math.isnan(val)) else None

def get_params(dte):
    return PARAMS["short"] if dte <= 45 else PARAMS["long"]

# ── Step 1 : Front future → F ──
print("=" * 60)
print("Step 1 : Front future EUR/CME → F")
print("=" * 60)

fut = Contract()
fut.symbol   = "EUR"
fut.secType  = "FUT"
fut.exchange = "CME"
fut.currency = "USD"

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
    if dte < 7:
        continue
    futures.append((dte, c))

futures.sort(key=lambda x: x[0])
front_fut = futures[0][1]

fut_ticker = ib.reqMktData(front_fut, "", False, False)
ib.sleep(3)
bid_f = safe(fut_ticker.bid)
ask_f = safe(fut_ticker.ask)
F_global = (bid_f + ask_f) / 2.0 if bid_f and ask_f else safe(fut_ticker.close)
ib.cancelMktData(front_fut)
ib.sleep(1)
print(f"  {front_fut.localSymbol}  F = {F_global:.5f}")

# ── Step 2 : Chains EUU ──
print(f"\n{'=' * 60}")
print("Step 2 : Chains EUU disponibles")
print("=" * 60)

euu_chains = []
seen = set()

for dte, fut_c in futures[:8]:
    chains = ib.reqSecDefOptParams(
        underlyingSymbol  = "EUR",
        futFopExchange    = "CME",
        underlyingSecType = "FUT",
        underlyingConId   = fut_c.conId,
    )
    for ch in chains:
        if ch.tradingClass != "EUU":
            continue
        for exp in sorted(ch.expirations):
            if exp in seen:
                continue
            seen.add(exp)
            exp_date = datetime.strptime(exp, "%Y%m%d") if len(exp) == 8 else datetime.strptime(exp, "%Y%m")
            dte_fop = (exp_date - now).days
            if dte_fop < 10:
                continue
            euu_chains.append({
                "expiry":     exp,
                "dte":        dte_fop,
                "strikes":    sorted(ch.strikes),
                "exchange":   ch.exchange,
                "multiplier": ch.multiplier,
                "fut_conId":  fut_c.conId,
                "fut_symbol": fut_c.localSymbol,
            })

euu_chains.sort(key=lambda x: x["dte"])
print(f"  {len(euu_chains)} expirations EUU disponibles :")
for ch in euu_chains:
    print(f"    {ch['expiry']}  DTE={ch['dte']:>3}  nStrikes={len(ch['strikes'])}")

# ── Step 3 : Sélection tenors ──
selected = []
for target in TARGET_DTES:
    best = min(euu_chains, key=lambda x: abs(x["dte"] - target))
    if best not in selected:
        selected.append(best)

print(f"\n  Tenors sélectionnés :")
for ch in selected:
    p = get_params(ch["dte"])
    print(f"    {ch['expiry']}  DTE={ch['dte']:>3}  "
          f"n_strikes={p['n_side']*2+1}  "
          f"rr25_max={p['rr25_max']}  bf25_min={p['bf25_min']}")

# ── Step 4 : Qualification contrats ──
print(f"\n{'=' * 60}")
print("Step 3 : Qualification des contrats")
print("=" * 60)

qualified_contracts = {}

for ch in selected:
    strikes = ch["strikes"]
    expiry  = ch["expiry"]
    dte     = ch["dte"]
    p       = get_params(dte)
    n_side  = p["n_side"]

    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - F_global))
    lo = max(0, atm_idx - n_side)
    hi = min(len(strikes) - 1, atm_idx + n_side)
    scan_strikes = strikes[lo:hi+1]

    qualified_contracts[expiry] = {}
    print(f"  {expiry} (DTE={dte}) — {len(scan_strikes)} strikes...", end=" ", flush=True)

    for K in scan_strikes:
        fop = Contract()
        fop.symbol       = "EUR"
        fop.secType      = "FOP"
        fop.exchange     = ch["exchange"]
        fop.currency     = "USD"
        fop.lastTradeDateOrContractMonth = expiry
        fop.strike       = K
        fop.right        = "C"
        fop.multiplier   = str(ch["multiplier"])
        fop.tradingClass = "EUU"

        fop_details = ib.reqContractDetails(fop)
        if fop_details:
            qualified_contracts[expiry][K] = fop_details[0].contract

    print(f"{len(qualified_contracts[expiry])} OK")

# ── Step 5 : Scan IV parallèle ──
print(f"\n{'=' * 60}")
print(f"Step 4 : Scan IV parallèle (wait={WAIT_GREEKS}s/tenor)")
print("=" * 60)

rows = []

for ch in selected:
    expiry    = ch["expiry"]
    dte       = ch["dte"]
    p         = get_params(dte)
    contracts = qualified_contracts.get(expiry, {})

    if not contracts:
        print(f"\n  {expiry} — aucun contrat qualifié, SKIP")
        continue

    print(f"\n  {expiry} (DTE={dte}) — envoi {len(contracts)} reqMktData simultanés...")

    tickers = {}
    for K, contract in contracts.items():
        tickers[K] = (contract, ib.reqMktData(contract, "100", False, False))

    ib.sleep(WAIT_GREEKS)

    iv_by_strike    = {}
    delta_by_strike = {}

    for K, (contract, ticker) in tickers.items():
        greeks = ticker.modelGreeks
        iv    = safe(greeks.impliedVol) if greeks else None
        delta = safe(greeks.delta)      if greeks else None

        if iv and iv > 0:
            iv_by_strike[K]    = iv
            delta_by_strike[K] = delta
            print(f"    K={K:.4f}  IV={iv*100:.2f}%  Δ={delta:.3f}" if delta else
                  f"    K={K:.4f}  IV={iv*100:.2f}%  Δ=--")
        else:
            print(f"    K={K:.4f}  IV=--")

        ib.cancelMktData(contract)

    ib.sleep(0.5)

    if len(iv_by_strike) < p["min_strikes"]:
        print(f"    SKIP — pas assez de strikes ({len(iv_by_strike)} < {p['min_strikes']})")
        continue

    # ── Interpolation delta-space ──
    pairs = [(delta_by_strike[k], iv_by_strike[k], k)
             for k in iv_by_strike if delta_by_strike.get(k) is not None]
    pairs.sort(key=lambda x: x[0])

    deltas = np.array([p[0] for p in pairs])
    ivs    = np.array([p[1] for p in pairs])
    ks     = np.array([p[2] for p in pairs])

    interp_iv     = PchipInterpolator(deltas, ivs)
    interp_strike = PchipInterpolator(deltas, ks)

    def get_iv_delta(d):
        try:
            return float(interp_iv(d)), float(interp_strike(d))
        except Exception:
            return None, None

    iv_atm,  k_atm  = get_iv_delta(0.50)
    iv_25dc, k_25dc = get_iv_delta(0.25)
    iv_25dp, k_25dp = get_iv_delta(-0.25)
    iv_10dc, k_10dc = get_iv_delta(0.10)
    iv_10dp, k_10dp = get_iv_delta(-0.10)

    rr25 = (iv_25dc - iv_25dp) * 100 if iv_25dc and iv_25dp else None
    bf25 = ((iv_25dc + iv_25dp) / 2 - iv_atm) * 100 if iv_25dc and iv_25dp and iv_atm else None

    # ── Validation adaptative ──
    tenor_params = get_params(dte)
    if rr25 is not None and abs(rr25) > tenor_params["rr25_max"]:
        print(f"    SKIP {expiry} — RR25 aberrant ({rr25:.2f}%)")
        continue
    if bf25 is not None and bf25 < tenor_params["bf25_min"]:
        print(f"    SKIP {expiry} — BF25 aberrant ({bf25:.2f}%)")
        continue

    if   dte <= 45:  label = "1M"
    elif dte <= 75:  label = "2M"
    elif dte <= 105: label = "3M"
    elif dte <= 135: label = "4M"
    elif dte <= 165: label = "5M"
    else:            label = "6M"

    row = {
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
    }
    rows.append(row)

    print(f"\n    sigma_ATM={row['sigma_ATM_pct']}%  "
          f"RR25={row['RR25_pct']}%  BF25={row['BF25_pct']}%")

# ── DataFrame summary ──
print(f"\n{'=' * 120}")
print(f"DATAFRAME VOL_MID  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 120)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", "{:.4f}".format)

if rows:
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print(f"\n  {len(rows)} tenors valides sur {len(selected)} scannés")
else:
    print("  Aucune donnée valide")

print("=" * 120)

# ── Export CSV ──
if rows:
    import csv
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        r["timestamp"] = ts
    fields = ["timestamp"] + [k for k in rows[0].keys() if k != "timestamp"]
    with open("vol_mid_output.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n  >> vol_mid_output.csv écrit ({len(rows)} tenors)")
ib.disconnect()