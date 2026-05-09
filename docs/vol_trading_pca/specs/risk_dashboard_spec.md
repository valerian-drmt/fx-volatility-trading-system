# Risk dashboard — spec d'implémentation

Spec des **8 tableaux additionnels** à ajouter au dashboard `trading-system v1`.
Le tableau existant `E · Open positions (5)` reste inchangé.

État actuel des données disponibles (extrait blotter) :

| ID | Structure | Side | Tenor | Expiry | Qty | Nominal | Contract | Mark | P&L | Δ ($) | Γ ($/pip) | Vega ($/vp) | Θ ($/d) |
|----|-----------|------|-------|--------|-----|---------|----------|------|-----|-------|-----------|-------------|---------|
| 1 | 6EM6 | SELL | 1M | 2026-06-15 | 6 | 750k € | 1.18278 | 1.18064 | +1.61k$ | -750k | 0 | 0 | 0 |
| 2 | EUUN6 P1170 | BUY | 2M | 2026-07-02 | 10 | 1.25M € | 0.01112 | 0.00483 | -7.86k$ | -417.20k | +2.10k | +2.06k | -91.37 |
| 3 | M6EM6 | SELL | 1M | 2026-06-15 | 3 | 37.5k € | 1.18217 | 1.18068 | +55.82$ | -37.50k | 0 | 0 | 0 |
| 4 | 6EK6 | BUY | 1W | 2026-05-18 | 5 | 625k € | 1.17041 | 1.17909 | +5.42k$ | +625k | 0 | 0 | 0 |
| 5 | EUUN6 C1170 | BUY | 2M | 2026-07-02 | 20 | 2.5M € | 0.01837 | 0.01960 | +3.08k$ | +1.51M | +2.62k | +4.37k | -327.98 |

---

## Hiérarchie d'implémentation

| Priorité | Tableau | Section | Coût | Impact |
|----------|---------|---------|------|--------|
| 1 | Header summary | A | Bas | Très haut |
| 2 | Open positions enrichi | (E existant + colonnes IV, Vanna, Volga) | Bas | Haut |
| 3 | Spot × Vol P&L grid | F | Moyen | Très haut |
| 4 | P&L attribution daily | G | Moyen | Haut |
| 5 | Greeks ladder | H | Moyen | Moyen |
| 6 | Vega bucket par tenor | I | Moyen | Moyen → Haut si multi-expiry |
| 7 | Pin risk grid | J | Bas (conditionnel near-expiry) | Haut près expiry |
| 8 | Margin / SPAN | K | Haut (broker API) | Critique scaling |

---

## A · Header summary (sticky, toujours visible)

**Format** : 1 ligne en haut du dashboard, valeurs agrégées sur tout le book.

| Champ | Type | Calcul | Exemple |
|-------|------|--------|---------|
| Total P&L | $ | Σ P&L_pending + P&L_realized_today | +2.31k$ |
| Δ net | $ | Σ Δ_position | +932k |
| Γ net | $/pip | Σ Γ_position | +4.72k |
| Vega net | $/vp | Σ Vega_position | +6.43k |
| Θ net | $/jour | Σ Θ_position | -419.35 |
| Margin used | $ | broker API | 48.2k |
| Margin avail | $ | broker API | 200k |
| Util % | % | used/total | 24.1% |
| VaR 1d 99% | $ | Monte Carlo ou paramétrique | -12.4k |

**Règle d'affichage** : code couleur sur Total P&L (vert/rouge), Util % (vert <50%, ambre 50-75%, rouge >75%), VaR (toujours négatif, magnitude alerte si > 5% NAV).

**Update** : real-time sur Δ/Γ/Vega/Θ/P&L (sur tick mark), 1× par jour pour VaR.

---

## F · Spot × Vol P&L grid

**Format** : matrice 2D, 7 colonnes (spot bins) × 5 lignes (vol bins). Cellule centrale = baseline (P&L = 0).

