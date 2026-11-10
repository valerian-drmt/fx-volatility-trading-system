# Documentation

The **FX Volatility Trading System** is an end-to-end platform for trading EUR/USD
FX options. A live Interactive Brokers feed flows through five async Python engines
that fit the volatility surface (SVI / SSVI / GARCH / HAR-RV), detect the market
regime (GMM), and reduce the 30-dimensional surface to PCA signals; the desk then
submits delta-hedged option structures and books every fill into a versioned
Postgres audit trail. A React trading desk (voldesk) serves it all over REST + WebSockets.
The public deployment at
[valeriandarmente.dev/fx-volatility-trading-system](https://valeriandarmente.dev/fx-volatility-trading-system/)
is read-only; trading, config, and the `/dev` console sit behind an auth boundary.

These docs describe the system as it is built. Diagrams are **SVG** files under
[`diagrams/`](diagrams/), embedded inline in the relevant `.md`.

## Architecture

| Doc | Covers |
|---|---|
| [architecture/overview.md](architecture/overview.md) | The platform in one page: what it trades, the 11-container core stack (6 ship our code) + optional obs stack, the live deployment. |
| [architecture/backend.md](architecture/backend.md) | The `src/` layout (`api` / `core` / `engines` / `persistence` / `bus` / `shared`) and the five import-linter contracts. |
| [architecture/data-flow.md](architecture/data-flow.md) | The live path: IB → engines → Redis pub/sub + cache → db-writer → Postgres; api reads DB + Redis; frontend via REST + WS. |
| [architecture/frontend.md](architecture/frontend.md) | The voldesk React app: 7 views, zustand stores, the typed OpenAPI client, WS hooks, the `/dev` console. |
| [architecture/database.md](architecture/database.md) | The Postgres schema: 31 ORM classes grouped by domain, Alembic flow, `VolConfig` versioning. |
| [architecture/websockets.md](architecture/websockets.md) | Every `/ws/*` stream: Redis channel, cadence, desk reader, one message example each (generated from the payload fixtures). |
| REST reference | `/api/openapi.json` (public; Swagger at `/api/docs` locally): every operation described, `traderCookie` on the gated ones, error codes per route. |
| [architecture/hot-cold-paths.md](architecture/hot-cold-paths.md) | Hot path (orders, fills, positions, account) vs cold path (surface, PCA, rollups): the six engines, the three collision points behind the November 2026 incidents, and the changes that separate them. |

## Planning

| Doc | Covers |
|---|---|
| [planning/iteration-plan.md](planning/iteration-plan.md) | The operational plan: 15 two-week iterations over the 71 open work tickets (audit campaign + hot / cold programme), ordering rules, dependency map, capacity model, review cadence. |

## Hot / cold path programme

| Doc | Covers |
|---|---|
| [hot-cold-path/README.md](hot-cold-path/README.md) | The programme: goal, principles, reading order. |
| [hot-cold-path/01-problems.md](hot-cold-path/01-problems.md) | Problem register P1 to P10: shared pool, all-or-nothing bundles, equal CPU priority, write side, polled hot state, per-request cold compute, freshness that never ages, no latency metric, chain load, duplicate analytics. |
| [hot-cold-path/02-tasks.md](hot-cold-path/02-tasks.md) | Task plan T1 to T10 with acceptance, dependencies and estimates; the dependency map. |

## Volatility modeling

| Doc | Covers |
|---|---|
| [vol-modeling/pca-signals.md](vol-modeling/pca-signals.md) | PCA on the surface snapshot → PC1/PC2/PC3 (level/slope/curvature) → z-scores → signals. |
| [vol-modeling/volatility-surface.md](vol-modeling/volatility-surface.md) | SVI / SSVI calibration, smile, term structure, delta/tenor pillars, PCHIP, surface-Z. |
| [vol-modeling/forecasting.md](vol-modeling/forecasting.md) | Realized-vol models: GARCH, HAR-RV, Yang-Zhang, VRP. |
| [vol-modeling/regime.md](vol-modeling/regime.md) | GMM regime detection and how the regime gates signals. |
| [vol-modeling/pricing.md](vol-modeling/pricing.md) | The one Black-Scholes kernel: inputs, boundary (intrinsic at expiry), the undiscounted-convention decision. |
| [vol-modeling/units.md](vol-modeling/units.md) | Units at every boundary: greeks (USD / per pip / per vol point), IV fraction vs percent, IB price points, P&L attribution terms. |
| [vol-modeling/notebooks/pca_signal_pipeline_explained.ipynb](vol-modeling/notebooks/pca_signal_pipeline_explained.ipynb) | Runnable walk-through of the PCA signal pipeline. |

## Strategy

| Doc | Covers |
|---|---|
| [strategy/structures.md](strategy/structures.md) | Straddle, strangle, risk reversal, butterfly, calendar — built by delta pillar + tenor. |
| [strategy/signals-to-trades.md](strategy/signals-to-trades.md) | Mapping PC signals to structures (level→straddle, slope→calendar, curvature→butterfly, skew→risk-reversal). |
| [strategy/risk.md](strategy/risk.md) | Greeks (Δ/Γ/V/Θ/vanna/volga), VaR, greek limits, P&L attribution, delta hedging. |

## Execution

| Doc | Covers |
|---|---|
| [execution/oms.md](execution/oms.md) | Order management: free-legs book, structure submit, reconciliation, reaper/projection/reservation. |
| [execution/order-lifecycle.md](execution/order-lifecycle.md) | Submit → IB → fills → booked position; states, idempotency, partial fills. |
| [execution/ib-integration.md](execution/ib-integration.md) | IB Gateway, ib_insync, the four client IDs, single-session-per-userid, paper vs live. |
| [execution/position-figures.md](execution/position-figures.md) | Which table feeds Position breakdown vs P&L attribution by tenor / by trade; units per figure; the footing invariants. |

## Observability

| Doc | Covers |
|---|---|
| [observability/conventions.md](observability/conventions.md) | Metric naming + label-cardinality rules (Prometheus / OTel). |
| [observability/runbooks.md](observability/runbooks.md) | Loki / Prometheus / Tempo / Grafana operator playbooks. |

## Ops

| Doc | Covers |
|---|---|
| [ops/local-stack.md](ops/local-stack.md) | Run locally: docker compose, `scripts/local` launchers, profiles, Alembic. |
| [ops/deployment.md](ops/deployment.md) | Prod on AWS/EC2: push → GitHub Actions → OIDC → SSM → deploy, the deploy gate. |
| [ops/runbooks.md](ops/runbooks.md) | Incident procedures: orientation, reboot, rollback (schema-aware), restore, IB session recovery; each with its last walk. |
| [ops/secrets.md](ops/secrets.md) | The SSM inventory (every `/fxvol/prod/*` name, what reads it, what happens when absent), load-to-RAM, never-echo. |
| [ops/data-terms.md](ops/data-terms.md) | Each data source: how it is fetched, what is stored, the attribution shown, and the decisions on IB redistribution and FRED storage. |
| [ops/dependency-policy.md](ops/dependency-policy.md) | Lockfile, image pins, weekly scan, license gate, maintenance-risk register, triage log. |
| [ops/redis-durability.md](ops/redis-durability.md) | What Redis holds, its TTLs, the eviction policy, and what a restart costs. |
| [ops/alerting.md](ops/alerting.md) | Prometheus rules → Alertmanager → SNS, the host disk guard, the IB watchdog, one-time IAM / IMDS setup. |
| [ops/security.md](ops/security.md) | The auth boundary: public reads, the trader cookie, the api-only internal token, the public-route decisions. |
