# Dashboard — Ajouts Vol Scanner & Risk

## Contexte

Le dashboard existant dispose de :
- Chart de ticks (spot en temps réel)
- Résumé portfolio (positions, P&L)
- Panel de prise de position
- Table ordres open / closed

Les ajouts ci-dessous s'appuient sur les outputs des **step 1** (`vol_mid_output.csv`) et **step 2** (`vol_fair_output.csv`) pour transformer le dashboard en outil de décision vol.

---

## Architecture des données

```
IB Gateway
    │
    ├── reqMktData tick 100      → IV_marché par strike/tenor
    ├── reqHistoricalData OHLC   → Realized Vol (Yang-Zhang)
    ├── reqMktData FUT           → Spot EUR/USD + taux r_d/r_f
    └── reqPositions + greeks    → Portfolio greeks live
         │
         ▼
    step1 : vol_mid_output.csv   (σ_mid, RR25, BF25, strikes pilliers)
    step2 : vol_fair_output.csv  (σ_fair, RV, σ_model, écart, signal)
         │
         ▼
    Dashboard panels (détail ci-dessous)
```

---

## Panel 1 — Vol Scanner (priorité critique)

**Position** : ligne centrale du dashboard, pleine largeur.

**Update** : toutes les 30 secondes (reqMktData tick 100 sur les strikes actifs).

### Source des données

| Colonne | Source | Calcul |
|---|---|---|
| `IV_marché%` | IB tick 100 | `ticker.impliedVolatility × 100` |
| `σ_fair%` | step2 `σ_fair%` + shape smile step1 | `σ_fair_ATM + ajustement RR/BF` |
| `Écart%` | calculé | `IV_marché − σ_fair` |
| `Signal` | seuil | `CHEAP if écart < −0.20 / EXPENSIVE if écart > +0.20 / FAIR` |

### Seuil de signal

```python
SIGNAL_THRESHOLD = 0.20   # en vol %
# en dessous de −0.20% → CHEAP  → opportunité achat
# au dessus de +0.20%  → EXPENSIVE → opportunité vente
# entre −0.20 et +0.20 → FAIR → pas d'action
```

### Structure de la table

```
Tenor | Δ_label  | Strike | IV_marché% | σ_fair% | Écart% | Signal     | Action suggérée
  3M  | ATM      | 1.085  |    8.50    |  7.81   | +0.69  | EXPENSIVE  | Vendre call/put
  3M  | 25Δ call | 1.108  |    7.68    |  7.95   | −0.27  | FAIR       | —
  3M  | 25Δ put  | 1.056  |    8.22    |  8.45   | −0.23  | FAIR       | —
  3M  | 10Δ call | 1.128  |    9.28    |  8.80   | +0.48  | EXPENSIVE  | Vendre call
  6M  | ATM      | 1.085  |    9.20    |  8.46   | +0.74  | EXPENSIVE  | Vendre call/put
  6M  | 25Δ put  | 1.040  |   10.33    |  9.85   | +0.48  | EXPENSIVE  | Vendre put
  1Y  | ATM      | 1.085  |    8.90    |  8.82   | +0.08  | FAIR       | —
```

### Coloration

```
EXPENSIVE (écart > +0.20%) → fond rouge clair  #FCEBEB
CHEAP     (écart < −0.20%) → fond vert clair   #E1F5EE
FAIR                       → fond neutre
```

### Interaction

- Clic sur une ligne → pré-remplit le panel de prise de position
  (strike, tenor, right, notionnel suggéré via delta sizing)
- Tri par `|Écart%|` décroissant → les meilleures opportunités en haut

---

## Panel 2 — Term Structure (IV vs σ_fair)

**Position** : haut droite, à côté du chart de ticks.

**Update** : toutes les 60 secondes.

### Source des données

```python
# X-axis : tenors [1W, 2W, 1M, 2M, 3M, 6M, 9M, 1Y, 18M, 2Y]
# Y-axis : vol %

courbe_iv_marche  = step1["σ_ATM%"]        # bleu
courbe_sigma_fair = step2["σ_fair%"]        # vert
courbe_rv         = step2["RV%"]            # orange pointillé
zone_opportunite  = iv_marche - sigma_fair  # remplie en rouge si > 0, vert si < 0
```

