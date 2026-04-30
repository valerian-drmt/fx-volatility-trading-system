# Vol — modèles mathématiques

> Référence des indicateurs de vol calculés par `vol-engine`. Pour chaque
> modèle : formule, inputs, outputs, paramètres, comment l'engine s'en sert.
> Pour l'architecture système (containers, flux), voir
> [`VOL_SYSTEM.md`](./VOL_SYSTEM.md).

---

## Synthèse — quoi sert à quoi

| Indicateur | Mesure | Rôle |
|---|---|---|
| **Yang-Zhang RV** | P (réalisé) | Anchor : "ce que la vol a vraiment fait" |
| **GARCH(1,1)** | P (forecast) | Estimateur P legacy (fallback si HAR fail) |
| **HAR-RV** | P (forecast) | Estimateur P préféré (mixed-frequency, plus précis sur FX) |
| **VRP** | Q − P spread | Convertit forecast P → fair price Q (option pricing) |
| **SVI** | Smile fit | Lisse le smile par tenor, permet pricing inter-strikes + arb check |
| **SSVI** | Surface fit | Lisse la surface cross-tenors, garantit no-calendar-arb |
| **Signal CHEAP/FAIR/EXPENSIVE** | Verdict | Compare σ_mid (Q) à σ_fair (Q), génère le signal trading |

**Pipeline conceptuel** :

```
OHLC daily ──┬─▶ Yang-Zhang RV (full window)
             │
             ├─▶ HAR-RV per tenor       ──┐
             │                             ├─▶ σ_fair_p (P)
             └─▶ GARCH per tenor (legacy) ─┘            │
                                                        │ + VRP(regime, tenor)
                                                        ▼
                                                σ_fair_q (Q)
                                                        │
                                                        │  vs σ_mid (Q, IB IV ATM)
                                                        ▼
                                              Signal CHEAP/FAIR/EXPENSIVE

Chain options (IV par delta) ──┬─▶ SVI per tenor (smile lissé + butterfly check)
                                └─▶ SSVI surface-wide (calendar-arb check)
```

---

## 1. Yang-Zhang RV

**Module** : `src/core/vol/yang_zhang.py`. **Output** : `surface["_rv_full_pct"]`.

### Formule

```
σ_YZ² = σ²_overnight + k · σ²_open_close + (1 − k) · σ²_range

avec :
  k = 0.34 / (1.34 + (n+1)/(n−1))             (n = nb de bars)
  σ²_overnight  = var(log(O_t / C_{t-1}))     (gap)
  σ²_open_close = var(log(C_t / O_t))         (intraday close - open)
  σ²_range      = mean(Rogers-Satchell)        (high-low intraday)

σ_YZ_annualized = √(252 · σ_YZ²) × 100         (en %)
```

**Pourquoi YZ et pas close-to-close** : FX a peu de drift close-to-close mais
beaucoup de range intraday. YZ capture les 3 sources (overnight, drift,
range) → plus stable et moins biaisé que log-returns simples.

### Inputs

| Param | Source | Note |
|---|---|---|
| OHLC bars | `_fetch_ohlc()` → IB CONTFUT EUR `whatToShow=TRADES` | ~250 bars (1 an) |
| `window` | hardcoded `len(ohlc)−1` | full sample |

### Output

`_rv_full_pct: float | None` — vol annualisée en %. Typique EURUSD : **5-10%**.

### Pré-conditions

- `≥ 3 bars` requis. Sinon retourne `None`.
- Les bars doivent avoir `O, H, L, C` non-nuls.

### Usage downstream

- Anchor pour comparer aux forecasts P (HAR / GARCH) — divergence > 1.5% =
  signal régime change probable
- Input du `detect_regime()` (cf. §VRP) comme `vol_level_pct`
- Affiché dans l'UI Estimators tab pour visualisation
- Stocké dans `signals.rv` colonne (par cycle, pour audit historique)

### Configuration

Aucun param tunable — formule déterministe.

### Failure modes

- OHLC fetch fail (Error 162, perms IB) → `_rv_full_pct = None` → tout le
  pipeline P-measure tombe (HAR/GARCH/fair_q absents)

---

## 2. GARCH(1,1)

**Module** : `src/core/vol/garch.py`. **Output** : `surface["_garch"][tenor] = {sigma_model_pct: float}`.

### Formule

Modèle GARCH(1,1) classique :

```
σ²_t|t-1 = ω + α · r²_{t-1} + β · σ²_{t-1|t-2}      (1-step ahead)

avec :
  ω = constante (long-run variance × (1 − α − β))
  α = poids du choc précédent (~0.05-0.15 typique)
  β = persistance vol (~0.80-0.95 typique)
  r_t = log-return daily
```

