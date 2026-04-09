"""
vol_fair_step2.py
─────────────────
Calcule σ_fair par tenor depuis vol_mid_output.csv (step 1).

Pipeline :
  A. Realized Vol Yang-Zhang  ← reqHistoricalData IB (OHLC journalier)
  B. σ_model GARCH(1,1)       ← calibré sur returns historiques
  C. δ_book                   ← vega net portfolio / vega limit
  D. σ_fair = W1*(RV+RP) + W2*σ_model + δ_book

Input  : vol_mid_output.csv
Output : vol_fair_output.csv + print DataFrame
"""

import math
import numpy as np
import pandas as pd
from arch import arch_model
from ib_insync import IB, Contract, util
import warnings
warnings.filterwarnings("ignore")

util.patchAsyncio()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

PORT      = 4002
CLIENT_ID = 16

W1 = 0.65
W2 = 0.35

ALPHA_BOOK       = 0.20
SIGNAL_THRESHOLD = 0.20

RISK_PREMIUM = {
    "1M": 1.20, "2M": 1.35, "3M": 1.50,
    "4M": 1.55, "5M": 1.58, "6M": 1.60,
}

VEGA_LIMITS = {
    "1M": 150_000, "2M": 200_000, "3M": 300_000,
    "4M": 350_000, "5M": 375_000, "6M": 400_000,
}

TENOR_T = {
    "1M": 1/12, "2M": 2/12, "3M": 3/12,
    "4M": 4/12, "5M": 5/12, "6M": 6/12,
}

def safe(val):
    return val if val is not None and not (isinstance(val, float) and math.isnan(val)) else None

# ─────────────────────────────────────────────
# CONNEXION IB
# ─────────────────────────────────────────────

ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)

def on_error(reqId, errorCode, errorString, contract):
    if errorCode in (10090, 10197, 10167, 200, 2119, 2104, 2106):
        return
    print(f"  [IB Error {errorCode}] {errorString}")

ib.errorEvent += on_error
ib.reqMarketDataType(3)

# ─────────────────────────────────────────────
# CHARGEMENT STEP 1
# ─────────────────────────────────────────────

df_step1 = pd.read_csv("vol_mid_output.csv")
print(f"[Step2] {len(df_step1)} tenors chargés depuis vol_mid_output.csv")

# ─────────────────────────────────────────────
# COUCHE A — OHLC + Yang-Zhang RV
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("COUCHE A — Realized Vol (Yang-Zhang)")
print("=" * 50)

fut_continuous = Contract()
fut_continuous.symbol   = "EUR"
fut_continuous.secType  = "CONTFUT"
fut_continuous.exchange = "CME"
fut_continuous.currency = "USD"

bars = ib.reqHistoricalData(
    fut_continuous,
    endDateTime    = "",
    durationStr    = "1 Y",
    barSizeSetting = "1 day",
    whatToShow     = "ADJUSTED_LAST",
    useRTH         = True,
    formatDate     = 1,
)

if not bars:
    raise ValueError("Pas de données historiques IB")

df_ohlc = util.df(bars)[["date", "open", "high", "low", "close"]]
df_ohlc["date"] = pd.to_datetime(df_ohlc["date"])
df_ohlc = df_ohlc.sort_values("date").reset_index(drop=True)
print(f"  Contrat continu EUR/CME")
print(f"  {len(df_ohlc)} barres [{df_ohlc['date'].iloc[0].date()} → {df_ohlc['date'].iloc[-1].date()}]")

# Yang-Zhang par tenor
rv_map = {}
for label, T in TENOR_T.items():
    window = max(21, int(T * 252))
    window = min(window, len(df_ohlc) - 1)

    df_w = df_ohlc.tail(window).copy()
    n    = len(df_w)

    o = np.log(df_w["open"].values)
    h = np.log(df_w["high"].values)
    l = np.log(df_w["low"].values)
    c = np.log(df_w["close"].values)

    overnight = o[1:] - c[:-1]
    oc        = c[1:] - o[1:]
    rs        = ((h[1:] - c[1:]) * (h[1:] - o[1:]) +
                 (l[1:] - c[1:]) * (l[1:] - o[1:]))

    sigma2_on = np.var(overnight, ddof=1)
    sigma2_oc = np.var(oc, ddof=1)
    sigma2_rs = np.mean(rs)
    k_yz      = 0.34 / (1.34 + (n + 1) / (n - 1))
    sigma2_yz = sigma2_on + k_yz * sigma2_oc + (1 - k_yz) * sigma2_rs

    rv = float(np.sqrt(max(sigma2_yz, 0) * 252) * 100)
    rv_map[label] = round(rv, 4)
    print(f"    {label} (fenêtre {window}j) : RV = {rv:.2f}%")