| ΔIV \ ΔSpot | -200bp | -100bp | -50bp | 0 | +50bp | +100bp | +200bp |
|-------------|--------|--------|-------|---|-------|--------|--------|
| +3vp | -8.2k | -2.1k | +1.4k | +5.8k | +8.1k | +11.2k | +15.6k |
| +1vp | -9.5k | -3.2k | -0.5k | +2.4k | +4.6k | +7.8k | +12.1k |
| 0 | -10.8k | -4.3k | -1.4k | **0** | +1.8k | +4.2k | +8.6k |
| -1vp | -12.1k | -5.4k | -2.3k | -2.1k | -0.8k | +0.6k | +5.1k |
| -3vp | -14.6k | -7.6k | -4.5k | -4.2k | -2.9k | -1.4k | +1.8k |

**Calcul** : full revaluation du book sous chaque scénario `(ΔS, Δσ)`.

```python
def pnl_grid(book, spot_bins, vol_bins, S0, sigma_surface_0):
    grid = np.zeros((len(vol_bins), len(spot_bins)))
    for i, dv in enumerate(vol_bins):
        for j, ds in enumerate(spot_bins):
            S = S0 * (1 + ds/10000)  # bp → ratio
            sigma_shifted = sigma_surface_0 + dv/100  # vp → décimal
            npv_new = book.revalue(S=S, sigma=sigma_shifted)
            grid[i, j] = npv_new - book.npv_baseline
    return grid
```

**Buckets recommandés** :
- Spot : ±50bp / ±100bp / ±200bp (calibrer sur 1σ daily du sous-jacent)
- Vol : ±1vp / ±3vp (parallel shift IV surface)

**Toggles** :
- `delta_hedged`: bool — affiche P&L net du delta hedge à t=0 (isole γ + vega)
- `bucket_size`: enum {1σ, 2σ, 3σ} — calibration auto sur historique 60d

**Code couleur** : heatmap rouge → vert, intensité proportionnelle à |P&L|.

**Update** : précalcul toutes les 5min, recalc complet à chaque trade.

---

## G · P&L attribution daily

**Format** : décomposition Greeks-based du P&L journalier par position (ou agrégé).

| Source | Pos 2 (PUT) | Pos 5 (CALL) | Futures (1+3+4) | Total |
|--------|-------------|--------------|-----------------|-------|
| Delta P&L | +1,250 | -4,530 | +850 | -2,430 |
| Gamma P&L | +78 | +162 | 0 | +240 |
| Theta P&L | -91 | -328 | 0 | -419 |
| Vega P&L | -8,860 | +8,150 | 0 | -710 |
| Rho P&L | -12 | +8 | +18 | +14 |
| Cross / résiduel | -225 | -382 | 0 | -607 |
| **P&L réel** | **-7,860** | **+3,080** | **+8,190** | **+3,410** |

**Formule (Taylor ordre 2)** :

$$\Delta P\&L = \Delta \cdot \Delta S + \tfrac{1}{2}\Gamma \cdot (\Delta S)^2 + \Theta \cdot \Delta t + \mathcal{V} \cdot \Delta\sigma + \rho \cdot \Delta r + \epsilon$$

```python
def pnl_attribution(pos, t_minus_1, t):
    dS = t.spot - t_minus_1.spot
    dsigma = t.iv - t_minus_1.iv
    dt = (t.timestamp - t_minus_1.timestamp).days
    dr = t.rate - t_minus_1.rate
    
    delta_pnl = pos.delta_t_minus_1 * dS
    gamma_pnl = 0.5 * pos.gamma_t_minus_1 * dS**2
    theta_pnl = pos.theta_t_minus_1 * dt
    vega_pnl = pos.vega_t_minus_1 * dsigma
    rho_pnl = pos.rho_t_minus_1 * dr
    
    actual_pnl = pos.npv_t - pos.npv_t_minus_1
    residual = actual_pnl - (delta_pnl + gamma_pnl + theta_pnl + vega_pnl + rho_pnl)
    
    return {
        'delta': delta_pnl, 'gamma': gamma_pnl, 'theta': theta_pnl,
        'vega': vega_pnl, 'rho': rho_pnl, 'residual': residual,
        'total': actual_pnl
    }
```

**Règle d'alerte** : si `|residual| / |total| > 5%`, modèle de risque incomplet (manque vanna/volga/charm). Logger pour investigation.

**Storage requis** : snapshot des Greeks à t-1 (EOD précédent) + spot/IV t-1.

**Update** : 1× par jour à EOD (close-to-close attribution). Optionnel intra-day si snapshot disponible.

---

## H · Greeks ladder par bucket de spot

