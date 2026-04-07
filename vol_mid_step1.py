"""
vol_mid_step1.py
────────────────
Étape 1 : Détermination de σ_mid(K, T) depuis IB Gateway
Pipeline : collect → filter → invert BS → reconstruct pilliers → output DataFrame

Dépendances : ib_insync, numpy, scipy, pandas
IB Gateway   : TWS ou IB Gateway tournant sur 127.0.0.1:7497 (paper) ou 4001 (live)
Sous-jacent  : EUR futures options CME (FOP)
"""

import time
import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import brentq, newton
from ib_insync import IB, Contract, util

# ─────────────────────────────────────────────
# 0. PARAMÈTRES GLOBAUX
# ─────────────────────────────────────────────

IB_HOST      = "127.0.0.1"
IB_PORT      = 7497          # 7497 = paper trading ; 4001 = IB Gateway live
CLIENT_ID    = 10
SYMBOL       = "EUR"
EXCHANGE     = "CME"
CURRENCY     = "USD"
MULTIPLIER   = "125000"      # 1 contrat = 125 000 EUR

# Filtres liquidité
MIN_VOLUME        = 5        # volume journalier minimum
MAX_BA_SPREAD_PCT = 0.20     # spread bid/ask max en % du ask (20%)
MAX_OTM_PCT       = 0.08     # on garde les strikes dans ±8% du spot

# Tenors cibles (codes IB : YYYYMM pour monthly, YYYYMMDD pour weekly)
# IMM dates EUR futures 2025 : mars, juin, sept, déc
TARGET_EXPIRATIONS = [
    "20250321",   # 1M  ~ mars 2025
    "20250620",   # 3M  ~ juin 2025
    "20250919",   # 6M  ~ sept 2025
    "20251219",   # 1Y  ~ déc 2025
]

# Labels lisibles associés
TENOR_LABELS = {
    "20250321": "3M",
    "20250620": "6M",
    "20250919": "9M",
    "20251219": "1Y",
}


# ─────────────────────────────────────────────
# 1. CONNEXION IB
# ─────────────────────────────────────────────

def connect_ib() -> IB:
    ib = IB()
    ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID)
    print(f"[IB] Connecté — server version {ib.serverVersion()}")
    return ib


# ─────────────────────────────────────────────
# 2. RÉCUPÉRATION DES INPUTS IB
#    INPUT A : spot EUR/USD
#    INPUT B : grille strikes/expirations disponibles (reqSecDefOptParams)
#    INPUT C : implied vol par strike (reqMktData tick 24)
# ─────────────────────────────────────────────

def get_spot(ib: IB) -> float:
    """
    INPUT A — Prix spot EUR/USD
    Proxy : mid du future front-month EUR CME
    tick type 4 = last price
    """
    fut = Contract()
    fut.symbol   = SYMBOL
    fut.secType  = "FUT"
    fut.exchange = EXCHANGE
    fut.currency = CURRENCY
    fut.lastTradeDateOrContractMonth = TARGET_EXPIRATIONS[0]

    ticker = ib.reqMktData(fut, "", False, False)
    ib.sleep(2)
    ib.cancelMktData(fut)

    spot = ticker.last if ticker.last and ticker.last > 0 else ticker.close
    if not spot or spot <= 0:
        raise ValueError("[IB] Spot EUR non récupéré — vérifier que le marché est ouvert")
    print(f"[IB] Spot EUR/USD = {spot:.5f}")
    return spot


def get_option_chain(ib: IB, expiry: str) -> list[float]:
    """
    INPUT B — Liste des strikes disponibles pour un tenor donné
    Utilise reqSecDefOptParams → renvoie strikes[] et expirations[]
    """
    # On récupère d'abord le conId du future sous-jacent
    fut = Contract()
    fut.symbol   = SYMBOL
    fut.secType  = "FUT"
    fut.exchange = EXCHANGE
    fut.currency = CURRENCY
    fut.lastTradeDateOrContractMonth = expiry

    details = ib.reqContractDetails(fut)
    if not details:
        print(f"[IB] Pas de détails contrat pour {expiry}")
        return []
    con_id = details[0].contract.conId

    # Récupération des paramètres de la chain
    chains = ib.reqSecDefOptParams(
        underlyingSymbol  = SYMBOL,
        futFopExchange    = EXCHANGE,
        underlyingSecType = "FUT",
        underlyingConId   = con_id
    )

    if not chains:
        print(f"[IB] Pas de chain options pour {expiry}")
        return []

    # On prend la première chain correspondant à CME
    chain = next((c for c in chains if c.exchange == EXCHANGE), chains[0])
    strikes = sorted(chain.strikes)
    print(f"[IB] Chain {expiry} : {len(strikes)} strikes "
          f"[{min(strikes):.4f} → {max(strikes):.4f}]")
    return strikes


