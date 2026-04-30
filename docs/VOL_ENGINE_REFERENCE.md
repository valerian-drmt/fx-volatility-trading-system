# Vol-engine — Référence technique

> Manuel de référence du container `fxvol-vol-engine` : inputs, outputs,
> modèles mathématiques, paramètres de configuration, ce qu'il faut surveiller.
> Cible : définir précisément les tables, charts et panels à afficher dans
> l'UI dev `/dev/vol`.
>
> Tout est tiré du code (`src/services/vol/`, `src/core/vol/`, `src/core/
> config/vol_params.py`) et des docs `VOL_TRADING_USER_GUIDE.md` +
> `VOL_MODEL_REFACTOR_PLAN.md`.

---

## 1. Vue d'ensemble

`vol-engine` tourne en boucle infinie avec un cycle nominal de **180s**. Chaque
cycle produit une **surface de volatilité fittée** + des **signaux trading**
CHEAP/FAIR/EXPENSIVE par tenor.

```
   ┌──────────────────────────────────────────────────────┐
   │  CYCLE 180s                                          │
   ├──────────────────────────────────────────────────────┤
   │  1. read latest_spot:EURUSD                          │
   │  2. fetch FOP chain via IB (~30s, 6 tenors × 36)     │
   │  3. fetch OHLC daily (cached 30min)                  │
   │  4. compute models : RV → HAR/GARCH → fair_q (VRP)   │
   │  5. fit SVI per tenor + SSVI surface                 │
   │  6. derive signals (mid Q vs fair Q)                 │
   │  7. SET latest_vol_surface + PUBLISH vol_update      │
   │  8. fan to db-writer → 4 tables persistées           │
   └──────────────────────────────────────────────────────┘
```

Si une étape échoue, le cycle continue avec ce qui marche (fallback gracieux,
cf. §7).

---

## 2. Inputs — d'où viennent les données

### Redis lu

| Key | Producer | Usage |
|---|---|---|
| `latest_spot:EURUSD` | `market-data` | Spot F (forward FX) ; cycle skip si absent |
| `config:changed` (pub/sub) | `api` admin endpoint | Hot-reload des params signal |

### IB lu

| Appel | Cadence | Donnée |
|---|---|---|
| `discover_chains()` + `_qualify_tenor_strikes()` + `reqMktData(genericTickList="100")` | À chaque cycle | Greeks IB (IV, delta) sur ~36 strikes × 6 tenors = ~216 contrats. Concurrency=3. ~10-30s wall time. |
| `reqHistoricalDataAsync(CONTFUT EUR, "1 Y", "1 day", whatToShow="TRADES")` | Cache 30 min | 250 bars OHLC quotidiens du futures EUR continu |

---

## 3. Outputs — ce qui est produit

### Redis écrit

| Key | TTL | Schéma | Consumer |
|---|---|---|---|
| `latest_vol_surface:EURUSD` | 600s | `{symbol, timestamp, surface}` cf. §5 | `/api/v1/vol/*`, frontend, risk-engine |
| `latest_signals:EURUSD` | 600s | `{symbol, timestamp, signals[]}` | `/api/v1/signals` |
| `heartbeat:vol_engine` | 300s | ISO-8601 timestamp | healthcheck, EngineHealth tab |
| `vol_update` (channel) | — | duplicate du surface payload | api WS bridge → `/ws/vol` |

### Postgres écrit (via db-writer)

| Table | Cardinalité | Contenu principal |
|---|---|---|
| `vol_surfaces` | 1 row / cycle | `surface_data` JSONB complet (toutes les sections) |
| `signals` | N rows / cycle (1 par tenor) | sigma_mid/fair, ecart, signal_type |
| `svi_params` | N rows / cycle (1 par tenor) | a, b, ρ, m, σ, rmse_fit, butterfly_g_min |
| `ssvi_params` | 1 row / cycle | η, γ, ρ, rmse_fit, calendar_arb_free |

---

## 4. Indicateurs calculés

Tous résident dans le payload `surface` (clés sous-prefixées `_`). Ordre
chronologique du cycle :

### 4.1 Yang-Zhang RV (`_rv_full_pct`)

Mesure de **volatilité réalisée P-measure** sur la fenêtre OHLC (typiquement 1 an).

```
σ_YZ = √[252 × (σ²_overnight + k·σ²_open_close + (1−k)·σ²_range)] × 100
```

