# Volatility dashboard — pipeline, data sources, panel logic

Practical companion to `docs/VOL_MODEL.md` (math). This doc explains
**where the data comes from**, **how each panel is computed**, and
**what each indicator means** in the R9 sandbox pipeline.

---

## 1. Data sources

| Source | Frequency | Used by | Path |
|---|---|---|---|
| **IB Gateway** `Forex('EURUSD')` | delayed, ~5 ticks/s | market-data → Redis `latest_spot:EURUSD` + channel `ticks` | `services/market_data/main.py::_subscribe_ib_ticks` |
| **IB FOP chain** EUR CME (trading class EUU) | on-demand, scanned each 30s | vol-engine for the Smile | `services/vol/chain_fetcher.py::scan_all_tenors_concurrent` |
| **IB `reqHistoricalDataAsync`** EUR CONTFUT 1Y daily | fetched once per 30min (cache TTL) | vol-engine for Yang-Zhang RV + GARCH fair | `services/vol/historical_fetcher.py::fetch_daily_ohlc` |
| **Positions / portfolio** (reqPositions + updatePortfolioEvent) | on-connect + update stream | displayed in BookPanel | delivered as side-effects by ib_insync |

> Delayed market data (`ib.reqMarketDataType(3)`) is forced on both
> `market-data` and `vol-engine` so paper accounts without a live CME
> subscription still get `modelGreeks` — otherwise IV comes back empty
> and every pillar drops.

---

## 2. End-to-end pipeline

```
┌─────────────┐
│ IB Gateway  │ (delayed, port 4002)
└─────┬───────┘
      │  Forex EURUSD ticks
      │  FOP chain EUU (reqSecDefOptParams)
      │  reqMktData tick=100 → modelGreeks per option
      │  reqHistoricalData EUR CONTFUT 1Y daily
      │
┌─────▼────────────────────────────────────────────┐
│ market-data                                       │
│  • publish_tick()  →  Redis CH `ticks`            │
│  • SET `latest_spot:EURUSD` (str, TTL 10s)        │
└─────┬─────────────────────────────────────────────┘
      │
┌─────▼─────────────────────────────────────────────┐
│ vol-engine (cycle every 30s)                      │
│  1. GET latest_spot:EURUSD  →  F                  │
│  2. discover_chains(ib)     →  6 tenors EUU       │
│  3. scan_all_tenors_concurrent(ib, F, chains)     │
│       per tenor, Semaphore(3) :                   │
│        • qualify 18 strikes × 2 rights            │
│        • reqMktData(contract, "100")              │
│        • sleep 12s → collect modelGreeks          │
│        • merge C+P per strike → (δ, σ, K)         │
│        • cancelMktData                            │
│  4. interpolate_delta_pillars → 5 labels per tenor│
│     (10P / 25P / ATM / 25C / 10C)                 │
│  5. fetch_daily_ohlc (cached 30min)               │
│  6. yang_zhang_rv_pct(ohlc) → RV %                │
│  7. fit_and_project_garch(closes) → σ fair per    │
│     tenor                                         │
│  8. _derive_signals : ecart = σ mid − σ fair →    │
│     CHEAP / FAIR / EXPENSIVE                      │
│  9. PUBLISH vol_update + SET latest_vol_surface   │
│ 10. PUBLISH db_events table=vol_surfaces + one    │
│     frame per tenor table=signals                 │
└─────┬─────────────────────────────────────────────┘
      │
┌─────▼──────────────┐     ┌────────────────────────┐
│ db-writer          │ ──► │ PostgreSQL             │
│ SUBSCRIBE db_events│     │  • vol_surfaces        │
│ batch INSERT       │     │  • signals             │
└────────────────────┘     └───────────┬────────────┘
                                       │
                            ┌──────────▼───────────┐
                            │ API FastAPI          │
                            │  GET /term-structure │ (reads Redis)
                            │  GET /smile/{tenor}  │ (reads Postgres + SVI fit)
                            │  GET /signals        │ (reads Postgres)
                            │  WS  /ws/vol         │ (relays Redis pubsub)
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │ Frontend React       │
                            │  • Term Structure    │
                            │  • Smile             │
                            │  • Vol Scanner       │
                            └──────────────────────┘
```