def get_implied_vol_single(ib: IB, strike: float, expiry: str, right: str,
                            timeout: float = 3.0) -> dict:
    """
    INPUT C — Implied vol IB pour un strike donné
    tick type 24 = impliedVolatility (calculée par IB via BS inversion)
    tick type 13 = modelOptPrice (prix modèle IB)
    Renvoie : {iv, bid, ask, last, volume, delta}
    """
    fop = Contract()
    fop.symbol     = SYMBOL
    fop.secType    = "FOP"
    fop.exchange   = EXCHANGE
    fop.currency   = CURRENCY
    fop.lastTradeDateOrContractMonth = expiry
    fop.strike     = strike
    fop.right      = right
    fop.multiplier = MULTIPLIER

    # tick types :
    # 100 = option greeks (delta, gamma, vega, theta, IV)
    # ""  = tous les ticks standard (bid, ask, last, volume)
    ticker = ib.reqMktData(fop, "100", False, False)
    ib.sleep(timeout)
    ib.cancelMktData(fop)

    iv     = ticker.impliedVolatility   # mid IV calculée par IB
    delta  = ticker.modelGreeks.delta   if ticker.modelGreeks else None
    bid    = ticker.bid
    ask    = ticker.ask
    last   = ticker.last
    volume = ticker.volume

    return {
        "iv"    : iv     if iv     and iv > 0     else np.nan,
        "bid"   : bid    if bid    and bid > 0    else np.nan,
        "ask"   : ask    if ask    and ask > 0    else np.nan,
        "last"  : last   if last   and last > 0   else np.nan,
        "volume": volume if volume and volume >= 0 else 0,
        "delta" : delta,
    }


# ─────────────────────────────────────────────
# 3. COLLECTE COMPLÈTE PAR TENOR
# ─────────────────────────────────────────────

def collect_chain_ivs(ib: IB, expiry: str, spot: float, T: float) -> pd.DataFrame:
    """
    Pour un tenor donné :
    1. Récupère tous les strikes disponibles
    2. Filtre sur ±MAX_OTM_PCT autour du spot
    3. Requête IV + greeks pour call et put à chaque strike
    4. Retourne DataFrame brut (avant filtrage liquidité)
    """
    all_strikes = get_option_chain(ib, expiry)
    if not all_strikes:
        return pd.DataFrame()

    # Filtre géographique strikes
    lo = spot * (1 - MAX_OTM_PCT)
    hi = spot * (1 + MAX_OTM_PCT)
    strikes = [k for k in all_strikes if lo <= k <= hi]
    print(f"[IB] {expiry} : {len(strikes)} strikes retenus sur ±{MAX_OTM_PCT*100:.0f}%")

    rows = []
    for strike in strikes:
        for right in ["C", "P"]:
            data = get_implied_vol_single(ib, strike, expiry, right)
            moneyness = np.log(strike / spot)
            rows.append({
                "expiry"   : expiry,
                "tenor"    : TENOR_LABELS.get(expiry, expiry),
                "T"        : T,
                "strike"   : strike,
                "right"    : right,
                "moneyness": round(moneyness, 5),
                "iv_raw"   : data["iv"],
                "bid"      : data["bid"],
                "ask"      : data["ask"],
                "last"     : data["last"],
                "volume"   : data["volume"],
                "delta_ib" : data["delta"],
            })

    df = pd.DataFrame(rows)
    print(f"[IB] {expiry} : {len(df)} obs collectées (calls + puts)")
    return df


# ─────────────────────────────────────────────
# 4. FILTRE LIQUIDITÉ
# ─────────────────────────────────────────────