Décompose la vol en : gaps overnight + open-close + range intraday. Plus
robuste que close-to-close pour FX (qui a peu de close-to-close drift mais
beaucoup de range).

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/yang_zhang.py` |
| **Input** | OHLC bars |
| **Min bars** | ≥ 3 |
| **Output** | float pct (annualisé) |
| **Config** | aucun (déterministe) |
| **Si fail** | `_rv_full_pct = None` → no anchor RV pour les autres steps |

### 4.2 GARCH(1,1) (`_garch`)

**Forecast P-measure** par tenor via modèle volatilité conditionnelle.

```
σ²_t|t-1 = ω + α·r²_{t-1} + β·σ²_{t-1|t-2}        (1-step ahead)
σ²(T) = σ²_LR + (σ²_C − σ²_LR)·exp(−κT)            (terme structure)
σ_model = 0.5·σ_GARCH + 0.5·σ_RV_empirique         (blend)
```

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/garch.py` |
| **Input** | closes daily, tenor_t = `{label → year fraction}` |
| **Min bars** | ≥ 5 |
| **Output** | `_garch[tenor] = {sigma_model_pct: float}` |
| **Blend** | 0.50 GARCH / 0.50 empirical RV (hardcoded) |
| **Si fail** | `_garch = {}` → fallback côté signal sur `_har` ou rien |

### 4.3 HAR-RV (Corsi 2009) (`_har`) — **estimateur préféré**

**Forecast P-measure** mixed-frequency.

```
log(RV_{t+1}) = β₀ + β_d·log(RV_t)         (composante daily)
                  + β_w·log(RV_t^w)         (rolling 5j)
                  + β_m·log(RV_t^m)         (rolling 22j)
                  + ε
RV_t ≈ |r_t| · √252        (proxy daily realized)
```

Itéré `horizon_days` fois pour le tenor cible.

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/har_rv.py` |
| **Input** | closes daily, tenor_days = `{label → days}` |
| **Min bars** | ≥ 42 (= 22 monthly lag + 20 fit margin) |
| **Output** | `_har[tenor] = {sigma_har_pct: float}` |
| **Config** | `calibration.har_components = (1, 5, 22)` (pas encore wired runtime) |
| **Pourquoi préféré** | Empiriquement bat GARCH sur term-structure FX (mixed-frequency) |
| **Si fail** | `_har = {}` → fallback GARCH |

### 4.4 Conversion P → Q via VRP (`_fair_q`)

Le marché des options prix la vol en mesure Q (risk-neutral), pas P (réalisée).
Comparer σ_mid (Q) à σ_fair^P directement est **économiquement faux** :
l'écart attendu = VRP (Variance Risk Premium).

```
σ_fair^Q = σ_fair^P + VRP(tenor, regime)
```

Le **régime** est détecté heuristiquement à partir de RV, vol-of-vol et term
slope :

```python
detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct) →
  "calm" | "stressed" | "pre_event"
```

VRP par défaut (en vol points) :

| Tenor | calm | stressed | pre_event |
|---|---|---|---|
| 1M | 0.6 | 1.5 | 2.5 |
| 2M | 0.7 | 1.6 | 2.6 |
| 3M | 0.8 | 1.7 | 2.7 |
| 4M | 0.9 | 1.8 | 2.8 |
| 5M | 1.0 | 1.9 | 2.9 |
| 6M | 1.1 | 2.0 | 3.0 |

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/vrp.py` |
| **Input** | σ_fair^P, tenor label |
| **Output** | `_fair_q[tenor] = {sigma_fair_p_pct, vrp_vol_pts, sigma_fair_q_pct, regime}` |
| **Config** | hardcoded (P1.2 du refactor pas encore branché à `VolTradingConfig.signal.vrp_regime_override`) |
| **Si fail** | `_fair_q = {}` → signaux fallback sur GARCH-as-if-Q (legacy) |

### 4.5 SVI per tenor (`_svi`)

Fit paramétrique du **smile** par tenor sur les 5 pillars δ (10dp, 25dp, atm,
25dc, 10dc).

```
w(k) = a + b·(ρ·(k − m) + √((k − m)² + σ²))      (Gatheral raw SVI)
```

