"""
vol_fair_step2.py
─────────────────
Étape 2 : Détermination de σ_fair(T) depuis σ_mid (étape 1)

Pipeline :
  A. Realized Vol (reqHistoricalData IB) + Risk Premium historique
  B. σ_model via GARCH(1,1) calibré sur returns historiques
  C. δ_book calculé depuis le portfolio réel (reqPositions IB)
  D. Combinaison : σ_fair = w1*(RV+RP) + w2*σ_model + δ_book

Input  : vol_mid_output.csv (output étape 1) + connexion IB live
Output : vol_fair_output.csv (une ligne par tenor)

Dépendances : ib_insync, numpy, scipy, pandas, arch
"""

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.optimize import minimize
from arch import arch_model
from ib_insync import IB, Contract, util
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# 0. PARAMÈTRES — w1/w2 ARBITRAIRES
#    À ajuster selon le régime de marché
# ─────────────────────────────────────────────

W1 = 0.65          # poids ancre RV+RP   (vue historique)
W2 = 0.35          # poids σ_model       (vue GARCH forward)
# w1 + w2 == 1 par construction

assert abs(W1 + W2 - 1.0) < 1e-9, "w1 + w2 doit valoir 1"

# Risk premium historique par tenor (IV - RV moyen observé, en %)
# À recalibrer sur données historiques réelles (12-24 mois)
RISK_PREMIUM = {
    "1W" : 0.80,
    "2W" : 0.90,
    "1M" : 1.20,
    "2M" : 1.35,
    "3M" : 1.50,
    "6M" : 1.60,
    "9M" : 1.65,
    "1Y" : 1.70,
    "18M": 1.75,
    "2Y" : 1.80,
}

# δ_book : aggressivité commerciale (α)
# 0.20% = ajustement max quand book à 100% de sa limite
ALPHA_BOOK = 0.20    # en %

# Vega limits par tenor (€/vol point) — fixées par le risk management
# En paper trading : valeurs arbitraires cohérentes avec taille du book
VEGA_LIMITS = {
    "1W" :  50_000,
    "2W" :  75_000,
    "1M" : 150_000,
    "2M" : 200_000,
    "3M" : 300_000,
    "6M" : 400_000,
    "9M" : 350_000,
    "1Y" : 300_000,
    "18M": 200_000,
    "2Y" : 150_000,
}

# IB connexion
IB_HOST    = "127.0.0.1"
IB_PORT    = 7497
CLIENT_ID  = 11

# Historique pour RV et GARCH
HIST_DAYS  = 252        # 1 an de données journalières
SYMBOL     = "EUR"
EXCHANGE   = "CME"
CURRENCY   = "USD"
MULTIPLIER = "125000"

# Mapping tenor label → T en années
TENOR_T = {
    "1W": 1/52, "2W": 2/52, "1M": 1/12, "2M": 2/12,
    "3M": 3/12, "6M": 6/12, "9M": 9/12, "1Y": 1.0,
    "18M": 1.5, "2Y": 2.0,
}