### Term-structure

```
σ²_LR = ω / (1 − α − β)                            (long-run mean)
κ = −ln(α + β)                                      (decay rate)
σ²(T) = σ²_LR + (σ²_C − σ²_LR) · exp(−κ · T)        (mean reversion T-days ahead)

avec σ²_C = current conditional variance
```

### Blend empirique

```
σ_model = 0.5 · σ_GARCH(T) + 0.5 · σ_RV_realized
```

Le blend ramène le forecast vers la RV récente — empiriquement plus stable
sur petits comptes paper où GARCH peut sur-réagir.

### Inputs

| Param | Source | Note |
|---|---|---|
| `closes` | `ohlc["close"].to_numpy()` | ≥ 5 bars min |
| `tenor_t` | `{"1M": 1/12, ..., "6M": 0.5}` | mapping label → year fraction |
| `rv_full` | `_rv_full_pct` (Yang-Zhang) | pour le blend |

### Output

```python
{"1M": {"sigma_model_pct": 6.92}, "2M": {...}, ...}
```

### Pré-conditions

- ≥ 5 closes requis (fit fragile sinon)
- α + β doit converger sous 1 (clampé à 0.9999 ligne 55)

### Usage downstream

- Estimateur P fallback quand HAR fail (< 42 bars)
- Si `_har` absent, le `_fair_q` step utilise `_garch` comme P-measure
- Si `_fair_q` complètement absent, `_derive_signals` utilise `_garch` comme
  si c'était Q-measure (legacy fallback, économiquement faux mais évite
  signaux vides)

### Configuration

Pas encore tunable au runtime. Hardcoded :
- blend = 0.50
- emp_kappa = 2.0

### Failure modes

- Convergence non-cv → `_garch = {}` (skip silencieux ce cycle)
- Test `α + β >= 1` (random walk vol) → garch trivial, peu informatif

---

## 3. HAR-RV (Corsi 2009) — estimateur préféré

**Module** : `src/core/vol/har_rv.py`. **Output** : `surface["_har"][tenor] = {sigma_har_pct: float}`.

### Formule

Modèle Heterogeneous AR sur log-RV :

```
log(RV_{t+1}) = β₀ + β_d · log(RV_t)              (composante daily)
                  + β_w · log(RV_t^w)              (rolling 5 jours)
                  + β_m · log(RV_t^m)              (rolling 22 jours)
                  + ε

avec :
  RV_t ≈ |r_t| · √252       (proxy daily realized = |log-return| annualisé)
  RV_t^w = mean(RV_{t-4..t})
  RV_t^m = mean(RV_{t-21..t})
```

Fit par OLS dans l'espace log (variance stabilisé). Itéré `horizon_days` fois
pour le tenor cible.

### Pourquoi HAR > GARCH pour FX

Le marché FX a 3 horizons traders distincts (intraday/swing/macro), chacun
avec sa propre persistance vol. GARCH(1,1) suppose 1 seule persistance →
mal-spécifié. HAR capture les 3 explicitement → empiriquement bat GARCH sur
forecast term-structure.

### Inputs

| Param | Source | Note |
|---|---|---|
| `closes` | OHLC closes | ≥ 42 bars (= 22 monthly lag + 20 fit margin) |
| `tenor_days` | `{"1M": 30, "2M": 60, ..., "6M": 180}` | days to expiry |

### Output

```python
{"1M": {"sigma_har_pct": 6.85}, "2M": {...}, ...}
```

### Configuration

