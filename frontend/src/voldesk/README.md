# VOLDESK — user-facing frontend

The live FX-vol trading desk served at `/`. Originally ported from a standalone
HTML prototype into this repo's **Vite + React 18 + strict TypeScript** stack,
now fully wired to the backend (typed OpenAPI client + WS streams).

## Structure

```
voldesk/
├── VoldeskApp.tsx       shell: Topbar + Rail (incl. Dev tab) + hash routing
├── voldesk.css          CSS (extracted verbatim from the prototype)
├── useTweaks.ts         accent / density / rail-labels (persisted to localStorage)
├── components/
│   ├── common.tsx       UI primitives (Panel, Tag, MetricTile, MiniStat, Bar, Delta, StatusDot)
│   ├── format.ts        pure helpers (pnlCls, gk$ [with $], signalTone)
│   ├── charts.tsx       Heatmap + Donut (SVG, no chart lib)
│   ├── PositionsTable.tsx  OpenPositionsTable + CashHoldings (local gk$ WITHOUT $)
│   └── OrderBuilder.tsx    multi-leg builder (exports BuilderState for TradeView)
├── data/
│   ├── core.ts          desk constants + shared domain types (formatters, tenors,
│   │                    deltas, reference SPOT for the order preview, smileFor)
│   ├── neutral.ts       honest empty states (EMPTY_ACCOUNT / EMPTY_GREEKS)
│   ├── extended.ts      shared portfolio/system type exports
│   ├── live/            REST/WS adapters, one module per data family
│   ├── provider.tsx     DataProvider (desk slices) + TicksProvider (1 Hz spot)
│   └── deskData.ts      context hooks: useDeskData() / useTicks()
└── views/               one view per rail tab
    DashboardView · SignalsView · RiskView · PortfolioView · TradeView · SystemView · SettingsView
```

## Routing (in `src/main.tsx`)

**Path**-based, base-aware (`import.meta.env.BASE_URL`):
- `/`        → **VoldeskApp** (this desk)
- `/dev/*`   → `DevLayout` (operator console, lazy-loaded — do NOT merge in here)

VOLDESK routes its own views **internally by hash** (`#trade`, `#risk`…). The
rail's **Dev** tab does a full-page navigation to `${BASE_URL}dev`.

## Data

All views read live data through `useDeskData()` / `useTicks()` (context over
the typed OpenAPI client + `/ws/ticks|vol|risk` streams). There is **no mock
fallback**: when the backend has no data yet, views render zeros / empty rows
plus the per-panel `FreshBadge` state — never fabricated numbers.

## Deployment & security

- **Single URL** `valeriandarmente.dev`, app under the
  `/fx-volatility-trading-system` subpath (`vite base`); API/WS proxied by
  nginx from the same origin.
- **Read/write boundary**: reads are public; writes (Trade tab order submit,
  admin config) and the `/dev` console require the write-auth cookie — see
  `SECURITY.md` at the repo root.

## Port notes

Faithful 1:1 port (same classNames → same CSS). Prototype dead code was never
ported: `IVSurface` canvas + `ComboZStrip` (Signal), `MarketDataBlock` (Trade),
`activeSignal`/`PcCard`, and the off-nav `TestView` with its exclusive
components (`CandleChart`, `GaussCurve`, `Sparkline`, `ZGauge`, `VolSurface3D`,
`SurfaceLab`).