---

## 3. Term Structure panel

**Question répondue** : à quel niveau se situe la vol implicite ATM pour chaque tenor, et comment ça se compare à la vol "fair" (GARCH) et la vol réalisée ?

### Données

Source : `GET /api/v1/vol/term-structure` → `api/services/vol_service.py::get_term_structure`.
L'API lit la **dernière surface en Redis** (`latest_vol_surface:EURUSD`, TTL ~120s) — pas Postgres — pour une latence minimale.

Chaque pillar retourné contient :

| Champ | Signification | Origine |
|---|---|---|
| `tenor` | label 1M..6M | vol-engine `chain_fetcher._discover_chains` |
| `sigma_atm_pct` | IV ATM observée | PCHIP sur les 5+ strikes au 0.50 delta |
| `sigma_fair_pct` | fair vol GARCH | `core/vol/garch.py::fit_and_project_garch` |
| `rv_pct` | Yang-Zhang RV | `core/vol/yang_zhang.py::yang_zhang_rv_pct` |

### Rendu

3 traces superposées :
- **σ mid** (vert, solide) : la réalité du marché ATM
- **σ fair (GARCH)** (orange, tiré) : ce que le modèle estime juste à chaque tenor
- **RV (Yang-Zhang)** (gris, pointillé) : la vol réalisée historique — ligne quasi-horizontale car c'est **une seule valeur** (agrégée sur toute la fenêtre) projetée sur chaque tenor pour servir de benchmark

### Lecture

- σ mid **au-dessus** des 2 autres → option chères vs historique ET vs modèle → potentiellement vendre de la vol
- σ mid **entre** σ fair et RV → régime normal, juste prix
- σ mid **en-dessous** → vol bon marché, potentiellement acheter (vega long)

---

## 4. Smile panel

**Question répondue** : pour un tenor donné, comment varie l'IV selon le strike (skew / convexité), et ça s'ajuste comment à un smile théoriquement cohérent ?

### Données

Source : `GET /api/v1/vol/smile/{tenor}` → lit **Postgres** (`vol_surfaces.surface_data`), pas Redis.
Pourquoi Postgres ? La row `vol_surfaces` conserve la structure pillar complète (10P/25P/ATM/25C/10C avec strikes exacts) ; le payload Redis compacté pourrait perdre des deltas intermediate.

Chaque réponse contient :

| Champ | Signification |
|---|---|
| `points` | 5 (delta_label, strike, iv_pct) observés |
| `sigma_fair_pct` | σ fair GARCH pour ce tenor |
| `rv_pct` | RV (même valeur que term structure — global) |
| `svi_curve` | courbe SVI interpolée (40 points) |

### SVI fit — calculé à la requête

À chaque `GET /smile/{tenor}` l'API fait :

1. Récupère les 5 pillars + le spot depuis la row Postgres
2. Convertit (strikes, IVs) en (log-moneyness k, total variance w = σ²T)
3. Calibre `a, b, ρ, m, σ` par `scipy.optimize.least_squares` (bounded trf) :
   ```
   w(k) = a + b × (ρ(k-m) + √((k-m)² + σ²))
   ```
4. Échantillonne le fit sur 40 points entre `ln(min_strike/F)` et `ln(max_strike/F)` → courbe lisse des points observés 10P jusqu'au 10C

Détails dans `core/vol/svi.py`.

### Rendu

4 traces :
- **pillars observés** (bleu, markers) : les 5 points 10P/25P/ATM/25C/10C
- **SVI fit** (violet, spline solide) : lissage paramétrique ancré sur les extrémités observées
- **σ fair (GARCH)** (orange, tiré) : ligne horizontale au niveau fair du tenor
- **RV** (gris, pointillé) : ligne horizontale au niveau réalisé

### Lecture