def filter_liquid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Critères de filtre :
    - iv_raw non NaN
    - bid > 0 et ask > 0
    - spread bid/ask < MAX_BA_SPREAD_PCT × ask
    - volume >= MIN_VOLUME
    Ajoute colonne 'liquid' (bool) et 'ba_spread_pct'
    """
    df = df.copy()

    has_iv   = df["iv_raw"].notna() & (df["iv_raw"] > 0)
    has_ba   = df["bid"].notna() & df["ask"].notna() & (df["bid"] > 0) & (df["ask"] > 0)
    ba_pct   = (df["ask"] - df["bid"]) / df["ask"].replace(0, np.nan)
    tight_ba = ba_pct < MAX_BA_SPREAD_PCT
    liq_vol  = df["volume"] >= MIN_VOLUME

    df["ba_spread_pct"] = ba_pct.round(4)
    df["liquid"]        = has_iv & has_ba & tight_ba & liq_vol

    n_liq = df["liquid"].sum()
    print(f"[Filter] {n_liq}/{len(df)} strikes liquides retenus")
    return df


# ─────────────────────────────────────────────
# 5. CALCUL IV MID (moyenne call/put à strike identique)
# ─────────────────────────────────────────────

def compute_mid_iv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pour chaque strike liquide : moyenne des IV call et put
    → σ_mid(K) = ½ [IV_call(K) + IV_put(K)]
    (put-call parity implique qu'elles devraient être égales ;
     la moyenne réduit le bruit de microstructure)
    Retourne DataFrame agrégé par (expiry, strike)
    """
    liq = df[df["liquid"]].copy()

    calls = liq[liq["right"] == "C"][["expiry","tenor","T","strike","moneyness","iv_raw","delta_ib"]].rename(
        columns={"iv_raw":"iv_call","delta_ib":"delta_call"})
    puts  = liq[liq["right"] == "P"][["expiry","strike","iv_raw","delta_ib"]].rename(
        columns={"iv_raw":"iv_put","delta_ib":"delta_put"})

    merged = calls.merge(puts, on=["expiry","strike"], how="inner")
    merged["iv_mid"]    = ((merged["iv_call"] + merged["iv_put"]) / 2).round(5)
    merged["iv_spread"] = (merged["iv_call"] - merged["iv_put"]).abs().round(5)

    return merged.sort_values(["expiry","strike"]).reset_index(drop=True)


# ─────────────────────────────────────────────
# 6. RECONSTRUCTION DES PILLIERS Δ
#    Conversion strike → delta (Newton-Raphson)
#    Puis interpolation sur les 5 pilliers standard
# ─────────────────────────────────────────────

def bs_delta(S: float, K: float, T: float, sigma: float, right: str) -> float:
    """Delta Black-Scholes (taux = 0, simplifié pour FX futures)"""
    if sigma <= 0 or T <= 0:
        return np.nan
    d1  = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    phi = 1 if right == "C" else -1
    return phi * norm.cdf(phi * d1)


def strike_to_delta(S: float, K: float, T: float, sigma: float) -> float:
    """Retourne le delta call (positif) pour un strike donné"""
    return bs_delta(S, K, T, sigma, "C")


def reconstruct_pillars(df_mid: pd.DataFrame, spot: float) -> pd.DataFrame:
    """
    Pour chaque (expiry, strike) liquide :
    - calcule le delta BS via la iv_mid
    - classe le strike dans les buckets Δ standards
    Retourne un DataFrame des pilliers reconstruits par tenor
    """
    TARGET_DELTAS = {
        "10dp": -0.10,
        "25dp": -0.25,
        "atm" :  0.00,   # delta-neutral straddle ≈ 0.50 call / -0.50 put
        "25dc": +0.25,
        "10dc": +0.10,
    }

    rows = []
    for expiry, grp in df_mid.groupby("expiry"):
        T     = grp["T"].iloc[0]
        tenor = grp["tenor"].iloc[0]

        # Calcul delta pour chaque strike
        deltas = grp.apply(
            lambda r: strike_to_delta(spot, r["strike"], T, r["iv_mid"]), axis=1
        )
        grp = grp.copy()
        grp["delta_bs"] = deltas.values

        # Pour chaque pillier cible, on trouve le strike le plus proche en delta
        pillar_row = {"expiry": expiry, "tenor": tenor, "T": T, "spot": spot}
        for label, target_d in TARGET_DELTAS.items():
            if label == "atm":
                # ATM = strike le plus proche du spot
                idx = (grp["strike"] - spot).abs().idxmin()
            else:
                idx = (grp["delta_bs"] - target_d).abs().idxmin()

            row_found = grp.loc[idx]
            pillar_row[f"K_{label}"]  = row_found["strike"]
            pillar_row[f"iv_{label}"] = row_found["iv_mid"]
            pillar_row[f"d_{label}"]  = round(row_found["delta_bs"], 4)

        # Reconstruction RR et BF depuis les pilliers
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


# ─────────────────────────────────────────────
# 7. OUTPUT — TABLEAU FINAL
# ─────────────────────────────────────────────