`VolTradingConfig.calibration.har_components = (1, 5, 22)` — daily/weekly/monthly
lags. Pas encore wired runtime (hardcoded dans har_rv.py pour l'instant).

### Usage downstream

- **Estimateur P-measure préféré** (default `signal.model_p = "har"`)
- Input principal du `_fair_q` step

### Failure modes

- < 42 bars OHLC disponibles → `_har = {}`, fallback GARCH
- OLS singular (variance log-RV trop faible) → exception caught, log warning

---

## 4. VRP (Variance Risk Premium) — conversion P → Q

**Module** : `src/core/vol/vrp.py`. **Output** : `surface["_fair_q"][tenor] = {sigma_fair_p_pct, vrp_vol_pts, sigma_fair_q_pct, regime}`.

### Pourquoi convertir P → Q

Le marché des options prix la vol en mesure **Q** (risk-neutral), pas en
mesure **P** (réalisée). Comparer un forecast P (HAR/GARCH = ce que la vol
fera vraiment) à une IV mid Q (ce que les options coûtent) directement est
**économiquement faux** : l'écart structurel attendu = VRP (Variance Risk
Premium = prime payée par les acheteurs d'options pour la couverture vol).

### Formule

```
σ_fair^Q = σ_fair^P + VRP(tenor, regime)

avec :
  σ_fair^P  ← HAR-RV ou GARCH selon `signal.model_p`
  VRP(tenor, regime) lookup table 6 × 3
  regime ← detect_regime(vol_level, vol_of_vol, term_slope)
```

### Détection de régime

```python
detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct) →
  "calm" | "stressed" | "pre_event"
```

Heuristique à seuils (cf. `vrp.py:86-113`). Renvoie un label (pas une
distribution). **Note importante** : c'est une heuristique, pas un GMM
calibré — annonce de "probabilités GMM" dans certains docs = aspirationnel.

### Table VRP par défaut (vol points)

| Tenor | calm | stressed | pre_event |
|---|---|---|---|
| 1M | 0.6 | 1.5 | 2.5 |
| 2M | 0.7 | 1.6 | 2.6 |
| 3M | 0.8 | 1.7 | 2.7 |
| 4M | 0.9 | 1.8 | 2.8 |
| 5M | 1.0 | 1.9 | 2.9 |
| 6M | 1.1 | 2.0 | 3.0 |

**Limite connue** : table hardcodée, pas estimée empiriquement. La phase
P1.2 du refactor prévoit `VolTradingConfig.signal.vrp_regime_override`
pour rendre tunable runtime, et P1.5 prévoit une estimation rolling
(régression sur features) au lieu du lookup statique.

### Output

```python
"_fair_q": {
  "1M": {
    "sigma_fair_p_pct": 6.85,        # input HAR ou GARCH
    "vrp_vol_pts": 0.6,              # add-on
    "sigma_fair_q_pct": 7.45,        # = p + vrp
    "regime": "calm"
  },
  "2M": {...}, ...
}
```

### Failure modes

- `_har` ET `_garch` absents (= no P-estimator) → `_fair_q = {}`
- Exception inattendue → log error, `_fair_q = {}`, signaux fallback legacy

### Limites importantes

- VRP en vol points (pas en variance points). Linéaire seulement
  localement — erreur d'ordre 2 quand vol level bouge significativement.
  Standard académique préfère variance pts. À reconsidérer en P1.2.
- Régime déterministe → pas de `predict_proba()` → pas de transition smooth
  entre régimes.

---

## 5. SVI per tenor

**Module** : `src/core/vol/svi.py`. **Output** : `surface["_svi"][tenor]`.

### Formule

Gatheral raw SVI sur la **total variance** :

```
w(k) = a + b · (ρ · (k − m) + √((k − m)² + σ²))

avec :
  k = log(K/F) = log-moneyness
  w(k) = σ²(k) · T = total variance
  a, b, ρ, m, σ = 5 params à fitter

Inversion : σ_iv(k) = √(w(k) / T)
```

### Interprétation des params

| Param | Sens |
|---|---|
| `a` | minimum total variance (mostly drives ATM level) |
| `b` | wings tightness (largeur du smile) |
| `ρ` | skew correlation ∈ [-1, 1] (≃ orientation gauche/droite) |
| `m` | log-moneyness centre (offset vs forward) |
| `σ` | ATM curvature |

EURUSD typique : `ρ < 0` (skew négatif, puts plus chers que calls). Et
`b > 0` toujours (sinon smile inversé physiquement absurde).

### No-arbitrage check

**Butterfly arbitrage** : la densité risk-neutrale `q(K)` doit être ≥ 0
partout. Test sur grille `k ∈ [-3, 3]` :

```
g(k) = (1 − k·w'(k)/(2·w(k)))² − (w'(k))²/4 · (1/w(k) + 1/4) + w''(k)/2

butterfly_g_min = min_k g(k)
butterfly_g_min < 0 ⇒ densité négative quelque part ⇒ smile arbitrage
```

Si violation : log warning, fit retourné quand même mais flagger côté UI.

### Inputs

5 obs (strike, IV) par tenor : 10dp, 25dp, atm, 25dc, 10dc (PCHIP-interpolés
depuis la chain IB).

### Output

```python
{
  "a": 0.0001, "b": 0.012, "rho": -0.73, "m": 0.001, "sigma": 0.031,
  "rmse_fit": 1.1e-05,         # erreur en w-space
  "butterfly_g_min": 0.0023    # ≥ 0 = OK
}
```

### Pré-conditions

- ≥ 3 strikes valides avec IV finie
- Bounds : `a ∈ [0, 2·max_w], b ∈ [0,1], ρ ∈ [-0.999, 0.999], m ∈ [-1, 1], σ ∈ [0.001, 2.0]`
- Fit via `scipy.optimize.least_squares` (trust-region)

### Limite — RMSE pas informatif

5 obs × 5 params = 0 DOF. Le fit est quasi-exact, RMSE ≈ 0 toujours. Donc
**ne jamais utiliser RMSE seul comme validation** — utiliser
`butterfly_g_min ≥ 0` comme test pertinent.

### Configuration

`VolTradingConfig.surface.{svi_rmse_max_warn, butterfly_check_grid}` —
définis mais pas wired runtime (hardcoded grille 100 points pour l'instant).

### Persistence

1 row par tenor dans `svi_params` table. Permet d'analyser la dérive des
params dans le temps (regime shifts visibles via param drift).

---

## 6. SSVI surface-wide

**Module** : `src/core/vol/ssvi.py`. **Output** : `surface["_ssvi"]`.

### Formule

SSVI Gatheral-Jacquier (2014) — modèle paramétrique de la surface entière :

```
w(k, θ) = (θ/2) · (1 + ρ·φ(θ)·k + √((φ(θ)·k + ρ)² + (1 − ρ²)))

avec :
  θ = ATM total variance par tenor
  φ(θ) = η · θ^(−γ)
  η, γ, ρ = 3 params (vs 5 par tenor pour SVI)
```

### Calendar-arbitrage condition

Garantie no-calendar-arb (= prix monotone en T) si :

```
2γ ≥ 1 − ρ²
```

Si violé : log warning. SSVI flagger `calendar_arb_free = False`.

### Inputs

Toutes les obs (T, K, IV) cross-tenors agrégées. ≥ 2 tenors avec ATM IV +
≥ 5 obs total min.

### Output

```python
{
  "eta": 1.69, "gamma": 0.41, "rho": -0.11,
  "rmse_fit": 1.6e-05,
  "calendar_arb_free": true
}
```

### SVI vs SSVI — quand utiliser quoi

- **SVI per tenor** : flexibilité maxi, capture des smile shapes très
  particuliers à un tenor donné. Risque : pas de garantie de cohérence
  inter-tenors (calendar arb possible).
- **SSVI surface** : régularité inter-tenors garantie, plus stable.
  Inconvénient : moins flexible si un tenor a un shape atypique (event
  pricing par exemple).

L'engine fitte les **deux** simultanément :
- SVI sert de "interpolation propre" pour pricer entre les pillars δ d'un
  tenor donné
- SSVI sert de "consistency check" + interpolation cross-tenors

### Persistence

1 row dans `ssvi_params` par cycle (pas par tenor).

---

## 7. Signaux CHEAP/FAIR/EXPENSIVE

**Code** : `src/services/vol/engine.py:_derive_signals()`. **Output** : `signals[]` array dans `latest_signals:EURUSD` + Postgres `signals` table.

### Logique

```python
ecart = sigma_mid_pct − sigma_fair_q_pct      # both in Q-measure

if abs(ecart) <= threshold_vol_pts:
    signal = "FAIR"
elif ecart > 0:
    signal = "EXPENSIVE"   # mid > fair → vol cher → vendre vol
else:
    signal = "CHEAP"       # mid < fair → vol cheap → acheter vol
```

### Inputs par tenor

| Champ | Source |
|---|---|
| `sigma_mid` | IV ATM IB × 100 (Q-measure) |
| `sigma_fair` | `_fair_q[tenor].sigma_fair_q_pct` (Q) si présent, sinon legacy GARCH-as-Q |
| `sigma_fair_p` | `_fair_q[tenor].sigma_fair_p_pct` (P) si présent |
| `vrp_vol_pts` | `_fair_q[tenor].vrp_vol_pts` si présent, sinon 0 |
| `rv` | `_rv_full_pct` (Yang-Zhang, full window) |

### Configuration

| Param | Default | Effet |
|---|---|---|
| `signal.threshold_vol_pts` | 1.0 | seuil au-delà duquel CHEAP/EXPENSIVE — sinon FAIR |
| `signal.model_p` | "har" | choix de l'estimateur P pour `_fair_q` (HAR ou GARCH) |

Les 2 fields sont **hot-reloadables** via `/api/v1/admin/config`.

### Sortie row

```python
{
  "underlying": "EURUSD",
  "tenor": "3M",
  "dte": 90,
  "sigma_mid": 5.96,        # Q
  "sigma_fair": 7.45,       # Q
  "sigma_fair_p": 6.85,     # P
  "vrp_vol_pts": 0.6,
  "ecart": -1.49,           # signed
  "signal_type": "CHEAP",   # |ecart|=1.49 > threshold=1.0
  "rv": 7.23                # P, Yang-Zhang
}
```

### Fallback path

Si `_fair_q` complètement absent (OHLC fetch fail) :
- `sigma_fair = _garch[tenor].sigma_model_pct` (legacy GARCH treated as Q)
- `sigma_fair_p = sigma_fair` (= P et Q sont identifiés)
- `vrp = 0`

C'est économiquement faux (P-measure utilisé comme Q) mais ça évite
d'avoir 0 signal quand IB historical est down. Comportement legacy R7.

### Failure modes par tenor

- ATM IV non-numeric (chain corrompue) → tenor skipped dans `signals`
- ni `_fair_q[tenor]` ni `_garch[tenor]` → tenor skipped (pas de fair de
  référence)

---

## 8. Pricing BS (Black-Scholes)

**Module** : `src/core/pricing/bs.py`. Pas un indicateur calculé en cycle,
mais utilitaire utilisé pour :
- Le pricing dans `_derive_signals` (mark-to-market)
- Les greeks dans `position_snapshots` côté execution-engine
- Endpoint `/api/v1/{price, greeks, iv}`

Approximation FX : zero rates (IRP négligé sur courts horizons).

### Fonctions exposées

| Fn | Signature | Output |
|---|---|---|
| `bs_price(F, K, T, σ, right)` | F=forward, K=strike, T=years, σ=annualized vol | option price |
| `bs_delta(F, K, T, σ, right)` | idem | delta |
| `bs_gamma(F, K, T, σ)` | idem (call=put) | gamma |
| `bs_vega(F, K, T, σ)` | idem | vega per 1.0 abs vol (à diviser par 100 pour vol pt) |
| `bs_theta(F, K, T, σ, right)` | idem | theta per day |

---

## Ordre d'exécution dans le cycle

```
1. Read spot F                   ← Redis
2. Fetch FOP chain               ← IB (greeks IV/delta sur ~216 contrats)
3. Fetch OHLC                    ← IB (cached 30 min)
4. PCHIP smile interpolation     → 5 pillars δ par tenor
5. Yang-Zhang RV                 → _rv_full_pct
6. GARCH per tenor               → _garch[tenor]
7. HAR-RV per tenor              → _har[tenor]
8. P→Q via VRP                   → _fair_q[tenor]    (input : _har or _garch)
9. SVI fit per tenor             → _svi[tenor]       (no-arb check)
10. SSVI fit surface             → _ssvi             (calendar-arb check)
11. Derive signals               → signals[]         (input : pillars + _fair_q)
12. Publish vol_update + DB events
13. Heartbeat
```

Étapes 4-10 sont indépendantes ; un fail isolé ne bloque pas les autres.
L'étape 11 (signals) consomme le résultat de 4 (pillars) + 8 (`_fair_q`)
ou fallback 6 (`_garch`).

---

## Ce qui n'est **pas** implémenté

Pour éviter de chercher dans le code pour rien — sont planifiés mais
absents au runtime :

- **PCA factor model** (PC1 level / PC2 slope / PC3 smile) — aucun fit,
  aucune table `pca_*`. Le moteur produit des signaux **par tenor**, pas
  des z-scores PC.
- **GMM regime probabilities** — `detect_regime()` est une heuristique à
  seuils, pas un GMM calibré. Output = label seul, pas distribution.
- **Event dampener / calendar macro** — pas de feed Bloomberg/ForexFactory,
  `event_dampener` toujours OFF.
- **VRP estimator empirique** — table hardcodée, pas de régression
  rolling.
- **Fair smile non-ATM** — `_fair_q` calcule fair Q **uniquement à l'ATM**.
  Pas de fair par delta_pillar → heatmap z-score (tenor × delta) non
  implémentable.
- **Trade preview multi-leg** — pas de `core/structures/`, pas de scenario
  engine, pas de sizing pipeline. Endpoint `/api/v1/vol/trade-preview`
  existe mais utilise un placeholder.
- **Position tracking lié aux signals** — `position_id` côté `trades` row
  pas linké au signal_id qui a déclenché l'entrée.

Quand l'une de ces pièces sera ajoutée, mettre à jour ce doc en conséquence.