- **Forme** du smile : courbure (convexité) et skew visuel
- **Position des pillars par rapport au SVI** : si un pillar s'écarte nettement du fit → anomalie de pricing (illiquidité, erreur de donnée, ou opportunité)
- **Position du smile vs σ fair** : ATM au-dessus de fair = vol ATM chère pour ce tenor
- **Table à droite** : delta / strike / IV mid / skew (bp vs ATM) — couleurs rouge (wing cher vs ATM) / vert (wing inversé)

---

## 5. Vol Scanner panel

**Question répondue** : à l'instant T, quels tenors sont CHEAP / FAIR / EXPENSIVE et de combien ?

### Données

Source : `GET /api/v1/signals?latest_per_tenor=true` → lit Postgres table `signals`.
Le paramètre `latest_per_tenor` déduplique : **1 ligne par (underlying, tenor)** — la plus récente.

Chaque ligne contient : `timestamp, underlying, tenor, dte, sigma_mid, sigma_fair, ecart, signal_type, rv`.

### Logique de classification (`services/vol/engine.py::_derive_signals`)

Pour chaque tenor à chaque cycle vol-engine :

```python
ecart = sigma_mid_pct - sigma_fair_pct      # en points de vol

if abs(ecart) <= 1.0:   signal_type = "FAIR"        # ±100bp
elif ecart > 0:         signal_type = "EXPENSIVE"   # mid > fair = option chère
else:                   signal_type = "CHEAP"       # mid < fair = option peu chère
```

Le seuil 1.0 point de vol (100bp) est réglable via `SIGNAL_ECART_THRESHOLD_PCT`. Il est volontairement conservateur : en deçà de 100bp d'écart, le bruit d'estimation (GARCH, bid/ask) domine la vraie info.

### Rendu

Table compacte :

| Time | Symbol | Tenor | Signal | Δ |
|---|---|---|---|---|
| 14:41 | EURUSD | 1M | FAIR | +0.22 |
| 14:41 | EURUSD | 2M | FAIR | +0.26 |
| ... | | | | |

Couleurs via `data-severity` : FAIR neutre, CHEAP info vert, EXPENSIVE warn orange/rouge.

---

## 6. Les 3 indicateurs — rappel

### Yang-Zhang RV (Realized Volatility)

- **Input** : 254 barres daily OHLC sur EUR CONTFUT (1 an de trading days)
- **Calcul** : `core/vol/yang_zhang.py` — estimateur qui combine overnight jump, open-close et Rogers-Satchell range avec le coefficient k_YZ = 0.34 / (1.34 + (n+1)/(n−1))
- **Unité** : % annualisée
- **Interprétation** : la vol "qu'on aurait mesurée si on avait vendu / acheté du gamma continuellement sur le dernier an"

### GARCH(1,1) fair vol

- **Input** : les close prices des mêmes 254 barres (séries de log-returns)
- **Calcul** : `core/vol/garch.py` — fit GARCH(1,1), projette la variance conditionnelle à horizon `T` années, annualise
- **Unité** : % annualisée, **une valeur par tenor** (1M/2M/3M/4M/5M/6M)
- **Interprétation** : "ce que la vol devrait être à horizon T si le futur ressemble au passé récent selon GARCH"
- **Limite** : en régime stationnaire (pas de choc récent), la term structure GARCH est quasi-plate. En régime après choc (spike puis normalisation), elle descend avec le tenor. Ça capture le mean-reversion mais pas les chocs futurs.

### SVI (Stochastic Volatility Inspired)

- **Input** : 5 points observés (strike, IV) d'un seul tenor + le forward F et le tenor T
- **Calcul** : `core/vol/svi.py::fit_svi` — least squares bounded pour ajuster `(a, b, ρ, m, σ)` qui minimisent l'erreur quadratique sur la total variance
- **Paramètres interprétables** :
  - `a` : niveau asymptotique minimum
  - `b` : tightness / agressivité des wings
  - `ρ` : skew (négatif = put wing plus haute)
  - `m` : shift ATM
  - `σ` : convexité à l'ATM