5 paramètres, 5 observations → fit exact possible mais via least-squares
borné pour éviter dégénérescences.

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/svi.py` |
| **Input** | 5 (strike, IV) per tenor |
| **Min strikes** | ≥ 3 |
| **Output** | `_svi[tenor] = {a, b, rho, m, sigma, rmse_fit, butterfly_g_min}` |
| **Bounds** | a ∈ [0, 2·max_w], b ∈ [0,1], ρ ∈ [−0.999, 0.999], m ∈ [−1,1], σ ∈ [0.001, 2.0] |
| **No-arb check** | `butterfly_g_min` ∈ R, ≥0 = OK, <0 = densité négative ⚠ |
| **Config** | `surface.svi_rmse_max_warn = 0.003`, `surface.butterfly_check_grid = 100` |

**Lecture** :
- `rho < 0` typique EURUSD (skew négatif : puts plus chers que calls)
- `b > 0` toujours (sinon smile inversé physiquement absurde)
- `rmse_fit < 0.003` = bon fit, > 0.01 = smile bruité ou ill-posed
- `butterfly_g_min < 0` = signal douteux sur ce tenor, ne pas trade

### 4.6 SSVI surface-wide (`_ssvi`)

Fit **paramétrique de la surface entière** (cross-tenors). Un seul triplet
(η, γ, ρ) pour tous les tenors.

```
w(k, θ) = (θ/2)·(1 + ρ·φ(θ)·k + √((φ(θ)·k + ρ)² + (1 − ρ²)))
φ(θ) = η · θ^(−γ)
θ = ATM total variance per tenor
```

| Aspect | Valeur |
|---|---|
| **Module** | `core/vol/ssvi.py` |
| **Input** | toutes les obs (T, K, IV) cross-tenors |
| **Min** | ≥ 2 tenors avec ATM IV + ≥ 5 obs total |
| **Output** | `_ssvi = {eta, gamma, rho, rmse_fit, calendar_arb_free}` (surface-level, 1 row pas N) |
| **Calendar-arb** | 2γ ≥ 1 − ρ² (Gatheral-Jacquier 2014) |
| **Bounds** | η ∈ [10⁻³, 10], γ ∈ [0.05, 0.95], ρ ∈ [−0.999, 0.999] |

**Pourquoi en plus de SVI** : SSVI garantit l'absence d'arbitrage calendaire
(prix ≥ pour tenor plus lointain) by construction si la condition tient.
Plus régulier mais moins flexible que les 6 fits SVI indépendants.

### 4.7 Signaux (`signals[]`)

Comparaison **σ_mid (Q)** vs **σ_fair (Q)** par tenor.

```python
ecart = sigma_mid_pct - sigma_fair_q_pct       # both in Q-measure
if abs(ecart) <= threshold_vol_pts: signal = "FAIR"
elif ecart > 0:                     signal = "EXPENSIVE"   # mid trop haut → vendre vol
else:                               signal = "CHEAP"       # mid trop bas → acheter vol
```

| Champ | Source | Unité |
|---|---|---|
| `underlying` | param symbol | "EURUSD" |
| `tenor` | clé surface | "1M"..."6M" |
| `dte` | `DTE_FROM_LABEL[tenor]` | days |
| `sigma_mid` | IV ATM IB × 100 | % (Q) |
| `sigma_fair` | `_fair_q[tenor].sigma_fair_q_pct` ou legacy GARCH | % (Q) |
| `ecart` | sigma_mid − sigma_fair | % signed |
| `signal_type` | seuil ci-dessus | str |
| `rv` | `_rv_full_pct` | % (P) |
| `sigma_fair_p` | P-measure pré-VRP | % |
| `vrp_vol_pts` | Q − P | % |

---

## 5. Schéma complet du payload `latest_vol_surface`

```jsonc
{
  "symbol": "EURUSD",
  "timestamp": "2026-04-30T08:30:01Z",
  "surface": {
    // Tenors publics (PCHIP-interpolés)
    "1M": {
      "10dp": { "iv": 0.062, "strike": 1.155 },
      "25dp": { "iv": 0.060, "strike": 1.165 },
      "atm":  { "iv": 0.059, "strike": 1.171 },
      "25dc": { "iv": 0.060, "strike": 1.180 },
      "10dc": { "iv": 0.063, "strike": 1.195 }
    },
    "2M": { ... },
    // ... jusqu'à 6M

    // Estimateurs P-measure
    "_rv_full_pct": 7.23,
    "_har":   { "1M": {"sigma_har_pct": 6.85}, "2M": {...}, ... },
    "_garch": { "1M": {"sigma_model_pct": 6.92}, ... },

    // Conversion P → Q
    "_fair_q": {
      "1M": {
        "sigma_fair_p_pct": 6.85,
        "vrp_vol_pts": 0.6,
        "sigma_fair_q_pct": 7.45,
        "regime": "calm"
      },
      ...
    },

    // Smile fits
    "_svi": {
      "1M": { "a": 0.0001, "b": 0.012, "rho": -0.73, "m": 0.001,
              "sigma": 0.031, "rmse_fit": 1.1e-05, "butterfly_g_min": 0.0023 },
      ...
    },

    // Surface fit
    "_ssvi": {
      "eta": 1.69, "gamma": 0.41, "rho": -0.11,
      "rmse_fit": 1.6e-05, "calendar_arb_free": true
    }
  }
}
```

---

## 6. Configuration & hot-reload

`VolTradingConfig` (dans `core/config/vol_params.py`) a 8 sections. Au runtime,
**seules 2 sont consommées** par vol-engine actuellement :

| Section | Field | Usage runtime | Hot-reload ? |
|---|---|---|---|
| `signal` | `threshold_vol_pts` | seuil CHEAP/FAIR/EXPENSIVE | ✅ via `config:changed` |
| `signal` | `model_p` | "har" \| "garch" choix estimateur P | ✅ via `config:changed` |
| `surface` | `tenors_days` | grille tenors | hardcodée engine.py |
| `surface` | `delta_pillars` | grille deltas | hardcodée engine.py |
| `surface` | `svi_rmse_max_warn` | seuil log warning | pas wired |
| `surface` | `butterfly_check_grid` | densité grid SVI | pas wired |
| `calibration` | `har_components` | (daily, weekly, monthly) | pas wired |
| `regime`, `sizing`, `exit_rules`, `delta_hedge`, `structures` | tous | phases P1+ refactor | pas wired |

**Mécanisme hot-reload** :
1. Admin POST `/api/v1/admin/config` → INSERT vol_config row v_n+1
2. `api` PUBLISH `config:changed` sur Redis avec le payload complet
3. `vol-engine._watch_config_changes` (main.py:28) consume → `engine.apply_config(cfg)`
4. Au prochain cycle : nouveau threshold + model_p effectifs

---

## 7. Failure modes & fallbacks

| Étape qui fail | Conséquence | Détectable comment |
|---|---|---|
| Spot Redis absent | Cycle skip silencieux | `vol_cycle_skipped reason=no_spot` log |
| Chain IB vide | Surface sans tenors publics | `surface = {}` post step, `_svi`/`_ssvi` skipped |
| OHLC fetch fail | Pas de RV/HAR/GARCH | `_rv_full_pct` absent → pas de `_fair_q` → signals fallback legacy |
| GARCH fit non-cv | `_garch[tenor]` manquant | log `garch_projection_failed` |
| HAR insuffisant (<42 bars) | `_har = {}` | log info, fallback GARCH |
| SVI fit fail (1 tenor) | tenor skipped dans `_svi` | rmse_fit absent pour ce tenor |
| Butterfly violation | warning logged, fit retourné quand même | `butterfly_g_min < 0` dans la row |
| SSVI insuffisant | `_ssvi = None` | calendar_arb_free skipped |
| Publish Redis fail | `vol_cycle` retourne False, retry next tick | log `publish_vol_update_failed` |
| Publish DB fail | log `publish_db_event_failed`, **cycle continue** | la row vol_surfaces manque ce cycle |

**Pas de circuit breaker** — le moteur continue à scanner. À détecter via
âge du heartbeat (>300s = bloqué).

---

## 8. Ce qu'il faut afficher dans `/dev/vol`

Priorité du plus utile au moins :

### Tier 1 — santé du moteur

| Métrique | Source | Critère vert |
|---|---|---|
| Heartbeat age | `heartbeat:vol_engine` | < 300s |
| Surface freshness | `latest_vol_surface` timestamp | < 200s |
| IB connection | (status execution-engine ou heartbeat) | OK |
| Cycle dernière durée | logs `position_sync_tick` ou nouveau metric | < 60s |
| Tenors publics présents | `surface` keys non-`_` | 6 |
| Signaux générés ce cycle | `signals` array | ≥ 1 |

### Tier 2 — qualité des fits

| Métrique | Source | Lecture |
|---|---|---|
| RV Yang-Zhang | `_rv_full_pct` | tendance régime, normal 5-10% pour EURUSD |
| RMSE SVI par tenor | `_svi[tenor].rmse_fit` | < 0.003 = bon, > 0.01 = douteux |
| Butterfly g_min par tenor | `_svi[tenor].butterfly_g_min` | ≥ 0 obligatoire pour trader ce tenor |
| RMSE SSVI | `_ssvi.rmse_fit` | < 0.05 = bon |
| Calendar arb-free | `_ssvi.calendar_arb_free` | true obligatoire |

### Tier 3 — signaux

| Métrique | Lecture |
|---|---|
| Distribution CHEAP/FAIR/EXPENSIVE | mix attendu, all-FAIR = threshold trop large, all-EXPENSIVE = vol cher |
| Max\|ecart\| | plus grosse mispricing du cycle |
| VRP par tenor | indicateur régime (0.6 calm, 1.5+ stressed) |
| Régime détecté | calm/stressed/pre_event — règle de trading hard |

### Tier 4 — visualisations

| Chart | Source | Pourquoi |
|---|---|---|
| Smile par tenor | `surface.{tenor}` pillars + `_svi` curve | voir le fit observed vs paramétrique |
| Term structure ATM | `surface.{tenor}.atm.iv` ∀ tenors | voir contango/backwardation |
| RV vs HAR vs GARCH | scalaires comparés | divergence des estimateurs = signal régime change |
| Heatmap z-score (tenor × delta) | (à brancher quand fair-smile P1.4 sera live) | vue synoptique mispricings |
| Time series SVI params | rows `svi_params` historiques | détecter regime shifts via param drift |

### Tier 5 — debug / data brute

| Panel | Quoi |
|---|---|
| Raw payload `latest_vol_surface` | `<pre>` JSON |
| Liste des tags par tenor (10dp/25dp/atm/25dc/10dc) | table strikes + IV |
| 4 tables Postgres récentes | DB Explorer existant |

---

## 9. Décisions de design (FAQ)

**Q : Pourquoi PCHIP plutôt que cubic spline ou linéaire ?**
A : PCHIP est monotone-preserving. Pour le smile FX où les pillars sont
proches en delta, on veut éviter l'overshoot du cubic spline qui peut créer
des régions à densité négative. Linéaire perdrait la convexité du smile.

**Q : Pourquoi 6 tenors et pas plus ?**
A : Compromis entre granularité term-structure et coût IB chain scan
(~20-30s pour 6 tenors × 36 strikes = 216 contracts). 12 tenors doublerait
le cycle. 3 tenors raterait les structures calendar.

**Q : Pourquoi 5 deltas pillars (10p/25p/atm/25c/10c) ?**
A : Convention OTC vanilla FX. Les fits SVI ont 5 paramètres → exactement
identifiables. Plus de pillars = redondance. Moins = ill-posed.

**Q : Pourquoi le legacy GARCH-as-Q en fallback ?**
A : R7 sandbox shippait avec GARCH treated as Q-measure (faux mais
fonctionnel). Le refactor R9 introduit P→Q via VRP. Quand OHLC fail, on
revient au legacy plutôt qu'avoir 0 signal — meilleur que rien.

**Q : Pourquoi cycle 180s et pas 30s ?**
A : Le bottleneck est le chain scan IB (~20-30s) limité par rate-limits +
qualifyContracts. Sub-180s = scans qui se chevauchent + saturation Gateway.
180s laisse marge pour GARCH fit lent + DB writes async.

---

## 10. Liens & code references

- Code coeur : `src/services/vol/{main.py, engine.py, chain_fetcher.py, historical_fetcher.py}`
- Modèles math : `src/core/vol/{yang_zhang.py, garch.py, har_rv.py, svi.py, ssvi.py, vrp.py, pchip_smile.py}`
- Pricing : `src/core/pricing/bs.py`
- Config schema : `src/core/config/vol_params.py`
- ORM tables : `src/persistence/models.py` (VolSurface, Signal, SviParams, SsviParams)
- API responses : `src/api/models/vol.py`
- Bus channels : `src/bus/{publisher.py, channels.py, keys.py}`
- Smoke tests : `scripts/smoke/vol/0[1-5]_test_*.ipynb`
- Doc user-facing : `docs/VOL_TRADING_USER_GUIDE.md` § Panel 5 — Surface Diagnostic
- Doc refactor plan : `docs/VOL_MODEL_REFACTOR_PLAN.md`