# ─────────────────────────────────────────────
# COUCHE B — GARCH(1,1)
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("COUCHE B — GARCH(1,1) forward vol")
print("=" * 50)

returns = (np.log(df_ohlc["close"] / df_ohlc["close"].shift(1))
           .dropna() * 100)

garch_model  = arch_model(returns, vol="Garch", p=1, q=1,
                           mean="Constant", dist="normal")
garch_result = garch_model.fit(disp="off")

omega = garch_result.params["omega"]
alpha = garch_result.params["alpha[1]"]
beta  = garch_result.params["beta[1]"]

cond_var    = garch_result.conditional_volatility.iloc[-1] ** 2
vol_current = np.sqrt(cond_var * 252)

persistence = min(alpha + beta, 0.9999)
vol_lr      = np.sqrt(omega / (1 - persistence) * 252)
kappa       = -np.log(persistence)

print(f"    ω={omega:.6f}  α={alpha:.4f}  β={beta:.4f}  "
      f"persist={persistence:.4f}  κ={kappa:.4f}")
print(f"    Vol courante={vol_current:.2f}%  Vol LT={vol_lr:.2f}%")

model_map = {}
var_c  = (vol_current / 100) ** 2
var_lr = (vol_lr      / 100) ** 2

for label, T in TENOR_T.items():
    var_T = var_lr + (var_c - var_lr) * np.exp(-kappa * T)
    vol   = float(np.sqrt(max(var_T, 0)) * 100)
    model_map[label] = round(vol, 4)
    print(f"    {label} (T={T:.3f}y) : σ_model = {vol:.2f}%")

# ─────────────────────────────────────────────
# COUCHE C — δ_book depuis portfolio IB
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("COUCHE C — δ_book portfolio IB")
print("=" * 50)

from datetime import datetime
now = datetime.now()

positions = ib.reqPositions()
ib.sleep(2)

fop_pos = [p for p in positions
           if p.contract.symbol == "EUR"
           and p.contract.secType == "FOP"
           and p.position != 0]

print(f"  {len(fop_pos)} positions FOP EUR ouvertes")

expiry_to_label = dict(zip(
    df_step1["expiry"].astype(str),
    df_step1["tenor_label"]
))

vega_by_tenor = {label: 0.0 for label in TENOR_T}

for pos in fop_pos:
    c = pos.contract
    c.exchange = "CME"
    details = ib.reqContractDetails(c)
    if details:
        c = details[0].contract

    ticker = ib.reqMktData(c, "100", False, False)
    ib.sleep(3)

    greeks = ticker.modelGreeks
    vega   = safe(greeks.vega) if greeks else None
    ib.cancelMktData(c)

    if vega is None:
        print(f"    {c.localSymbol} — vega non disponible, skip")
        continue

    # Associer au tenor
    exp_str = c.lastTradeDateOrContractMonth
    if exp_str in expiry_to_label:
        label = expiry_to_label[exp_str]
    else:
        try:
            exp_date = (datetime.strptime(exp_str, "%Y%m%d")
                        if len(exp_str) == 8
                        else datetime.strptime(exp_str, "%Y%m"))
        except ValueError:
            label = "3M"
        else:
            dte_pos = (exp_date - now).days
            target_dtes = {"1M": 30, "2M": 60, "3M": 90,
                           "4M": 120, "5M": 150, "6M": 180}
            label = min(target_dtes, key=lambda t: abs(target_dtes[t] - dte_pos))

    if label not in vega_by_tenor:
        continue

    contrib = vega * pos.position * float(c.multiplier or 125000) / 100.0
    vega_by_tenor[label] += contrib
    print(f"    {c.localSymbol}  qty={pos.position:+.0f}  "
          f"vega={vega:.5f}  contrib={contrib:+.0f}€/%")