- **Usage** : "fair smile" lissé — interpolation cohérente entre les pillars observés. Permet de détecter visuellement un strike outlier (point qui s'éloigne nettement du fit).

---

## 7. Pourquoi c'est cohérent entre tenors

Les 3 indicateurs utilisent **les mêmes 254 barres historiques** pour tous les tenors :
- La RV est **globale** (agrégée sur la fenêtre) → même valeur partout
- GARCH projette la **même variance initiale** à des horizons différents → valeurs proches entre tenors en régime calme
- SVI est **recalibré par tenor** → forme unique par tenor, peut diverger dans le skew

C'est normal que la term structure des courbes fair et RV soit presque plate en marché calme — c'est le signal d'un régime stationnaire, pas un bug.

---

## 8. Limitations connues

- **Market data delayed** : toute la chain est scannée en mode type 3 → valeurs à ±20 min. Acceptable pour smoke & dashboard, pas pour execution.
- **Chain qualification bruyante** : reqSecDefOptParams retourne des strikes historiquement listées dont certaines ne sont plus tradeable → `Error 200: No security definition` dans les logs. Filtré silencieusement, non bloquant.
- **GARCH sur continuous future** : CONTFUT stitch les expiries — les "rolls" peuvent créer des discontinuités artificielles dans les returns. Comportement légèrement biaisé au moment des changements de front. Acceptable sur 1 an, à considérer avant de prendre des décisions trade.
- **SVI avec 5 points et 5 params** : le fit est très bien ajusté mais peu contraint — pas de test de butterfly / calendar arbitrage. Pour un desk de production on ajouterait SSVI (surface fit) et/ou arbitrage filters.

---

## 9. Où toucher quoi

| Quoi ajuster | Fichier | Paramètre |
|---|---|---|
| Liste des tenors à scanner | `services/vol/chain_fetcher.py` | `DEFAULT_TARGET_DTES` |
| Nombre de strikes qualifiés par tenor | `services/vol/chain_fetcher.py` | `DEFAULT_STRIKES_PER_SIDE` |
| Concurrency tenor scan | `services/vol/chain_fetcher.py` | `DEFAULT_MAX_CONCURRENT` |
| Wait greeks (modelGreeks populating) | `services/vol/chain_fetcher.py` | `DEFAULT_GREEKS_WAIT_S` |
| Durée du fetch historique | `services/vol/main.py::_ohlc_real` | `duration_str` |
| TTL cache historical | `services/vol/historical_fetcher.py` | `CACHE_TTL_S` |
| Seuil CHEAP/FAIR/EXPENSIVE | `services/vol/engine.py` | `SIGNAL_ECART_THRESHOLD_PCT` |
| Mapping tenor → year fraction (GARCH horizons) | `services/vol/engine.py` | `DEFAULT_TENOR_T` |
| Nombre de points sur la courbe SVI | `api/services/vol_service.py` | argument `n_points` passé à `svi_curve` |

---

## 10. Commandes de diagnostic rapide

```powershell
# 1. Les 3 containers produisent bien du data
docker compose exec redis redis-cli GET heartbeat:market_data
docker compose exec redis redis-cli GET heartbeat:vol_engine
docker compose exec redis redis-cli GET heartbeat:db_writer

# 2. La surface Redis est fraîche
docker compose exec redis redis-cli GET latest_vol_surface:EURUSD | Select-Object -First 1

# 3. La DB a des rows récentes
docker compose exec postgres psql -U fxvol -d fxvol -c `
  "SELECT COUNT(*), MAX(timestamp) FROM vol_surfaces; SELECT COUNT(*), MAX(timestamp) FROM signals;"

# 4. Les 3 endpoints servent ce qu'il faut
curl.exe http://localhost/api/v1/vol/term-structure
curl.exe http://localhost/api/v1/vol/smile/1M
curl.exe "http://localhost/api/v1/signals?latest_per_tenor=true"

# 5. Le fetch historique a été exécuté
docker compose logs vol-engine | Select-String "fetch_daily_ohlc"

# 6. Les 6 tenors ont été scannés avec succès
docker compose logs vol-engine --tail=50 | Select-String "scan_one_tenor.*ok"
```
