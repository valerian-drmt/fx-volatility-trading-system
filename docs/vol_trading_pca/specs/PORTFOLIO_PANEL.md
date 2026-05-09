# Portfolio Panel — gestion compte / risques / activité

> Onglet frontend dédié à la **gestion de portfolio**, séparé des onglets
> "vol analysis" (Step 1/2), "trade workflow" (Step 3/4) et "live monitoring"
> (Step 5).
>
> **Scope** : tout ce qui concerne le **compte** et le **portfolio** —
> account summary, équity curve, greeks agrégés, distribution par tenor,
> historique d'activité (orders / trades / fills / hedges / snapshots).
>
> **Hors scope** :
> - Analyse de marché (régime, signaux PCA, surface vol) → Step 1/2
> - Pré-trade (preview, sizing) → Step 3
> - Post-trade execution (booking, fills) → Step 4
> - Monitoring per-position avec exit rules → Step 5
>
> **Status** : draft — à valider avant code.
> **Date** : 2026-05-09

---

## 1. Objectif & motivation

Aujourd'hui les données portfolio sont éparpillées :
- `account_snaps` (NetLiq, margin) écrit par execution-engine, **non lu** par aucun panneau dédié
- `positions` IB lu par Portfolio panel (legacy) mais sans agrégation
- `position_snapshots` (1.4 M lignes) jamais affiché ailleurs que dans Orders tab
- `orders` / `trades` / `hedge_orders` lus chacun par leur onglet de l'écosystème step

→ Pas de **vue unifiée** "où en est mon book aujourd'hui". Cet onglet la fournit.

---

## 2. Architecture

```
                                      poll 5s
   ┌──────────────────┐    REST    ┌──────────────────────┐
   │  Portfolio panel │  ───────►  │  /api/v1/portfolio/* │
   │   (frontend)     │  ◄───────  │   /api/v1/positions  │
   └──────────────────┘            │   /api/v1/orders     │
                                   │   /api/v1/dev/tables │
                                   └──────────┬───────────┘
                                              │ SELECT
                                              ▼
                                       ┌───────────┐
                                       │ Postgres  │  (tables alimentées
                                       └───────────┘   par execution-engine
                                                       toutes les 30s)
```

**Single source of truth = Postgres**. Aucun appel IB direct depuis le
panneau. Toutes les écritures DB sont déjà faites par `position_sync`
(execution-engine, 30s) ; le panneau est read-only.

---

## 3. Layout — 9 sections, 1 page scrollable

```
┌───────────────────────────────────────────────────────────────┐
│ A — Account header                                             │
│  Net Liq │ Cash │ Margin used │ Excess Liq │ Cushion% │ #pos  │
├───────────────────────────────────────────────────────────────┤
│ B — Equity curve (net_liq vs time, 30j Plotly line)            │
├──────────────────────────────────┬────────────────────────────┤
│ C — Aggregate greeks              │ D — Vega per tenor (bar)  │
│  Σ Δ │ Σ Γ │ Σ V │ Σ Θ            │  1M │ 2M │ 3M │ 4M │ ≥6M  │
├──────────────────────────────────┴────────────────────────────┤
│ E — Open positions table (union trade_positions + positions)   │
├───────────────────────────────────────────────────────────────┤
│ F — Open orders (working, not yet filled)                      │
├───────────────────────────────────────────────────────────────┤
│ G — Trades / fills (last 50)                                   │
├───────────────────────────────────────────────────────────────┤
│ H — Hedge orders log                                           │
├───────────────────────────────────────────────────────────────┤
│ I — Position snapshots history (last 100, paginable)           │
└───────────────────────────────────────────────────────────────┘
```

Le pattern visuel (titre + bouton Refresh + table) reprend
`OrderSubmit.tsx` pour rester cohérent avec Orders tab.

---

## 4. Détail par section

### A — Account header

**À voir** : valeurs latest de `account_snaps`, avec delta vs row précédente.

