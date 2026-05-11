# Portfolio tab — data flow par panel

Frontend : `frontend/src/pages/dev/Portfolio.tsx`
Polling global : `setInterval(refreshAll, 5000)` — 5s, REST uniquement, aucun WS.

---

## Panel A · Account detail (sticky header)

- Composant : `<Section title="A · Account detail">` + `<KV>` rows
- Fetch : `refreshHeader()` + `refreshAccount()`
- Endpoints :
  - `GET /api/v1/portfolio/header` → `{account, pnl, greeks, var_1d_99}`
  - `GET /api/v1/portfolio/account` → `{latest, prev_24h, freshness}`
- Routers : `src/api/routers/portfolio_panel.py` (lignes 82, 108)
- DB :
  - `account_snapshots` (latest row + row ≈24h prev)
  - `position_snapshots` (latest per position) pour l'agrégat greeks de header
- Writer DB : `execution-engine` (account_snapshots) + `risk-engine` (position_snapshots)
- Fréquence : 5s
- WS : non

---

## Panel F · Spot × Vol P&L grid (square)

- Composant : `<SquareCell><StressGrid /></SquareCell>`
- Fetch : `refreshStress()`
- Endpoint : `GET /api/v1/portfolio/stress-grid` → `{current_spot, spot_bins_bps, vol_bins_vps, grid, n_positions}`
- Router : `src/api/routers/portfolio_panel.py` (line 397)
- DB : `positions` (toutes les rows, market_price + iv + structure)
- Compute : full BS revaluation × 5 vol bins × 7 spot bins
- Writer DB : `risk-engine` UPDATE `positions.market_price/iv` à chaque cycle (2s)
- Dépendance Redis : `contract_marks:EUR` hash (publié par execution-engine) → consommé par risk-engine pour calculer `market_price`
- Fréquence : 5s
- WS : non

---

## Panel I · Vega bucket par tenor (square)

- Composant : `<SquareCell><VegaPerTenor /></SquareCell>`
- Fetch : `refreshVegaTenor()`
- Endpoint : `GET /api/v1/portfolio/vega-per-tenor` → `[{bucket, dte_lo, dte_hi, vega_usd, n_positions}]`
- Router : `src/api/routers/portfolio_panel.py` (line 297)
- DB : `positions` (WHERE structure LIKE 'EUU%', SELECT expiry, vega_usd)
- Compute : bucket par DTE en Python (1M / 2M / 3M / 6M / 1Y / 2Y+)
- Writer DB : `risk-engine` UPDATE `positions.vega_usd`
- Fréquence : 5s
- WS : non

---

## Panel H · Greeks ladder (square)

- Composant : `<SquareCell><GreeksLadder /></SquareCell>`
- Fetch : `refreshLadder()`
- Endpoint : `GET /api/v1/portfolio/greeks-ladder` → `{current_spot, spot_bins_bps, rows}`
- Router : `src/api/routers/portfolio_panel.py` (line 504)
- DB : `positions` (toutes les rows, market_price + iv + greeks)
- Compute : BS revaluation × 5 spot bins (-400 / -200 / 0 / +200 / +400 bp)
- Writer DB : `risk-engine` UPDATE `positions.*` à chaque cycle
- Fréquence : 5s
- WS : non

---

## Panel E · Open positions

- Composant : `<Section title="E · Open positions">` + table inline
- Fetch : `refreshPositions()`
- Endpoint : `GET /api/v1/positions/active` → `ActivePosition[]`
- Router : `src/api/routers/positions.py`
- DB :
  - `trade_positions` (state='open', booked structures)
  - `positions` (IB-live rows, post migration 028)
  - `trade_structures` (join pour metadata structure)
  - `position_mtm_history` (latest MTM par booked position)
- Writers DB :
  - `execution-engine` UPSERT `positions` (qty, market_price boot fallback)
  - `risk-engine` UPDATE `positions.*` (greeks + pnl + market_price)
- Redis fallback : hash `contract_marks:EUR` lu au boot avant le premier UPDATE risk-engine
- Fréquence : 5s
- WS : `positions` channel publié par execution-engine event-driven sur trades — **non consommé** par Portfolio aujourd'hui

---

## Panel G · P&L attribution daily

- Composant : `<Section title="G · P&L attribution">` + `<PnlAttribution />`
- Fetch : aucun (dérive du state `positions`)
- DB : `positions` (via Panel E)
- Compute : client-side, transpose `positions` array en lignes Greeks × colonnes positions
- Fréquence : suit Panel E (5s)
- WS : suit Panel E

---

## Panel J · Pin risk grid

- Composant : `<PinRiskSection positions={positions} spot={...} />`
- Fetch : aucun (dérive du state `positions` + `stress.current_spot`)
- DB : `positions` (via Panel E + F)
- Compute : client-side, filter `option_type ∈ {CALL, PUT}` + DTE compute
- Fréquence : suit Panel E + F (5s)
- WS : non

---

## Panel K · Margin / SPAN utilization

- Composant : `<MarginUtilization account={account?.latest} header={header} />`
- Fetch : aucun (dérive de `account.latest` + `header.greeks`)
- DB : `account_snapshots` + `position_snapshots` (via Panel A)
- Compute : client-side, lignes margin + Greek exposure vs NetLiq
- Fréquence : suit Panel A (5s)
- WS : non

SPAN scenario rows : `TODO` — requirent IB RiskNavigator API (non câblé, backlog post-obs v1.0)

---

## Chaîne complète IB → display

```
IB Gateway
  ↓ ib_insync (clientId 1/2/3/5)
  ├─ market-data    → ticks → Redis pub/sub `ticks`           → /ws/ticks   (frontend WS Monitor uniquement)
  ├─ vol-engine     → surface → Redis pub/sub `vol_update`    → /ws/vol     (frontend WS Monitor uniquement)
  ├─ risk-engine    → greeks agrégés → Redis pub/sub `risk_update` → /ws/risk (frontend WS Monitor uniquement)
  │                  + UPDATE positions.* (DB) ← lu par tous les endpoints REST Portfolio
  └─ execution-engine
       ↓ Redis hashes `contract_marks:EUR` + `option_marks:EUR` + `unrealized_pnl:EUR`
       ↓ UPSERT positions (qty, boot market_price)
       ↓ INSERT/UPDATE account_snapshots
       ↓ Redis pub/sub `positions` (event-driven sur trade)   → /ws/positions (frontend non-consommé)
       ↓ Redis pub/sub `account`                               → /ws/account   (frontend non-consommé)
       ↓ Redis pub/sub `orders:*`                              → /ws/orders    (frontend non-consommé)

DB (postgres)
  ↓ SQLAlchemy AsyncSession
  ↓ api routers (portfolio_panel.py, positions.py)
  ↓ FastAPI JSON response
  ↓ fetch() côté Portfolio.tsx
  ↓ setState
  ↓ render panel
```
