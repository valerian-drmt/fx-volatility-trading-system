"""
vol_fair_step2.py — σ_fair par tenor (v2)
──────────────────────────────────────────
Fixes vs v1:
  - RP dynamique basé sur VRP spot (au lieu de constantes arbitraires)
  - GARCH blendé avec mean-reversion empirique RV
  - W1 conditionnel au ratio RV_court/RV_long

Pipeline:
  A. Yang-Zhang RV + RP dynamique
  B. GARCH(1,1) + empirical blend
  C. δ_book
  D. σ_fair = W1_adj*(RV+RP_dyn) + W2_adj*σ_model_blend + δ_book
"""
import math, csv, time
import numpy as np
import pandas as pd
from datetime import datetime
from arch import arch_model
from ib_insync import IB, Contract, util
import warnings
warnings.filterwarnings("ignore")

util.patchAsyncio()

# ── Config ──
PORT, CLIENT_ID = 4002, 16
WAIT_GREEKS     = 3

W1_BASE, W2_BASE = 0.65, 0.35
ALPHA_BOOK       = 0.20
SIGNAL_THRESHOLD = 0.20

# RP floors and VRP shift (instead of fixed RP per tenor)
RP_FLOOR    = 0.20   # minimum RP in vol%
VRP_SHIFT   = 0.50   # additive shift on VRP spot to get RP

# Fallback RP if no IV available (should not happen)
RP_FALLBACK = {"1M": 1.20, "2M": 1.35, "3M": 1.50,
               "4M": 1.55, "5M": 1.58, "6M": 1.60}

VEGA_LIMITS  = {"1M": 150_000, "2M": 200_000, "3M": 300_000,
                "4M": 350_000, "5M": 375_000, "6M": 400_000}

TENOR_T      = {"1M": 1/12, "2M": 2/12, "3M": 3/12,
                "4M": 4/12, "5M": 5/12, "6M": 6/12}

# W1 adjustment: if RV_short/RV_long > this threshold, reduce W1
W1_RATIO_THRESHOLD = 1.15
W1_RATIO_SENSITIVITY = 0.10  # W1 reduction per unit of excess ratio
W1_FLOOR = 0.40

# GARCH-empirical blend weight
GARCH_EMPIRICAL_BLEND = 0.50  # 50% GARCH, 50% empirical mean-reversion
EMPIRICAL_KAPPA = 2.0         # empirical mean-reversion speed (annualized)

def safe(val):
    return val if val is not None and not (isinstance(val, float) and math.isnan(val)) else None

def step(msg):
    print(f"  [{time.perf_counter() - T0:.1f}s] {msg}", flush=True)

def yang_zhang_rv(df_ohlc, window):
    """Compute Yang-Zhang RV on the last `window` bars. Returns annualized vol in %."""
    df_w = df_ohlc.tail(window).copy()
    n = len(df_w)
    if n < 3:
        return None
    o = np.log(df_w["open"].values)
    h = np.log(df_w["high"].values)
    l = np.log(df_w["low"].values)
    c = np.log(df_w["close"].values)
    overnight = o[1:] - c[:-1]
    oc = c[1:] - o[1:]
    rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (l[1:] - c[1:]) * (l[1:] - o[1:])
    s2_on = np.var(overnight, ddof=1)
    s2_oc = np.var(oc, ddof=1)
    s2_rs = np.mean(rs)
    k_yz = 0.34 / (1.34 + (n + 1) / (n - 1))
    s2_yz = s2_on + k_yz * s2_oc + (1 - k_yz) * s2_rs
    return float(np.sqrt(max(s2_yz, 0) * 252) * 100)

T0 = time.perf_counter()

# ── Step 1 : Connect + load step1 ──
step("1/6  connect + load vol_mid_output.csv")
ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)
_SUPPRESS = {10090, 10197, 10167, 200, 2119, 2104, 2108, 2106}
ib.errorEvent += lambda reqId, code, msg, contract: (
    print(f"IB Error {code}: {msg}") if code not in _SUPPRESS else None
)
ib.reqMarketDataType(3)