| Champ | Source | Note |
|---|---|---|
| Net Liq | `account_snaps.net_liq_usd` | + Δ$ vs 24h |
| Cash | `account_snaps.cash_usd` | |
| Init margin req | `account_snaps.init_margin_req` | |
| Maint margin req | `account_snaps.maint_margin_req` | |
| Excess liquidity | `account_snaps.excess_liquidity` | rouge si < 5% net liq |
| Cushion | `account_snaps.cushion` | rouge si < 0.05 |
| # open positions | `account_snaps.open_positions_count` | |
| Last update | `account_snaps.timestamp` | "fresh"/"stale" badge (cf. Step 5) |

**Endpoint** : `GET /api/v1/portfolio/account` → `{ latest: {...}, prev_24h: {...} }`.
À créer (n'existe pas).

### B — Equity curve

**À voir** : ligne Plotly `net_liq_usd` sur 30 j (downsample à 1 point /
5 min — 8640 points max).

**Endpoint** : `GET /api/v1/portfolio/equity-curve?window=<1d|7d|30d|1y|all>` →
`[{timestamp, net_liq_usd, is_eod}, ...]`. À créer.

**Adaptive downsampling** (pattern industriel — Bloomberg / RiskMetrics) :
constant ~1–2 k points quelle que soit la fenêtre.

| Window | Granularité serveur | Points |
|---|---|---|
| `1d`  | 1 pt / 1 min  | ~1440 |
| `7d`  | 1 pt / 5 min  | ~2016 |
| `30d` | 1 pt / 30 min | ~1440 |
| `1y`  | 1 pt / 4 h    | ~2190 |
| `all` | 1 pt / 1 jour (EOD) | < 1000 |

**EOD markers** : `is_eod=true` sur les rows correspondant à la dernière
valeur avant 22:00 UTC (close FX) → affichage Plotly en pointillé pour
distinguer audit canonical truth vs intraday tick.

**MVP** : courbe seule (pas de drawdown / Sharpe).

### C — Aggregate greeks

**À voir** : 4 cards style Step 5 actuel.
- Σ Δ (unhedged) — somme des `position_snapshots.delta_usd` pour positions OPEN
- Σ Γ ($/pip²) — idem `gamma_usd`
- Σ V ($/volpt) — idem `vega_usd`
- Σ Θ ($/jour) — idem `theta_usd`

**Endpoint** : `GET /api/v1/portfolio/aggregate-greeks` → `{ delta, gamma, vega, theta, n_positions, computed_at }`.
À créer (l'`/positions/aggregate` actuel ne lit que `trade_positions`,
pas les rows IB).

### D — Vega per tenor

**À voir** : bar chart vega ($/volpt) ventilé par bucket d'expiry.
- Buckets : 0–30 j (1M), 31–60 j (2M), 61–90 j (3M), 91–120 j (4M), 121–180 j (5M-6M), > 180 j (long)
- Couleur : vert si vega positif (long), rouge si court
- Tooltip : nb positions du bucket

**Endpoint** : `GET /api/v1/portfolio/vega-per-tenor` →
`[{bucket: "1M", vega_usd: 4200, n_positions: 2}, ...]`.
À créer.

**Implémentation** : group-by sur `(maturity - now).days` côté SQL.

### E — Open positions table

**À voir** : tableau actuel de Step 5 (union booked + ib_live).

**Endpoint** : `GET /api/v1/positions/active` (déjà fait, pas de changement).

**Réutilise** : composant `<PositionsTable>` factorisé depuis `Step5Positions.tsx`
pour éviter la duplication. Step 5 garde son onglet pour les
exit rules / signal tracking (concept different : monitoring per-trade).

### F — Open orders

**À voir** : table identique à Orders tab section "Open orders" mais
read-only (pas de bouton Cancel ici — ça reste dans Orders).

| Col | Source |
|---|---|
| Order ID | `orders.id` |
| Symbol + expiry + strike + right | `orders.{symbol, expiry, strike, right}` |
| Side / Qty | `orders.{side, quantity}` |
| Limit price | `orders.limit_price` |
| Status | `orders.status` |
| Filled / total | `orders.{filled_qty, quantity}` |
| IB order ID | `orders.ib_order_id` |
| Submitted at | `orders.created_at` |

**Endpoint** : `GET /api/v1/orders?status=working` (existe — extend avec filtre status).

### G — Trades / fills

**À voir** : 50 derniers fills.

| Col | Source |
|---|---|
| Trade ID | `trades.id` |
| Position ID | `trades.position_id` (link cliquable vers section E) |
| Side / Qty | `trades.{side, quantity}` |
| Price | `trades.price` |
| Commission | `trades.commission` |
| Timestamp | `trades.timestamp` |
| IB order ID | `trades.ib_order_id` |

**Endpoint** : `GET /api/v1/dev/tables/trades?limit=50` (existe).

### H — Hedge orders

**À voir** : log des hedges delta cumulés sur la durée.

| Col | Source |
|---|---|
| ID / Position | `hedge_orders.{id, position_id}` |
| Triggered at | `hedge_orders.triggered_at` |
| Δ imbalance | `hedge_orders.delta_imbalance_at_trigger` |
| Side / Qty | `hedge_orders.{side, hedge_qty}` |
| Fill price | `hedge_orders.fill_price` |
| Cost | `hedge_orders.total_cost_usd` |
| State | `hedge_orders.state` |

**Footer** : multi-window summary (today | WTD | MTD | rolling 7d | rolling 30d | YTD).
Pattern Risk Ops standard : un drift se voit en comparant les fenêtres
(today brutal vs 7d normal → événement local ; 30d en hausse vs today
calme → drift structurel).

**Endpoints** :
- `GET /api/v1/dev/tables/hedge_orders?limit=100` — log brut (à ajouter au router `dev.py`).
- `GET /api/v1/portfolio/hedge-summary` — multi-window cumul :
  ```jsonc
  {
    "today":       { "n_hedges": 0,  "cum_cost_usd": 0 },
    "wtd":         { "n_hedges": 4,  "cum_cost_usd": -180 },
    "mtd":         { "n_hedges": 12, "cum_cost_usd": -420 },
    "ytd":         { "n_hedges": 89, "cum_cost_usd": -2380 },
    "rolling_7d":  { "n_hedges": 6,  "cum_cost_usd": -240 },
    "rolling_30d": { "n_hedges": 14, "cum_cost_usd": -510 }
  }
  ```

### I — Position snapshots history

**À voir** : derniers 100 rows de `position_snapshots`, paginé.

Colonnes : id, position_id, timestamp, spot, iv, delta_usd, gamma_usd,
vega_usd, theta_usd, pnl_usd.

**Endpoint** : `GET /api/v1/dev/tables/position_snapshots?limit=100&offset=0` (existe).

**Note** : 1.4 M lignes en DB ; toujours limiter à 100 + offset paginé.
Filtre optionnel `?position_id=X` pour drill-down.

---

## 5. Endpoints API à créer (6 au total)

| Endpoint | Source DB | Logique |
|---|---|---|
| `GET /api/v1/portfolio/account` | `account_snaps ORDER BY timestamp DESC LIMIT 2` | Latest + prev (24h-ish) |
| `GET /api/v1/portfolio/equity-curve?window=<1d\|7d\|30d\|1y\|all>` | `account_snaps WHERE timestamp >= now-window` | Adaptive downsampling ~1–2k pts |
| `GET /api/v1/portfolio/aggregate-greeks` | `position_snapshots latest per position` SUM | Réutiliser `_compute_position_metrics` style |
| `GET /api/v1/portfolio/vega-per-tenor` | `positions JOIN latest position_snapshots GROUP BY tenor_bucket` | bucket = days_to_expiry binning |
| `GET /api/v1/portfolio/hedge-summary` | `hedge_orders WHERE state='filled'` | Multi-window cumul (today/WTD/MTD/YTD/r7d/r30d) |

Tous read-only, tous async, tous sous `/api/v1/portfolio/*` (nouveau router).
Pas de migration DB nécessaire — les tables existent déjà.

À ajouter aussi : `GET /api/v1/dev/tables/hedge_orders` (1-liner dans router `dev.py`).

---

## 6. Frontend — nouveau composant

**Fichier** : `frontend/src/pages/dev/Portfolio.tsx`.

**Tab** : à ajouter dans `DevLayout.tsx` :
```tsx
{ id: "portfolio", label: "💼 Portfolio", Component: Portfolio },
```

**Pattern réutilisé** : `<TableSection title onRefresh>` + `<GenericTable rows cols>`
copiés depuis `OrderSubmit.tsx` (factorisation possible plus tard
dans `frontend/src/components/common/TableSection.tsx`).

**Polling** : 5 s pour A / C / D / E / F (live). 30 s pour B (equity
curve change peu). Manuel pour G / H / I (boutons Refresh).

**Plotly** : pour la courbe equity (B) et le bar chart vega (D).
Déjà installé (`plotly.js` cf. CLAUDE.md).

---

## 7. Plan d'implémentation — 3 phases

| Phase | Scope | New endpoints | Effort | Bloquant |
|---|---|---|---|---|
| **P1** Sections A/E/F/G/I | 1 (`/portfolio/account`) — E/F/G/I réutilisent existant | nouveau tab + 5 tables | 1 j | Oui |
| **P2** Sections B/C/D | 3 (`equity-curve` / `aggregate-greeks` / `vega-per-tenor`) | + Plotly equity + bar chart vega | 1.5 j | Oui |
| **P3** Section H | 2 (`hedge-summary` + `/dev/tables/hedge_orders`) | + footer multi-window cumul | 0.5 j | Oui |

**Total ~3 j** pour la version complète, **6 nouveaux endpoints**.

---

## 8. Definition of done

- [ ] Router `src/api/routers/portfolio_panel.py` (à part de `portfolio.py` legacy) avec 4 endpoints
- [ ] `dev.py` : table `hedge_orders` exposée
- [ ] Frontend `Portfolio.tsx` nouvelle tab `💼 Portfolio` dans `DevLayout`
- [ ] 9 sections rendues avec données réelles
- [ ] Polling 5s sur live blocks, refresh manuel sur historique
- [ ] Pas de call IB direct depuis le frontend
- [ ] Tests : unit sur les agrégations greeks + bucketing tenor
- [ ] Doc à jour

---

## 9. Décisions actées

1. **Routers séparés par bounded context** : `/portfolio/*` (vue agrégée
   compte/book) distinct de `/positions/*` (monitoring per-position Step 5).
   Cohérent avec DDD, OpenAPI tags propres, RBAC scopes futurs distincts.
2. **Position snapshots** : `limit=100 ORDER BY timestamp DESC` par
   défaut, filtre `?position_id=X` en option.
3. **Equity curve** : adaptive downsampling par window (1d/7d/30d/1y/all),
   cible ~1–2 k points constant. EOD markers (`is_eod=true`) sur la
   dernière valeur intraday < 22:00 UTC. Pattern Bloomberg/RiskMetrics.
4. **Hedge cumulative** : multi-window dans une réponse (today/WTD/MTD/
   YTD/rolling 7d/rolling 30d). Pattern Risk Ops — un drift se détecte
   en comparant plusieurs fenêtres simultanément.

---

## 10. Ce qui n'est PAS dans cette spec (à plus tard)

- **Stress / scenario testing** : matrice spot × IV — utile mais gros morceau UX, à mettre en V2.
- **Risk limits** : Vega budget, concentration alerts — nécessite une table `risk_limits_config` à designer.
- **Per-strategy P&L** : breakdown par signal PCA / structure type — relevant for analytics dashboard, pas portfolio quotidien.
- **Win rate / holding period stats** : analytics historique post-clôture, V2.
- **Export CSV / report PDF** : nice-to-have, pas MVP.