for label, vnet in vega_by_tenor.items():
    print(f"    Vega net {label} = {vnet:+,.0f} €/vol%")

dbook_map = {}
for label, vnet in vega_by_tenor.items():
    limit = VEGA_LIMITS.get(label, 300_000)
    ratio = max(-1.0, min(1.0, vnet / limit))
    db    = round(-ALPHA_BOOK * ratio, 5)
    dbook_map[label] = db
    print(f"    {label} : ratio={ratio:+.3f}  δ_book={db:+.4f}%")

ib.disconnect()
print("\n[IB] Déconnecté")

# ─────────────────────────────────────────────
# COMBINAISON — σ_fair
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("COMBINAISON — σ_fair")
print("=" * 50)

rows = []
for _, row in df_step1.iterrows():
    label = row["tenor_label"]

    sigma_mid = row["sigma_ATM_pct"]
    rv        = rv_map.get(label, np.nan)
    rp        = RISK_PREMIUM.get(label, 1.50)
    ancre     = rv + rp if not np.isnan(rv) else np.nan
    s_model   = model_map.get(label, np.nan)
    db        = dbook_map.get(label, 0.0)

    if np.isnan(ancre) or np.isnan(s_model):
        sigma_fair = np.nan
        ecart      = np.nan
        signal     = "N/A"
    else:
        sigma_fair = round(W1 * ancre + W2 * s_model + db, 4)
        ecart      = round(sigma_fair - sigma_mid, 4)

        if   ecart < -SIGNAL_THRESHOLD: signal = "CHEAP"
        elif ecart > +SIGNAL_THRESHOLD: signal = "EXPENSIVE"
        else:                           signal = "FAIR"

    rows.append({
        "tenor_label":     label,
        "expiry":          row["expiry"],
        "dte":             row["dte"],
        "F":               row["F"],
        "sigma_mid_pct":   round(sigma_mid, 4),
        "RV_pct":          round(rv, 4)        if not np.isnan(rv)      else None,
        "RP_pct":          rp,
        "ancre_pct":       round(ancre, 4)     if not np.isnan(ancre)   else None,
        "sigma_model_pct": round(s_model, 4)   if not np.isnan(s_model) else None,
        "w1":              W1,
        "w2":              W2,
        "delta_book_pct":  db,
        "sigma_fair_pct":  sigma_fair,
        "ecart_pct":       ecart,
        "signal":          signal,
        "RR25_pct":        row["RR25_pct"],
        "BF25_pct":        row["BF25_pct"],
        "iv_10dp_pct":     row["iv_10dp_pct"],
        "iv_25dp_pct":     row["iv_25dp_pct"],
        "iv_25dc_pct":     row["iv_25dc_pct"],
        "iv_10dc_pct":     row["iv_10dc_pct"],
        "strike_atm":      row["strike_atm"],
        "strike_25dp":     row["strike_25dp"],
        "strike_25dc":     row["strike_25dc"],
        "strike_10dp":     row["strike_10dp"],
        "strike_10dc":     row["strike_10dc"],
    })

df_output = pd.DataFrame(rows)

# ─────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────

pd.set_option("display.float_format", "{:.4f}".format)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 200)

display_cols = [
    "tenor_label", "dte", "F",
    "sigma_mid_pct", "RV_pct", "RP_pct", "ancre_pct",
    "sigma_model_pct", "delta_book_pct",
    "sigma_fair_pct", "ecart_pct", "signal"
]

print("\n" + "=" * 100)
print("DATAFRAME VOL_FAIR")
print("=" * 100)
print(df_output[display_cols].to_string(index=False))

print("\n" + "=" * 60)
print("SIGNALS")
print("=" * 60)
for _, r in df_output.iterrows():
    marker = {"CHEAP": "▲ BUY", "EXPENSIVE": "▼ SELL", "FAIR": "— FAIR"}.get(
        r["signal"], r["signal"])
    print(f"  {r['tenor_label']:4s}  "
          f"σ_mid={r['sigma_mid_pct']:.2f}%  "
          f"σ_fair={r['sigma_fair_pct']:.2f}%  "
          f"écart={r['ecart_pct']:+.2f}%  {marker}")

df_output.to_csv("vol_fair_output.csv", index=False, encoding="utf-8-sig")
print(f"\n  >> vol_fair_output.csv écrit ({len(df_output)} tenors)")