df_step1 = pd.read_csv("vol_mid_output.csv")
# Build IV_ATM lookup for dynamic RP
iv_atm_by_tenor = dict(zip(df_step1["tenor_label"], df_step1["sigma_ATM_pct"]))
step(f"1/6  done — {len(df_step1)} tenors loaded")

# ── Step 2 : OHLC + Yang-Zhang RV + dynamic RP ──
step("2/6  OHLC + Yang-Zhang RV + dynamic RP")

fut_cont = Contract(symbol="EUR", secType="CONTFUT", exchange="CME", currency="USD")
bars = ib.reqHistoricalData(
    fut_cont, endDateTime="", durationStr="1 Y",
    barSizeSetting="1 day", whatToShow="ADJUSTED_LAST",
    useRTH=True, formatDate=1,
)
if not bars:
    raise ValueError("No historical data from IB")

df_ohlc = util.df(bars)[["date", "open", "high", "low", "close"]]
df_ohlc["date"] = pd.to_datetime(df_ohlc["date"])
df_ohlc = df_ohlc.sort_values("date").reset_index(drop=True)

# Full-window RV (1 year) for empirical mean-reversion target
rv_full = yang_zhang_rv(df_ohlc, len(df_ohlc) - 1)

rv_rows = []
for label, T in TENOR_T.items():
    window = max(21, int(T * 252))
    window = min(window, len(df_ohlc) - 1)
    rv = yang_zhang_rv(df_ohlc, window)

    # Dynamic RP: based on observed VRP (IV - RV)
    iv_atm = iv_atm_by_tenor.get(label)
    if rv is not None and iv_atm is not None:
        vrp_spot = iv_atm - rv  # negative if IV < RV
        rp = max(RP_FLOOR, vrp_spot + VRP_SHIFT)
    else:
        rp = RP_FALLBACK.get(label, 1.50)

    anchor = round(rv + rp, 4) if rv is not None else None
    rv_rows.append({
        "tenor": label, "window": window,
        "RV_pct": round(rv, 4) if rv else None,
        "IV_ATM_pct": round(iv_atm, 4) if iv_atm else None,
        "VRP_spot_pct": round(iv_atm - rv, 4) if iv_atm and rv else None,
        "RP_pct": round(rp, 4),
        "anchor_pct": anchor,
    })

step(f"2/6  done — {len(df_ohlc)} bars, RV_full={rv_full:.2f}%")

# ── Step 3 : GARCH(1,1) + empirical blend ──
step("3/6  GARCH(1,1) + empirical blend")

returns = (np.log(df_ohlc["close"] / df_ohlc["close"].shift(1)).dropna() * 100)
garch_fit = arch_model(returns, vol="Garch", p=1, q=1,
                        mean="Constant", dist="normal").fit(disp="off")

omega = garch_fit.params["omega"]
alpha = garch_fit.params["alpha[1]"]
beta  = garch_fit.params["beta[1]"]
persistence = min(alpha + beta, 0.9999)
kappa = -np.log(persistence)

cond_var = garch_fit.conditional_volatility.iloc[-1] ** 2
vol_current = np.sqrt(cond_var * 252)
vol_lr = np.sqrt(omega / (1 - persistence) * 252)

var_c  = (vol_current / 100) ** 2
var_lr = (vol_lr / 100) ** 2

rv_map_for_blend = {r["tenor"]: r["RV_pct"] for r in rv_rows}

garch_rows = []
for label, T in TENOR_T.items():
    # GARCH forward projection
    var_T = var_lr + (var_c - var_lr) * np.exp(-kappa * T)
    vol_garch = float(np.sqrt(max(var_T, 0)) * 100)

    # Empirical mean-reversion: RV(tenor) converges to RV_full at speed EMPIRICAL_KAPPA
    rv_tenor = rv_map_for_blend.get(label)
    if rv_tenor is not None and rv_full is not None:
        vol_empirical = rv_full + (rv_tenor - rv_full) * np.exp(-EMPIRICAL_KAPPA * T)
    else:
        vol_empirical = vol_garch  # fallback

    # Blend
    vol_model = GARCH_EMPIRICAL_BLEND * vol_garch + (1 - GARCH_EMPIRICAL_BLEND) * vol_empirical

    garch_rows.append({
        "tenor": label, "T": round(T, 4),
        "sigma_garch_pct": round(vol_garch, 4),
        "sigma_empirical_pct": round(vol_empirical, 4),
        "sigma_model_pct": round(vol_model, 4),
    })