def build_output_table(pillars: pd.DataFrame) -> pd.DataFrame:
    """
    Table de sortie de l'étape 1 :
    Colonnes : tenor | spot | σ_ATM | RR25 | BF25 | RR10 | BF10
               K_10dp | K_25dp | K_atm | K_25dc | K_10dc
               iv_10dp | iv_25dp | iv_atm | iv_25dc | iv_10dc
    Toutes les vols en % (×100)
    """
    out = pillars.copy()
    vol_cols = ["iv_10dp","iv_25dp","iv_atm","iv_25dc","iv_10dc","RR25","BF25","RR10","BF10"]
    for c in vol_cols:
        if c in out.columns:
            out[c] = (out[c] * 100).round(3)

    display_cols = [
        "tenor","spot",
        "iv_atm","RR25","BF25","RR10","BF10",
        "iv_10dp","iv_25dp","iv_25dc","iv_10dc",
        "K_10dp","K_25dp","K_atm","K_25dc","K_10dc",
    ]
    display_cols = [c for c in display_cols if c in out.columns]
    return out[display_cols].rename(columns={
        "iv_atm" : "σ_ATM%",
        "iv_10dp": "σ_10Δp%",
        "iv_25dp": "σ_25Δp%",
        "iv_25dc": "σ_25Δc%",
        "iv_10dc": "σ_10Δc%",
        "RR25"   : "RR25%",
        "BF25"   : "BF25%",
        "RR10"   : "RR10%",
        "BF10"   : "BF10%",
    })


# ─────────────────────────────────────────────
# 8. PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

def run_step1() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Exécute l'étape 1 complète.
    Retourne :
      df_raw     — données brutes IB (une ligne par strike × right)
      df_mid     — IV mid filtrée (une ligne par strike liquide)
      df_output  — tableau pilliers Δ (une ligne par tenor)
    """
    ib = connect_ib()

    # ── INPUT A : Spot
    spot = get_spot(ib)

    # ── Calcul des T en années (approximation)
    # En production : utiliser les vraies dates business
    T_map = {
        "20250321": 3/12,
        "20250620": 6/12,
        "20250919": 9/12,
        "20251219": 12/12,
    }

    all_raw  = []
    all_mid  = []

    for expiry in TARGET_EXPIRATIONS:
        T = T_map[expiry]
        print(f"\n{'─'*50}")
        print(f"Tenor {TENOR_LABELS[expiry]} — expiry {expiry} — T={T:.3f}y")
        print('─'*50)

        # ── INPUT B + C : chain + implied vols
        df_raw = collect_chain_ivs(ib, expiry, spot, T)
        if df_raw.empty:
            continue

        # Filtre liquidité
        df_raw = filter_liquid(df_raw)
        all_raw.append(df_raw)

        # IV mid
        df_mid = compute_mid_iv(df_raw)
        all_mid.append(df_mid)

        time.sleep(1)   # throttle IB requests

    ib.disconnect()
    print("\n[IB] Déconnecté")

    if not all_raw:
        raise RuntimeError("Aucune donnée collectée — vérifier connexion IB et marchés ouverts")

    df_raw_full = pd.concat(all_raw, ignore_index=True)
    df_mid_full = pd.concat(all_mid, ignore_index=True)

    # ── Reconstruction pilliers Δ
    pillars    = reconstruct_pillars(df_mid_full, spot)

    # ── Output table
    df_output  = build_output_table(pillars)

    return df_raw_full, df_mid_full, df_output


# ─────────────────────────────────────────────
# 9. ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":

    df_raw, df_mid, df_output = run_step1()

    pd.set_option("display.float_format", "{:.4f}".format)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 160)

    print("\n" + "═"*60)
    print("STEP 1 OUTPUT — σ_mid par tenor et pillier Δ")
    print("═"*60)
    print(df_output.to_string(index=False))

    print("\n" + "═"*60)
    print(f"DONNÉES BRUTES IB — {len(df_raw)} observations")
    print("═"*60)
    print(df_raw[["tenor","strike","right","iv_raw","bid","ask",
                  "volume","ba_spread_pct","liquid"]].to_string(index=False))

    print("\n" + "═"*60)
    print(f"IV MID FILTRÉE — {len(df_mid)} strikes liquides")
    print("═"*60)
    print(df_mid[["tenor","strike","moneyness","iv_call","iv_put",
                  "iv_mid","iv_spread"]].to_string(index=False))

    # Export CSV
    df_output.to_csv("vol_mid_output.csv", index=False)
    df_raw.to_csv("vol_mid_raw.csv", index=False)
    print("\n[OK] Exports : vol_mid_output.csv, vol_mid_raw.csv")