### Ce qu'on lit sur ce graphe

- La zone rouge entre les deux courbes = tenors où le marché est cher
- La zone verte = tenors où le marché est bon marché
- La courbe RV orange = ancre fondamentale (ce que le marché a réellement livré)
- Si IV >> RV sur tous les tenors → régime de risk premium élevé → structurellement vendeur de vol

### Annotations automatiques

```python
# Annotation du tenor avec l'écart maximum
max_ecart_tenor = step2.loc[step2["écart%"].abs().idxmax(), "tenor"]
# → "Opportunité max : 6M (+0.74%)" affiché sur le graphe
```

---

## Panel 3 — Smile Chart par tenor

**Position** : ligne du milieu, centre.

**Update** : à la demande (sélecteur de tenor).

### Source des données

```python
# Sélecteur : [1M, 3M, 6M, 1Y]
tenor_selectionne = "3M"

# X-axis : pilliers delta [10Δp, 25Δp, ATM, 25Δc, 10Δc]
# Y-axis : vol %

smile_marche = step1[["iv_10dp%","iv_25dp%","σ_ATM%","iv_25dc%","iv_10dc%"]]  # bleu
smile_fair   = calculé depuis σ_fair_ATM + RR/BF step1                         # vert
```

### Ce qu'on lit

- Si le smile marché est au-dessus du smile fair sur les puts OTM
  → le skew est trop cher → opportunité de vente de RR (vendre put, acheter call)
- Si le smile marché est en dessous sur les calls OTM
  → les calls sont bon marché → opportunité d'achat call

---

## Panel 4 — Greeks Portfolio (agrégé)

**Position** : ligne du milieu, gauche.

**Update** : temps réel (à chaque tick sur les positions ouvertes).

### Source des données

```python
# Pour chaque position ouverte (depuis reqPositions + reqMktData tick 100)
# IB fournit les greeks via ticker.modelGreeks

delta_net  = Σ (position_i × delta_i  × multiplier)   # en EUR
vega_net   = Σ (position_i × vega_i   × multiplier)   # en EUR / vol%
gamma_net  = Σ (position_i × gamma_i  × multiplier)   # en EUR / (EUR/USD)²
theta_net  = Σ (position_i × theta_i  × multiplier)   # en EUR / jour
```

### Affichage

```
┌─────────────────────────────────────┐
│  Delta net    −35 420 EUR           │
│  Vega net     +12 300 EUR / vol%    │
│  Gamma net    +1 840 EUR            │
│  Theta net    −280 EUR / jour       │
└─────────────────────────────────────┘

  Delta hedge suggéré :
  Acheter 0.28 contrat future EUR
  [Bouton → ordre IB automatique]
```

### Interprétation affichée

```python
# Theta interprétation
theta_annuel = theta_net * 252
# "Au rythme actuel : −70 560 EUR/an si spot et vol stables"

# Vega interprétation
# "Si vol +1% sur tous les tenors : +12 300 EUR"
```

---

## Panel 5 — P&L Decomposition par position

**Position** : intégré dans la table des positions ouvertes (colonnes supplémentaires).

**Update** : toutes les 60 secondes.

### Calcul

```python
# Pour chaque position ouverte

# P&L total (donné par IB)
pnl_total = (prix_actuel - prix_entree) * position * multiplier

# P&L delta (mouvement spot depuis entrée)
pnl_delta = delta_entree * (spot_actuel - spot_entree) * position * multiplier

# P&L theta (time decay depuis entrée)
pnl_theta = theta_journalier * nb_jours_depuis_entree * position * multiplier

# P&L vega (résiduel)
pnl_vega  = pnl_total - pnl_delta - pnl_theta
# approximation : capte le mouvement de vol implicite
```

### Colonnes ajoutées à la table positions