**Format** : tableau qui montre l'évolution des Greeks book quand le spot bouge.

| Spot | P&L ($) | Δ ($) | Γ ($/pip) | Vega ($/vp) | Hedge à faire |
|------|---------|-------|-----------|-------------|----------------|
| 1.17509 (-400bp) | -18,400 | +892k | +5,420 | +8,200 | -892k Δ |
| 1.17709 (-200bp) | -10,800 | +1,260k | +5,950 | +7,440 | -1.26M Δ |
| **1.17909 (spot)** | **0** | **+1,591k** | **+6,430** | **+6,430** | **-1.59M Δ** |
| 1.18109 (+200bp) | +1,800 | +1,920k | +5,950 | +5,440 | -1.92M Δ |
| 1.18309 (+400bp) | +8,640 | +2,210k | +5,420 | +4,180 | -2.21M Δ |

**Calcul** : full reval book à `S = S0 × (1 + ΔS_bp/10000)`, IV inchangée.

**Buckets** : ±100bp / ±200bp / ±400bp (5 lignes).

**Colonnes optionnelles** :
- `Vanna ($/vp/u)` : ΔΔ par volpoint
- `Charm ($/d)` : ΔΔ par jour
- `% du Δ rehedgé` : si tu maintiens un hedge ratio cible

**Lecture clé** : non-stationnarité du Γ. Si Γ varie fortement entre buckets → book non-linéairement exposé, hedging dynamique nécessaire.

**Update** : real-time (cheap car local approx OK : delta + gamma + cross).

---

## I · Vega bucket par tenor

**Format** : décomposition du vega net par tranche de maturité.

| Bucket | Vega ($/vp) | % du total | Vanna ($/vp/u) | Volga ($/vp²) |
|--------|-------------|------------|----------------|---------------|
| 0–1M (front) | 0 | 0% | 0 | 0 |
| 1–3M | +6,430 | 100% | +1,820 | +340 |
| 3–6M | 0 | 0% | 0 | 0 |
| 6–12M | 0 | 0% | 0 | 0 |
| 12M+ | 0 | 0% | 0 | 0 |
| **TOTAL** | **+6,430** | **100%** | **+1,820** | **+340** |

**Buckets standards** : 1W / 1M / 3M / 6M / 1Y / 2Y+. À ajuster selon ton univers.

**Colonnes obligatoires** : Vega ($/vp), % du total.
**Colonnes optionnelles** : Vanna, Volga (ordre 2 vol).

**Règle d'alerte** : concentration > 80% sur un bucket = exposition à un seul point de la courbe IV. Diversifier en ouvrant une jambe sur autre tenor pour neutraliser parallel shift sans perdre l'exposition principale.

**Variantes utiles** :
- `Vega bucket par strike` (skew exposure) : -25Δ / -10Δ / ATM / +10Δ / +25Δ
- `Vega bucket par sous-jacent` (multi-asset)

**Update** : recalc à chaque trade.

---

## J · Pin risk grid (conditionnel near-expiry)

**Format** : affiché uniquement si au moins 1 option a `DTE < 7j`. Sinon caché.

| Option | DTE | Strike | Spot | Distance | Δ now | P&L if pin (ΔS=0) | P&L if breach (ΔS=±50bp) |
|--------|-----|--------|------|----------|-------|-------------------|--------------------------|
| PUT 1.17 × 10 | 54d | 1.17000 | 1.17909 | +91 pips | -0.33 | -1,250 | +12,500 |
| CALL 1.17 × 20 | 54d | 1.17000 | 1.17909 | +91 pips | +0.60 | +2,500 | +25,000 |

**Calcul** :
- `Distance` = (Spot - Strike) en pips
- `P&L if pin` = P&L si spot termine **exactement** au strike (Δ flippe brutalement)
- `P&L if breach` = P&L si spot bouge de ±50bp dans le sens défavorable

**Règle d'alerte** : si une option a `DTE < 3j` et `|Distance| < 20 pips`, flag rouge **PIN RISK**. Δ instable, hedging dynamique impossible sur les derniers ticks.

**Update** : real-time près expiry, recalc Δ instantané sur chaque tick.

---

## K · Margin / SPAN utilization

**Format** : tableau d'exposition margin avec scenarios SPAN.

