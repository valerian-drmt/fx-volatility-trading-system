# VOLDESK — frontend utilisateur

Dashboard de trading FX vol **côté utilisateur** (la partie « live »), porté depuis
un prototype HTML autonome (React via Babel CDN, JSX global-`window`) vers la stack
**Vite + React 18 + TypeScript strict** de ce repo. Sert la route `/`.

## Structure

```
voldesk/
├── VoldeskApp.tsx       shell : Topbar + Rail (incl. onglet Dev) + routing par hash
├── voldesk.css          CSS (extrait verbatim du prototype)
├── useTweaks.ts         accent / densité / rail-labels (persistés localStorage)
├── components/
│   ├── common.tsx       primitives UI (Panel, Tag, MetricTile, MiniStat, Bar, Delta, StatusDot)
│   ├── format.ts        helpers purs (pnlCls, gk$ [avec $], signalTone)
│   ├── charts.tsx       Heatmap + Donut (SVG, sans lib graphique)
│   ├── PositionsTable.tsx  OpenPositionsTable + CashHoldings (gk$ local SANS $)
│   └── OrderBuilder.tsx    builder multi-leg (exporte BuilderState pour TradeView)
├── data/                couche mock TYPÉE — point de bascule vers l'API
│   ├── core.ts          marché / vol / pca / regime / positions / compte / greeks
│   ├── extended.ts      risk / stress / attribution / system / config
│   └── index.ts         barrel : { DATA, DATA2, fmt, scenarioSeries, … }
└── views/               une vue par onglet
    DashboardView · SignalsView · RiskView · PortfolioView · TradeView · SystemView · SettingsView
```

## Routing (dans `src/main.tsx`)

Routing par **path**, base-aware (`import.meta.env.BASE_URL`) :
- `/`        → **VoldeskApp** (ce front)
- `/dev/*`   → `DevLayout` (console interne — NE PAS fusionner ici)
- `/config`  → `VolEngineConfigPage`

VOLDESK route ses propres vues en **interne par hash** (`#trade`, `#risk`…). L'onglet
**Dev** du rail fait une navigation full-page vers `${BASE_URL}dev`.

## Données : mock → backend

Toute la couche `data/` est **synthétique** (PRNG). C'est le **point de bascule** : quand
on câble une vue au backend, on remplace ses imports `from "../data"` par le client
OpenAPI typé + les hooks WS (`/ws/ticks`, `/ws/vol`, `/ws/risk`). Les vues restent
agnostiques de la source.

## Déploiement & sécurité (décidé 2026-06-13)

- **Une seule URL** `valeriandarmente.dev` (Route 53). App sous le sous-chemin
  `/fx-volatility-trading-system` (le routing est déjà base-portable → `vite base` en 1 ligne).
  API/WS restent à la racine (`/api/v1`, `/ws`). Backend **live**.
- **Frontière lecture/écriture** (auth à implémenter, cf. plus bas) :
  lecture publique (surface, signal, risk, portfolio) ; **écriture auth-gatée**
  (soumission d'ordre onglet Trade, `/config`, `/dev`). Un visiteur anonyme ne doit
  jamais passer d'ordre.

## Reste à faire

1. **Auth** (différée) : login admin → JWT cookie ; middleware FastAPI protégeant
   ordres + `/config` + `/dev`.
2. **Câblage backend** vue par vue (remplacer `data/` par l'API).
3. **`vite base`** = `/fx-volatility-trading-system/` le jour du déploiement.

## Notes de port

Port fidèle 1:1 (mêmes classNames → même CSS). Code mort du prototype non porté car
jamais rendu par les vues livrées : `IVSurface` canvas + `ComboZStrip` (Signal),
`MarketDataBlock` (Trade), `activeSignal`/`PcCard`, et la `TestView` (banc d'essai hors
nav) avec ses composants exclusifs (`CandleChart`, `GaussCurve`, `Sparkline`, `ZGauge`,
`VolSurface3D`, `SurfaceLab`).