```
Position | Strike | Tenor | P&L total | P&L delta | P&L vega | P&L theta | IV_entrée | IV_actuelle
Call 3M  | 1.085  |  3M   |  +1 240€  |  +890€    |  +620€   |  −270€    |   7.50%   |   8.10%
Put  6M  | 1.040  |  6M   |  −380€    |  −120€    |  −180€   |  −80€     |   9.80%   |   9.65%
```

---

## Panel 6 — RV vs IV Historique

**Position** : bas du dashboard, pleine largeur (optionnel, toggle).

**Update** : une fois par jour (données journalières).

### Source des données

```python
# reqHistoricalData : 60 barres journalières
# Yang-Zhang calculé sur fenêtre glissante 21 jours

dates        = df_ohlc["date"]
rv_21j       = [yang_zhang_rv(df_ohlc, 21, end=i) for i in range(len(df_ohlc))]
iv_atm_hist  = step1["σ_ATM%"]  # snapshot quotidien stocké en DB locale
risk_premium = iv_atm_hist - rv_21j
```

### Ce qu'on lit

- Risk premium positif en permanence → normal (IV > RV structurellement)
- Risk premium qui s'effondre → la RV rattrape l'IV → mauvais moment pour être long vol
- Risk premium anormalement élevé → opportunité de vente de vol

---

## Panel 7 — Vol P&L Post-Trade (analyse)

**Position** : onglet séparé "Analyse".

**Source** : table des ordres closed enrichie.

### Calcul post-trade

```python
# Pour chaque trade fermé

iv_achetee     = IV au moment de l'entrée (stockée dans la table ordres)
rv_realisee    = Yang-Zhang sur la période de détention
edge_realise   = rv_realisee - iv_achetee   # positif = bon trade vol

# Exemple
# IV achetée : 7.50%
# RV réalisée sur 21 jours : 8.80%
# Edge vol réalisé : +1.30%  → trade correctement identifié
```

### Table analyse

```
Date entrée | Date sortie | Tenor | Strike | IV achetée | RV réalisée | Edge vol | P&L vol | Verdict
2025-01-15  | 2025-02-05  |  3M   | 1.085  |   7.50%    |   8.80%     |  +1.30%  | +1 240€ | BON
2025-01-22  | 2025-02-12  |  6M   | 1.040  |   9.80%    |   8.20%     |  −1.60%  |  −380€  | MAUVAIS
```

---

## Résumé des updates nécessaires dans le code

### Nouveaux modules Python à ajouter

```
vol_mid_step1.py      → déjà écrit (step 1)
vol_fair_step2.py     → déjà écrit (step 2)
vol_scanner.py        → nouveau : agrège step1 + step2 → table scanner
greeks_monitor.py     → nouveau : reqMktData tick 100 sur positions ouvertes
pnl_decompose.py      → nouveau : split P&L delta/vega/theta
rv_monitor.py         → nouveau : Yang-Zhang rolling sur reqHistoricalData
```

### Fréquences de mise à jour

```
Temps réel (< 1s)  : spot tick chart, P&L total positions
Toutes les 30s     : vol scanner (IV_marché par strike)
Toutes les 60s     : term structure, greeks portfolio
Toutes les 5min    : smile chart
Une fois par jour  : RV historique, recalibrage GARCH (step 2)
```

### Données à persister localement

```
vol_mid_output.csv    → output step 1 (mis à jour toutes les 30s)
vol_fair_output.csv   → output step 2 (mis à jour 1x/jour)
orders_history.csv    → table ordres enrichie (IV_entrée, spot_entrée)
rv_history.csv        → série RV journalière (pour panel 6)
```

---

## Ordre d'implémentation suggéré

```
1. Vol Scanner (panel 1)     ← impact immédiat sur la prise de décision
2. Term Structure (panel 2)  ← contexte macro vol
3. Greeks Portfolio (panel 4) ← risk management de base
4. P&L Decomposition (panel 5) ← compréhension des trades ouverts
5. Smile Chart (panel 3)     ← affinage du signal strike
6. RV vs IV historique (panel 6) ← contexte long terme
7. Vol P&L post-trade (panel 7)  ← validation du modèle
```