step(f"3/6  done — ω={omega:.6f} α={alpha:.4f} β={beta:.4f} persist={persistence:.4f} RV_full={rv_full:.2f}%")

# ── Step 4 : Conditional W1 ──
rv_1m = next((r["RV_pct"] for r in rv_rows if r["tenor"] == "1M"), None)
rv_6m = next((r["RV_pct"] for r in rv_rows if r["tenor"] == "6M"), None)

if rv_1m and rv_6m and rv_6m > 0:
    rv_ratio = rv_1m / rv_6m
    if rv_ratio > W1_RATIO_THRESHOLD:
        W1 = max(W1_FLOOR, W1_BASE - W1_RATIO_SENSITIVITY * (rv_ratio - 1.0))
    else:
        W1 = W1_BASE
else:
    W1 = W1_BASE
    rv_ratio = None
W2 = 1.0 - W1

# ── Step 5 : δ_book (portfolio vega) ──
step("4/6  δ_book (portfolio vega)")

now = datetime.now()
positions = ib.reqPositions()
ib.sleep(2)

fop_pos = [p for p in positions
           if p.contract.symbol == "EUR"
           and p.contract.secType == "FOP"
           and p.position != 0]

expiry_to_label = dict(zip(df_step1["expiry"].astype(str), df_step1["tenor_label"]))
target_dtes = {"1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180}
vega_by_tenor = {label: 0.0 for label in TENOR_T}

for pos in fop_pos:
    c = pos.contract
    c.exchange = "CME"
    det = ib.reqContractDetails(c)
    if det:
        c = det[0].contract
    ticker = ib.reqMktData(c, "100", False, False)
    ib.sleep(WAIT_GREEKS)
    greeks = ticker.modelGreeks
    vega = safe(greeks.vega) if greeks else None
    ib.cancelMktData(c)
    if vega is None:
        continue
    exp_str = c.lastTradeDateOrContractMonth
    if exp_str in expiry_to_label:
        label = expiry_to_label[exp_str]
    else:
        try:
            exp_date = (datetime.strptime(exp_str, "%Y%m%d") if len(exp_str) == 8
                        else datetime.strptime(exp_str, "%Y%m"))
            dte_pos = (exp_date - now).days
            label = min(target_dtes, key=lambda t: abs(target_dtes[t] - dte_pos))
        except ValueError:
            label = "3M"
    if label not in vega_by_tenor:
        continue
    contrib = vega * pos.position * float(c.multiplier or 125000) / 100.0
    vega_by_tenor[label] += contrib

book_rows = []
for label, vnet in vega_by_tenor.items():
    limit = VEGA_LIMITS.get(label, 300_000)
    ratio = max(-1.0, min(1.0, vnet / limit))
    db = round(-ALPHA_BOOK * ratio, 5)
    book_rows.append({"tenor": label, "vega_net": round(vnet, 0),
                      "vega_limit": limit, "ratio": round(ratio, 4),
                      "delta_book_pct": db})

step(f"4/6  done — {len(fop_pos)} FOP positions")

# ── Step 6 : Combine σ_fair ──
step("5/6  combine σ_fair")

rv_map    = {r["tenor"]: r for r in rv_rows}
garch_map = {r["tenor"]: r for r in garch_rows}
book_map  = {r["tenor"]: r for r in book_rows}

fair_rows = []
for _, row in df_step1.iterrows():
    label = row["tenor_label"]
    sigma_mid = row["sigma_ATM_pct"]

    rv_data    = rv_map.get(label, {})
    garch_data = garch_map.get(label, {})
    book_data  = book_map.get(label, {})

    anchor  = rv_data.get("anchor_pct")
    s_model = garch_data.get("sigma_model_pct")  # blended model
    db      = book_data.get("delta_book_pct", 0.0)

    if anchor is not None and s_model is not None:
        sigma_fair = round(W1 * anchor + W2 * s_model + db, 4)
        ecart = round(sigma_fair - sigma_mid, 4)
        if   ecart > +SIGNAL_THRESHOLD: signal = "CHEAP"
        elif ecart < -SIGNAL_THRESHOLD: signal = "EXPENSIVE"
        else:                           signal = "FAIR"
    else:
        sigma_fair, ecart, signal = None, None, "N/A"

    fair_rows.append({
        "tenor_label":     label,
        "expiry":          row["expiry"],
        "dte":             row["dte"],
        "F":               row["F"],
        "sigma_mid_pct":   round(sigma_mid, 4),
        "RV_pct":          rv_data.get("RV_pct"),
        "VRP_spot_pct":    rv_data.get("VRP_spot_pct"),
        "RP_pct":          rv_data.get("RP_pct"),
        "anchor_pct":      anchor,
        "sigma_model_pct": s_model,
        "w1":              round(W1, 4),
        "w2":              round(W2, 4),
        "delta_book_pct":  db,
        "sigma_fair_pct":  sigma_fair,
        "ecart_pct":       ecart,
        "signal":          signal,
        "RR25_pct":        row.get("RR25_pct"),
        "BF25_pct":        row.get("BF25_pct"),
    })

step("5/6  done")

# ── Step 7 : Output ──
step("6/6  output")
ib.disconnect()

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
pd.set_option("display.float_format", "{:.4f}".format)

ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# W1 adjustment info
print(f"\n{'═' * 100}")
print(f"  W1 ADJUSTMENT  —  RV_1M/RV_6M = {rv_ratio:.3f}" if rv_ratio else "  W1 ADJUSTMENT  —  no ratio")
print(f"  W1_base={W1_BASE}  W1_adj={W1:.4f}  W2_adj={W2:.4f}")
print(f"{'═' * 100}")

# Layer A — RV + dynamic RP
print(f"\n{'═' * 100}")
print(f"  LAYER A — Yang-Zhang RV + dynamic RP  —  {ts}")
print(f"{'═' * 100}")
df_rv = pd.DataFrame(rv_rows)
print(df_rv.to_string(index=False))

# Layer B — GARCH + empirical blend
print(f"\n{'═' * 100}")
print(f"  LAYER B — GARCH + empirical blend  —  vol_cur={vol_current:.2f}%  vol_LR={vol_lr:.2f}%  RV_full={rv_full:.2f}%")
print(f"{'═' * 100}")
df_garch = pd.DataFrame(garch_rows)
print(df_garch.to_string(index=False))

# Layer C — Book
print(f"\n{'═' * 100}")
print(f"  LAYER C — δ_book  (α={ALPHA_BOOK})")
print(f"{'═' * 100}")
df_book = pd.DataFrame(book_rows)
print(df_book.to_string(index=False))

# σ_fair + signal
print(f"\n{'═' * 120}")
print(f"  VOL_FAIR  —  {ts}  —  W1={W1:.4f}  W2={W2:.4f}  threshold={SIGNAL_THRESHOLD}%")
print(f"{'═' * 120}")
df_fair = pd.DataFrame(fair_rows)
display_cols = ["tenor_label", "dte", "F", "sigma_mid_pct", "RV_pct", "VRP_spot_pct",
                "RP_pct", "anchor_pct", "sigma_model_pct",
                "delta_book_pct", "sigma_fair_pct", "ecart_pct", "signal"]
print(df_fair[display_cols].to_string(index=False))

# Signals
print(f"\n{'═' * 80}")
print(f"  SIGNALS")
print(f"{'═' * 80}")
for _, r in df_fair.iterrows():
    marker = {"CHEAP": "▲ BUY", "EXPENSIVE": "▼ SELL", "FAIR": "— FAIR"}.get(r["signal"], r["signal"])
    sf = f"{r['sigma_fair_pct']:.2f}" if r["sigma_fair_pct"] is not None else "N/A"
    ec = f"{r['ecart_pct']:+.2f}" if r["ecart_pct"] is not None else "N/A"
    print(f"  {r['tenor_label']:4s}  σ_mid={r['sigma_mid_pct']:.2f}%  σ_fair={sf}%  écart={ec}%  {marker}")

print(f"{'═' * 80}")
step("done")
print()

# ── CSV export ──
df_fair.to_csv("vol_fair_output.csv", index=False, encoding="utf-8-sig")