# Expiries IB associées aux labels (pour reqHistoricalData et positions)
TENOR_EXPIRY = {
    "3M" : "20250620",
    "6M" : "20250919",
    "9M" : "20251219",
    "1Y" : "20260320",
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
# 2. COUCHE A — REALIZED VOL (Yang-Zhang)
#    Input IB : reqHistoricalData → OHLC journalier
# ─────────────────────────────────────────────

def fetch_historical_ohlc(ib: IB) -> pd.DataFrame:
    """
    Récupère HIST_DAYS barres journalières OHLC sur le future EUR front-month.
    IB reqHistoricalData :
      - barSizeSetting = "1 day"
      - whatToShow     = "MIDPOINT"
      - durationStr    = f"{HIST_DAYS} D"
    """
    fut = Contract()
    fut.symbol   = SYMBOL
    fut.secType  = "FUT"
    fut.exchange = EXCHANGE
    fut.currency = CURRENCY
    fut.lastTradeDateOrContractMonth = list(TENOR_EXPIRY.values())[0]

    bars = ib.reqHistoricalData(
        fut,
        endDateTime    = "",          # maintenant
        durationStr    = f"{HIST_DAYS} D",
        barSizeSetting = "1 day",
        whatToShow     = "MIDPOINT",
        useRTH         = True,
        formatDate     = 1,
    )

    if not bars:
        raise ValueError("[IB] Pas de données historiques — marché fermé ou contrat expiré")

    df = util.df(bars)[["date","open","high","low","close"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    print(f"[IB] Historique : {len(df)} barres [{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}]")
    return df


def yang_zhang_rv(df: pd.DataFrame, window: int) -> float:
    """
    Estimateur Yang-Zhang de la realized vol.
    Plus efficace que close-to-close car utilise O, H, L, C.

    σ²_YZ = σ²_overnight + k·σ²_open + (1-k)·σ²_rs

    σ²_overnight = variance des log-returns overnight (close → open)
    σ²_open      = variance des log-returns intraday ouverts (open → close)
    σ²_rs        = Rogers-Satchell : E[ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O)]
    k            = 0.34 / (1.34 + (n+1)/(n-1))
    """
    df = df.tail(window).copy()
    n  = len(df)

    o = np.log(df["open"].values)
    h = np.log(df["high"].values)
    l = np.log(df["low"].values)
    c = np.log(df["close"].values)

    # Overnight return : close[t-1] → open[t]
    overnight = o[1:] - c[:-1]
    # Open-to-close return
    oc        = c[1:] - o[1:]

    sigma2_on   = np.var(overnight, ddof=1)
    sigma2_oc   = np.var(oc, ddof=1)

    # Rogers-Satchell (ne nécessite pas le close précédent)
    rs = (h[1:] - c[1:]) * (h[1:] - o[1:]) + (l[1:] - c[1:]) * (l[1:] - o[1:])
    sigma2_rs   = np.mean(rs)

    k           = 0.34 / (1.34 + (n + 1) / (n - 1))
    sigma2_yz   = sigma2_on + k * sigma2_oc + (1 - k) * sigma2_rs

    return float(np.sqrt(max(sigma2_yz, 0) * 252) * 100)   # annualisé, en %


def compute_rv_by_tenor(df_ohlc: pd.DataFrame) -> dict[str, float]:
    """
    Calcule la RV Yang-Zhang sur fenêtre glissante adaptée à chaque tenor.
    Fenêtre = max(21, T_en_jours_ouvrés)
    """
    rv_map = {}
    for tenor, T in TENOR_T.items():
        window = max(21, int(T * 252))
        window = min(window, len(df_ohlc) - 1)
        rv = yang_zhang_rv(df_ohlc, window)
        rv_map[tenor] = round(rv, 4)
        print(f"[RV] {tenor} (fenêtre {window}j) : RV = {rv:.2f}%")
    return rv_map


def compute_anchor(rv_map: dict, rp_map: dict) -> dict[str, float]:
    """Ancre_1(T) = RV(T) + RP(T)"""
    return {
        tenor: round(rv + rp_map.get(tenor, 1.50), 4)
        for tenor, rv in rv_map.items()
    }


# ─────────────────────────────────────────────
# 3. COUCHE B — σ_MODEL via GARCH(1,1)
#    Calibré sur returns log-returns historiques
# ─────────────────────────────────────────────

def fit_garch(df_ohlc: pd.DataFrame) -> dict:
    """
    Calibre un GARCH(1,1) sur les log-returns journaliers.
    Returns : {omega, alpha, beta, long_run_vol, current_vol}

    Modèle :
      r_t   = μ + ε_t
      ε_t   = σ_t · z_t,   z_t ~ N(0,1)
      σ²_t  = ω + α·ε²_{t-1} + β·σ²_{t-1}

    Vol long terme : σ_LR = sqrt(ω / (1 - α - β)) × sqrt(252) × 100
    Vol forward T  : σ²(T) = σ²_LR + (σ²_current - σ²_LR) · exp(-κ·T)
                     où κ = -ln(α + β)   [vitesse de mean-reversion]
    """
    returns = np.log(df_ohlc["close"] / df_ohlc["close"].shift(1)).dropna() * 100

    model  = arch_model(returns, vol="Garch", p=1, q=1, mean="Constant", dist="normal")
    result = model.fit(disp="off")

    omega  = result.params["omega"]
    alpha  = result.params["alpha[1]"]
    beta   = result.params["beta[1]"]
    mu     = result.params["mean"]

    # Vol annualisée courante (dernière σ_t du modèle)
    cond_var   = result.conditional_volatility.iloc[-1] ** 2   # variance journalière
    vol_current = np.sqrt(cond_var * 252) * 100                 # annualisée en %

    # Vol long terme
    persistence = alpha + beta
    if persistence >= 1.0:
        persistence = 0.999   # contrainte de stationnarité
    vol_lr = np.sqrt(omega / (1 - persistence) * 252) * 100

    # Vitesse de mean-reversion
    kappa = -np.log(persistence)

    print(f"[GARCH] ω={omega:.6f} α={alpha:.4f} β={beta:.4f} "
          f"persist={persistence:.4f} κ={kappa:.4f}")
    print(f"[GARCH] Vol courante={vol_current:.2f}% Vol LT={vol_lr:.2f}%")

    return {
        "omega"      : omega,
        "alpha"      : alpha,
        "beta"       : beta,
        "kappa"      : kappa,
        "vol_current": vol_current,
        "vol_lr"     : vol_lr,
        "persistence": persistence,
    }


def garch_forward_vol(garch_params: dict, T: float) -> float:
    """
    Vol forward GARCH à l'horizon T (en années) :
    σ²(T) = σ²_LR + (σ²_current - σ²_LR) · exp(-κ·T)
    """
    var_current = (garch_params["vol_current"] / 100) ** 2
    var_lr      = (garch_params["vol_lr"]      / 100) ** 2
    kappa       = garch_params["kappa"]

    var_T = var_lr + (var_current - var_lr) * np.exp(-kappa * T)
    return float(np.sqrt(max(var_T, 0)) * 100)   # en %


def compute_model_vols(garch_params: dict) -> dict[str, float]:
    """σ_model par tenor via GARCH forward"""
    model_map = {}
    for tenor, T in TENOR_T.items():
        vol = garch_forward_vol(garch_params, T)
        model_map[tenor] = round(vol, 4)
        print(f"[GARCH fwd] {tenor} (T={T:.3f}y) : σ_model = {vol:.2f}%")
    return model_map


# ─────────────────────────────────────────────
# 4. COUCHE C — δ_BOOK depuis portfolio IB
#    Input IB : reqPositions → positions ouvertes
#               reqMktData   → greeks (vega) de chaque option
# ─────────────────────────────────────────────

def get_portfolio_positions(ib: IB) -> list[dict]:
    """
    Récupère toutes les positions FOP (futures options) EUR ouvertes.
    reqPositions() retourne : account, contract, position, avgCost
    On filtre sur symbol=EUR, secType=FOP
    """
    positions = ib.reqPositions()
    ib.sleep(2)

    fop_positions = []
    for pos in positions:
        c = pos.contract
        if c.symbol == SYMBOL and c.secType == "FOP" and pos.position != 0:
            fop_positions.append({
                "conId"   : c.conId,
                "expiry"  : c.lastTradeDateOrContractMonth,
                "strike"  : c.strike,
                "right"   : c.right,
                "position": pos.position,       # nb de contrats (+ = long, - = short)
                "avgCost" : pos.avgCost,
            })

    print(f"[IB] Portfolio : {len(fop_positions)} positions FOP EUR ouvertes")
    return fop_positions


def get_option_greeks(ib: IB, pos: dict) -> dict:
    """
    Récupère les greeks IB (vega, delta, gamma, theta) pour une position.
    tick type 100 = option greeks (modelGreeks)
    Vega IB = variation de la valeur de l'option pour +1% de vol
              sur 1 contrat de MULTIPLIER notionnel
    """
    fop = Contract()
    fop.symbol     = SYMBOL
    fop.secType    = "FOP"
    fop.exchange   = EXCHANGE
    fop.currency   = CURRENCY
    fop.lastTradeDateOrContractMonth = pos["expiry"]
    fop.strike     = pos["strike"]
    fop.right      = pos["right"]
    fop.multiplier = MULTIPLIER
    fop.conId      = pos["conId"]

    ticker = ib.reqMktData(fop, "100", False, False)
    ib.sleep(2.5)
    ib.cancelMktData(fop)

    greeks = ticker.modelGreeks
    if greeks is None:
        return {"vega": 0.0, "delta": 0.0, "gamma": 0.0, "impliedVol": np.nan}

    # vega IB = en $ par contrat pour +1vol point (pas +1%)
    # On ramène en €/vol% : vega_IB × 100 × MULTIPLIER / 1000
    # (simplification : on travaille en unités IB directement)
    return {
        "vega"      : greeks.vega       if greeks.vega       else 0.0,
        "delta"     : greeks.delta      if greeks.delta      else 0.0,
        "gamma"     : greeks.gamma      if greeks.gamma      else 0.0,
        "impliedVol": greeks.impliedVol if greeks.impliedVol else np.nan,
    }


def expiry_to_tenor(expiry: str) -> str:
    """
    Associe une date d'expiration IB (YYYYMMDD) au label tenor le plus proche.
    Compare sur les expiries cibles définies dans TENOR_EXPIRY.
    """
    rev = {v: k for k, v in TENOR_EXPIRY.items()}
    if expiry in rev:
        return rev[expiry]
    # fallback : on prend le tenor dont l'expiry est la plus proche
    expiry_dt = pd.to_datetime(expiry)
    best, best_diff = "3M", float("inf")
    for tenor, exp in TENOR_EXPIRY.items():
        diff = abs((pd.to_datetime(exp) - expiry_dt).days)
        if diff < best_diff:
            best_diff, best = diff, tenor
    return best


def compute_book_vega(ib: IB, positions: list[dict]) -> dict[str, float]:
    """
    Calcule le vega net du book par tenor.

    Vega_net(T) = Σ_i [ position_i × vega_i × multiplier ]
                  (sommé sur toutes les options du tenor T)

    position > 0 → long → vega net positif → trader veut vendre
    position < 0 → short → vega net négatif → trader veut acheter

    Retourne : {tenor: vega_net_en_euros_par_vol_point}
    """
    vega_by_tenor: dict[str, float] = {t: 0.0 for t in TENOR_EXPIRY}

    if not positions:
        print("[Book] Aucune position FOP — δ_book = 0 pour tous les tenors")
        return vega_by_tenor

    for pos in positions:
        greeks = get_option_greeks(ib, pos)
        tenor  = expiry_to_tenor(pos["expiry"])

        # Vega en $ par contrat par vol point (IB convention)
        # × position (nb contrats signés) × multiplier / 100 pour avoir en €/%
        vega_contract = greeks["vega"]                        # $/vol point/contrat
        vega_position = vega_contract * pos["position"] * float(MULTIPLIER) / 100.0

        vega_by_tenor[tenor] = vega_by_tenor.get(tenor, 0.0) + vega_position

        print(f"[Book] {pos['expiry']} K={pos['strike']} {pos['right']} "
              f"qty={pos['position']:+.0f} "
              f"vega={vega_contract:.4f} "
              f"contrib={vega_position:+.0f}€/vol%")

    for tenor, vnet in vega_by_tenor.items():
        print(f"[Book] Vega net {tenor} = {vnet:+,.0f} €/vol%")

    return vega_by_tenor


def compute_delta_book(vega_by_tenor: dict[str, float]) -> dict[str, float]:
    """
    δ_book(T) = −α × Vega_net(T) / Vega_limit(T)

    Signe :
      Vega_net > 0 (long vol) → δ_book < 0 → on marque en dessous du mid → vendeur
      Vega_net < 0 (short vol) → δ_book > 0 → on marque au-dessus → acheteur

    Clampé à ±α pour éviter des ajustements extrêmes.
    """
    delta_book = {}
    for tenor, vnet in vega_by_tenor.items():
        limit = VEGA_LIMITS.get(tenor, 300_000)
        ratio = vnet / limit                              # ratio signé [-1, +1] théoriquement
        ratio = max(-1.0, min(1.0, ratio))               # clampage sécurité
        db    = -ALPHA_BOOK * ratio
        delta_book[tenor] = round(db, 5)
        print(f"[δ_book] {tenor} : Vnet={vnet:+,.0f} limit={limit:,} "
              f"ratio={ratio:+.3f} → δ_book={db:+.4f}%")
    return delta_book


# ─────────────────────────────────────────────
# 5. COMBINAISON FINALE — σ_fair
# ─────────────────────────────────────────────

def compute_sigma_fair(
    df_step1   : pd.DataFrame,
    anchor_map : dict[str, float],
    model_map  : dict[str, float],
    dbook_map  : dict[str, float],
) -> pd.DataFrame:
    """
    σ_fair(T) = W1 * Ancre_1(T) + W2 * σ_model(T) + δ_book(T)

    Appliqué à la colonne σ_ATM% de l'étape 1.
    Pour les wings (25Δ, 10Δ) : on applique le même ajustement ATM
    car RV et modèle ne donnent qu'une vol ATM forward.
    L'ajustement du smile (RR, BF) reste celui du marché (étape 1).
    """
    rows = []
    for _, row in df_step1.iterrows():
        tenor = row["tenor"]

        sigma_mid_atm = row.get("σ_ATM%", np.nan)
        ancre         = anchor_map.get(tenor, np.nan)
        sigma_model   = model_map.get(tenor, np.nan)
        db            = dbook_map.get(tenor, 0.0)

        if np.isnan(ancre) or np.isnan(sigma_model):
            sigma_fair = np.nan
        else:
            sigma_fair = W1 * ancre + W2 * sigma_model + db

        ecart = round(sigma_fair - sigma_mid_atm, 4) if not np.isnan(sigma_fair) else np.nan

        if   ecart < -0.15: signal = "VENDEUR"
        elif ecart > +0.15: signal = "ACHETEUR"
        else:               signal = "NEUTRE"

        r = row.to_dict()
        r.update({
            "RV%"        : round(ancre - RISK_PREMIUM.get(tenor, 1.5), 4) if not np.isnan(ancre) else np.nan,
            "RP%"        : RISK_PREMIUM.get(tenor, 1.5),
            "Ancre1%"    : round(ancre, 4),
            "σ_model%"   : round(sigma_model, 4),
            "w1"         : W1,
            "w2"         : W2,
            "δ_book%"    : db,
            "σ_fair%"    : round(sigma_fair, 4),
            "écart%"     : ecart,
            "signal"     : signal,
        })
        rows.append(r)

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# 6. OUTPUT TABLE
# ─────────────────────────────────────────────

def build_output_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "tenor",
        "σ_ATM%",        # mid marché (étape 1)
        "RV%",           # realized vol Yang-Zhang
        "RP%",           # risk premium historique
        "Ancre1%",       # RV + RP
        "σ_model%",      # GARCH forward
        "w1", "w2",
        "δ_book%",       # ajustement portfolio
        "σ_fair%",       # output final
        "écart%",        # σ_fair − σ_ATM
        "signal",        # VENDEUR / ACHETEUR / NEUTRE
    ]
    cols = [c for c in cols if c in df.columns]
    return df[cols]


# ─────────────────────────────────────────────
# 7. PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

def run_step2(step1_csv: str = "vol_mid_output.csv") -> pd.DataFrame:
    """
    Exécute l'étape 2 complète.
    Retourne df_fair (une ligne par tenor).
    """

    # ── Chargement output étape 1
    df_step1 = pd.read_csv(step1_csv)
    print(f"[Step2] Étape 1 chargée : {len(df_step1)} tenors")
    print(df_step1[["tenor","σ_ATM%","RR25%","BF25%"]].to_string(index=False))

    ib = connect_ib()

    print("\n" + "─"*50)
    print("COUCHE A — Realized Vol (Yang-Zhang)")
    print("─"*50)
    df_ohlc   = fetch_historical_ohlc(ib)
    rv_map    = compute_rv_by_tenor(df_ohlc)
    anchor_map = compute_anchor(rv_map, RISK_PREMIUM)

    print("\n" + "─"*50)
    print("COUCHE B — GARCH(1,1) forward vol")
    print("─"*50)
    garch_params = fit_garch(df_ohlc)
    model_map    = compute_model_vols(garch_params)

    print("\n" + "─"*50)
    print("COUCHE C — δ_book depuis portfolio IB")
    print("─"*50)
    positions    = get_portfolio_positions(ib)
    vega_by_t    = compute_book_vega(ib, positions)
    dbook_map    = compute_delta_book(vega_by_t)

    ib.disconnect()
    print("\n[IB] Déconnecté")

    print("\n" + "─"*50)
    print("COMBINAISON — σ_fair")
    print("─"*50)
    df_fair  = compute_sigma_fair(df_step1, anchor_map, model_map, dbook_map)
    df_output = build_output_table(df_fair)

    return df_output


# ─────────────────────────────────────────────
# 8. ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":

    df_output = run_step2(step1_csv="vol_mid_output.csv")

    pd.set_option("display.float_format", "{:.4f}".format)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 180)

    print("\n" + "═"*70)
    print("STEP 2 OUTPUT — σ_fair par tenor")
    print(f"Paramètres : w1={W1} w2={W2} α_book={ALPHA_BOOK}%")
    print("═"*70)
    print(df_output.to_string(index=False))

    df_output.to_csv("vol_fair_output.csv", index=False)
    print("\n[OK] Export : vol_fair_output.csv")

    # ── Résumé signals
    print("\n" + "─"*40)
    print("SIGNALS")
    print("─"*40)
    for _, r in df_output.iterrows():
        sign = "▼" if r["signal"] == "VENDEUR" else ("▲" if r["signal"] == "ACHETEUR" else "—")
        print(f"  {r['tenor']:4s}  σ_mid={r['σ_ATM%']:.2f}%  "
              f"σ_fair={r['σ_fair%']:.2f}%  "
              f"écart={r['écart%']:+.2f}%  {sign} {r['signal']}")
