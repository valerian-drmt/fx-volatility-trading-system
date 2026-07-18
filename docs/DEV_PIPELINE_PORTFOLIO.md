# Dev Pipeline — Portfolio domain rebuild (structure)

Plan for redoing the **Portfolio** section of the dev "Pipeline" tab
(`frontend/src/pages/dev/pipelines.ts`). The sidebar shows one big "Portfolio"
group whose sub-entries each map to ONE panel (or sub-panel) of the live
Portfolio view — modelled on the existing Risk and Signals entries.

## Why a rebuild

The 5 current entries predate the Portfolio view rebuild (PRs #216–#220) and
are stale:

| Current entry | Status |
|---|---|
| `account` — Account & capital | kept, but the panel is now a composite of 4 sub-blocks worth their own entries |
| `perf` — Performance | kept, but the panel changed shape entirely (P&L/drawdown + greek-P&L 2×2) |
| `carry-convex` — Carry vs convexity | **panel no longer exists** → dead sidebar row |
| `pnl-attribution` — Realized P&L attribution | **stale id** — the view now has `attrib-tenor` + `attrib-leg`, so panel isolation is broken |
| `book-composition` — Book composition | **panel no longer exists** (moved into Trade/Risk position tables) |

## Inventory of the live Portfolio view (today)

`frontend/src/voldesk/views/PortfolioView.tsx` renders 4 top-level panels;
two are composites:

1. **Account & capital** (`data-pp="account"`)
   - a. Cash & margin table (net liq + 24h pill, cash, margins % used, excess liq, cushion)
   - b. Leverage & buying power table (gross/net leverage ×, buying power)
   - c. Holdings valuation (net-liq decomposition: USD cash / EUR cash $ / contracts)
   - d. Portfolio valuation chart (windowed stacked bands, assets from 0 / debts from top)
2. **Performance** (`data-pp="perf"`)
   - a. P&L equity curve + drawdown underwater plot (windowed, trade markers overlay)
   - b. 2×2 Taylor greek-P&L grid (delta / gamma / vega / theta cumulative $)
3. **P&L attribution by tenor** (`data-pp="attrib-tenor"`)
4. **P&L attribution by trade** (`data-pp="attrib-leg"`)

## Proposed sidebar structure — 8 sub-entries

Composite panels are split so each sub-entry documents ONE real data flow
(new `data-pp` anchors added to the sub-blocks for CSS isolation, same
mechanism as everywhere else):

| # | id | Sidebar label | data-pp anchor | Endpoint | Source chain |
|---|----|---------------|----------------|----------|--------------|
| 1 | `acct-cash-margin` | Cash & margin | `acct-cash-margin` *(new)* | `GET /portfolio/account` | IB account summary → execution-engine snaps → db_events → db-writer → `account_history` (latest + prev_24h) |
| 2 | `acct-leverage` | Leverage & buying power | `acct-leverage` *(new)* | client-side over `/positions` + account + WS ticks | risk-engine book (`open_position` notionals) + IB heartbeat (buying power) + live spot (€↔$ conversion) |
| 3 | `acct-holdings` | Holdings valuation | `acct-holdings` *(new)* | `GET /portfolio/cash` | `account_history.currencies` (CashBalance per ccy) + `vol_surface.spot` (EUR→$) |
| 4 | `acct-valuation` | Portfolio valuation chart | `acct-valuation` *(new)* | `GET /portfolio/valuation-history?window=` | `account_history` bucketed (DISTINCT ON) → USD cash / EUR cash (surface spot) / contracts residual bands |
| 5 | `perf-equity` | Performance — P&L & drawdown | `perf-equity` *(new)* | `GET /portfolio/equity-curve` + `GET /portfolio/trade-markers` | `account_history` net-liq series (server-side downsample) ⊕ `booked_position` open/close events |
| 6 | `perf-greek-pnl` | Performance — greek P&L grid | `perf-greek-pnl` *(new)* | `GET /portfolio/greek-pnl-history?window=` | risk-engine 2s snaps → `open_position_history` → per-bucket Taylor terms (greeks@start, dS from 6E forward, dσ from leg IV) |
| 7 | `attrib-tenor` | P&L attribution by tenor | `attrib-tenor` *(exists)* | `GET /portfolio/pnl-attribution?group_by=tenor` | `open_position_history` now-vs-then Taylor decomposition, bucketed by tenor |
| 8 | `attrib-leg` | P&L attribution by trade | `attrib-leg` *(exists)* | `GET /portfolio/pnl-attribution` | same decomposition, one row per booked leg, grouped by trade |

Cadences: 1–3 follow the portfolio domain beat; 4–8 poll ~120s with a
window/param (annotate with `cadence:` like `stress` / `pin-risk` do).

## DAG shapes (following the Risk examples)

- **1, 4, 5 (account-history flows)** → `dagPersist` archetype:
  `IB → ib-gateway → execution-engine → Redis(db_events) → db-writer →
  Postgres(account_history) → api → frontend → panel`. Entry 5 adds a second
  Postgres input (`booked_position` → trade-markers) merging at the api node —
  small custom DAG, not plain `dagPersist`.
- **2 (leverage)** → custom join DAG (like `pin-risk`): two store inputs
  (`open_position` notionals + account snapshot) plus the live ticks WS,
  merging in a frontend-compute node (the ×-ratios are computed client-side —
  worth showing honestly as a `frontend` transform node).
- **3 (holdings valuation)** → short persist DAG with a second read input
  (`vol_surface.spot` for the EUR leg) merging at the api node.
- **6, 7, 8 (Taylor flows)** → custom api-compute DAGs (like `marginal-var`):
  explicit api transform nodes for the decomposition steps
  (`read snapshots` → `bucket / pick then-vs-now` → `δ·dS` / `½Γ·dS²` / `V·dσ`
  / `Θ·dt` → `assemble matrix`) so the dev page teaches how the numbers are
  actually produced.

## Implementation checklist

1. `PortfolioView.tsx`: add the 6 new `data-pp` anchors on the sub-blocks
   (`acct-cash-margin`, `acct-leverage`, `acct-holdings`, `acct-valuation`,
   `perf-equity`, `perf-greek-pnl`) — top-level panels keep their existing ids.
2. `pipelines.ts`: delete `carry-convex`, `book-composition`, `pnl-attribution`;
   replace `account` + `perf` with the 8 entries above (flat `nodes`/`edges`
   chain + full `dag` each, `isolated: true`).
3. Check the pipeline page's isolation CSS works for nested `data-pp` blocks
   (sub-block inside a panel) — the Trade view's `trade-indicators` case is the
   precedent.
4. Verify each entry's health roll-up: engines referenced = `exec-engine`,
   `risk-engine`, `db-writer`, `postgres`, `redis`, `__api`, `__self` (same
   keys as the Risk entries).
5. Frontend gates: lint + typecheck + vitest + build; eyeball the dev Pipeline
   tab (sidebar shows 8 Portfolio rows, each schema renders, panel isolation
   shows the right block).