| Métrique | Valeur | Limite | % util |
|----------|--------|--------|--------|
| Total margin used | $48,200 | $200,000 | 24.1% |
| Maintenance margin | $35,400 | $200,000 | 17.7% |
| Initial margin | $48,200 | $200,000 | 24.1% |
| SPAN scenario worst (futures) | -$72,400 (-3σ) | -$150,000 | 48.3% |
| SPAN scenario worst (options) | -$28,300 | -$100,000 | 28.3% |
| Combined worst case | -$92,100 | -$250,000 | 36.8% |
| Liquidation buffer | $107,900 | — | — |

**Calcul** :
- `SPAN scenarios` : 16 scénarios pré-définis CME (combinaisons spot ±3σ × vol ±X) — récupérer depuis broker API
- `Liquidation buffer` = available_funds - max(SPAN scenarios)

**Règle d'alerte** :
- `Util % > 75%` : ambre, réduire taille
- `Util % > 90%` : rouge, liquidation imminente possible
- `Liquidation buffer < 10% NAV` : alerte critique

**Source** : IB API (`reqAccountSummary`, `reqAccountUpdates`), Trade Workstation `RiskNavigator`.

**Update** : every 30s ou sur événement (trade, position close).

---

## Vue d'ensemble — schéma de layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ A · HEADER SUMMARY (sticky)                                         │
│ P&L · Δ · Γ · Vega · Θ · Margin · VaR                               │
├──────────────────────────────────┬──────────────────────────────────┤
│ F · Spot × Vol P&L grid          │ H · Greeks ladder                │
│ (heatmap 7×5)                    │ I · Vega bucket par tenor        │
│                                  │ (tabs)                           │
├──────────────────────────────────┴──────────────────────────────────┤
│ E · Open positions (existant, inchangé)                             │
│ + drill-down → G · P&L attribution par position                     │
├──────────────────────────────────┬──────────────────────────────────┤
│ J · Pin risk (si near-expiry)    │ K · Margin / SPAN                │
└──────────────────────────────────┴──────────────────────────────────┘
```

---

## Dépendances techniques par tableau

| Tableau | Données requises | Service backend | Compute cost |
|---------|------------------|-----------------|--------------|
| A | Greeks per position (déjà calculés), broker margin API | aggregator + IB API | O(N) bas |
| F | Pricing engine (BS-FX), IV surface | `pricing_service` | O(N × 35) moyen |
| G | Greeks t-1 storage, spot/IV t-1 | `state_store` (Redis ou Postgres) | O(N) bas |
| H | Pricing engine (BS-FX) | `pricing_service` | O(N × 5) bas |
| I | Bucketing par tenor des Greeks existants | aggregator | O(N) bas |
| J | Pricing engine + tick stream | `pricing_service` + WS | O(K) bas (K = options proche expiry) |
| K | Broker API (SPAN scenarios) | IB API wrapper | O(1) côté nous, lent côté broker |

---

## Phase de roadmap

**Phase 1 (semaine 1-2)** : A + Open positions enrichi (ajouter colonnes Vanna, Volga, IV%).
**Phase 2 (semaine 3-4)** : F (Spot × Vol grid) — le plus gros impact décisionnel.
**Phase 3 (semaine 5-6)** : G + H + I (P&L attribution + Greeks ladder + Vega bucket).
**Phase 4 (semaine 7-8)** : J + K (pin risk + SPAN margin) — quand tu commences à scaler.

Tag `v1.0` après Phase 2 (book viable monitoring), `v1.5` après Phase 3, `v2.0` après Phase 4.

---

## Limitations à noter

- Tous les tableaux assument un pricing model unique (Black-Scholes-Merton FX, ou Garman-Kohlhagen). Si tu ajoutes des barrier options, asians, baskets → besoin d'un pricing engine multi-modèle.
- IV surface assumée disponible en input. Sans elle, les Vegas bucketés et la grille Spot×Vol ne sont pas calculables. Sources : Bloomberg OVDV, Reuters EIKON, ou ta propre construction depuis chaîne d'options listées.
- Greeks d'ordre 3+ (speed, color, ultima) non couverts. Marginaux pour positions standards mais critiques pour gros books de skew/vol traders. À ajouter si volga > 10% du vega exposure.
- Update real-time des grids = expensive. Privilégier précalcul + interpolation entre snapshots toutes les 1-5 min.
