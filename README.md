# FX Volatility Trading System

**End-to-end trading platform for EUR/USD FX options : microservices pipeline,
research-grade vol signals, web cockpit, and Interactive Brokers execution.**

[![CI](https://github.com/valerian-drmt/fx-volatility-trading-system/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/valerian-drmt/fx-volatility-trading-system/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

15-panel React cockpit on top of 5 async Python engines :
live IB tick stream → vol surface fit (SVI/SSVI, GARCH, HAR-RV) → GMM regime
+ PCA signal z-scores → order submission with delta hedge → versioned audit
trail in Postgres.

---

## Features

### Market data + execution
- IB Gateway container (gnzsnz fork) serving delayed EUR/USD FOP chains
- Real-time tick stream published on Redis (throttled ~200ms)
- Order structures factory : straddle ATM, calendar spread, risk reversal 25d,
  butterfly 25d, all delta-hedged via 6E futures leg

### Volatility analytics
- **Regime detector** — GMM on `[vol_of_vol, vol_level, term_slope]` → 3 regimes
  (calm / stressed / pre_event) driving sizing multipliers
- **Surface fit** — SVI per tenor + SSVI global, butterfly + calendar no-arb checks,
  fair smile via EWMA on historical SVI params
- **Signal** — PCA(3) on the 30-D surface snapshot (6 tenors × 5 delta pillars),
  z-score each PC vs rolling 3M distribution, arm trade on `|z| > 1.5`
- **VRP** — realized forward vol vs IV ATM, conditional on regime ; fallback
  constant RP if history < 6 months

### Risk + P&L
- Greeks aggregation across open structures, per-tenor vega bar charts
- Delta hedge modes : static / threshold (default Δ=0.05) / scheduled
- Exit rules systematic : z-flip, time ratio, stop-loss vega, time to expiry

### Admin & observability
- Versioned vol config in Postgres (`vol_config` append-only table), edited
  via `/settings` React page, hot-reloaded into engines via Redis pub/sub
- Secrets in AWS SSM Parameter Store (KMS-encrypted), never on disk, loaded
  per-session by `scripts/ops/load_secrets.ps1` (Windows) or IAM role (EC2)
- Structured JSON logs (structlog), Prometheus metrics at `/metrics`,
  extended health probe exercising DB + Redis + engine heartbeats

---

## Architecture

**10 containers, 6 ship our Python code.**

```
                          ┌────────────────┐
                          │  React cockpit │   ←──── Users
                          │   (frontend)   │
                          └────────┬───────┘
                                   │ HTTP + WS
                          ┌────────▼───────┐
                          │     nginx      │  reverse proxy (80/443)
                          └────────┬───────┘
                                   │
                          ┌────────▼───────┐
                          │    FastAPI     │  REST + WS bridge (8000)
                          │     (api)      │
                          └─┬────────────┬─┘
                            │            │
            ┌───────────────┘            └──────────────────┐
            ▼                                               ▼
    ┌─────────────┐                                ┌────────────────┐
    │  Postgres   │◄───── db-writer ─────┐         │     Redis      │
    │   (16)      │  (Redis → DB sink)   │         │ pub/sub + cache│
    └─────────────┘                      │         └─┬────┬──┬───┬──┘
                                         │           │    │  │   │
                                         └───────────┤    │  │   │
                                                     │    │  │   │
       ┌────────────┐  ticks/bars     ┌──────────────▼┐   │  │   │
       │ ib-gateway │◄────────────────│ market-data    │───┘  │   │
       │  (IB API)  │  (clientID 1)   │   engine       │      │   │
       └─────┬──────┘                 └────────────────┘      │   │
             │                                                │   │
             │     option chains + IV history                 │   │
             │     (clientID 2)        ┌────────────────┐     │   │
             ├────────────────────────►│   vol-engine   │─────┘   │
             │                         │ SVI/SSVI/GARCH │         │
             │                         │ HAR/PCA/GMM    │         │
             │                         └────────────────┘         │
             │                                                    │
             │     positions + greeks  ┌────────────────┐         │
             │     (clientID 3)        │   risk-engine  │─────────┘
             ├────────────────────────►│ Δ/Γ/V aggreg.  │
             │                         └────────────────┘
             │
             │     order submission    ┌────────────────┐
             │     (clientID 5)        │ execution-eng. │  HTTP server
             └────────────────────────►│ orders+hedger  │  (port 8001)
                                       └────────────────┘
```

| Container | Runs | Source | Dockerfile |
|---|---|---|---|
| `postgres` | DB 16 | — | `postgres:16-alpine` |
| `redis` | Bus + cache | — | `redis:7-alpine` |
| `nginx` | Reverse proxy | `infrastructure/nginx/` | `nginx:alpine` |
| `ib-gateway` | IB API | — | `gnzsnz/ib-gateway:latest` |
| `frontend` | React SPA | `frontend/` | `infrastructure/docker/web.Dockerfile` |
| **`api`** | FastAPI REST + WS | `src/api/` + shared libs | `infrastructure/docker/api.Dockerfile` |
| **`market-data`** | IB ticks → Redis (clientID 1) | `src/engines/market_data/` | `src/engines/market_data/Dockerfile` |
| **`vol-engine`** | SVI/SSVI/GARCH/HAR/PCA/GMM (clientID 2) | `src/engines/vol/` | `src/engines/vol/Dockerfile` |
| **`risk-engine`** | Greeks + delta hedge (clientID 3) | `src/engines/risk/` | `src/engines/risk/Dockerfile` |
| **`db-writer`** | Redis → Postgres async sink | `src/engines/db_writer/` | `src/engines/db_writer/Dockerfile` |
| **`execution-engine`** | Order submission HTTP (clientID 5, :8001) | `src/engines/execution/` | `infrastructure/docker/execution.Dockerfile` |

Networks : `fxvol-public` (nginx), `fxvol-internal` (services), `fxvol-external` (IB outbound). The 5 Python engines live behind the `engines` compose profile (opt-in : `docker compose --profile engines up -d`).

Shared Python libs (under `src/`, no container of their own) :
- **`core/`** — pure pricing + vol + risk algorithms (no I/O)
- **`persistence/`** — SQLAlchemy 2 ORM (`models.py`, 20 classes) + 18 Alembic revisions + `AsyncDatabaseWriter`
- **`bus/`** — Redis pub/sub helpers + channel/key constants + connection factories
- **`shared/`** — config (`Settings`), structlog setup, IB connection wrapper, db-events publisher

Dependency direction is enforced by [`import-linter`](https://import-linter.readthedocs.io/) in CI ; see [`.importlinter`](.importlinter) for the 5 contracts.

**Full architecture** : see [`docs/vol_trading_pca/project_architecture.md`](docs/vol_trading_pca/project_architecture.md) (canonical reference) and [`docs/structure-refactor-plan.md`](docs/structure-refactor-plan.md) (the 20-step plan that produced the current src-layout).

---

## Tech stack

| Layer | Tech |
|---|---|
| Language | Python 3.11 + TypeScript 5 |
| Build / packaging | `pyproject.toml` (PEP 621) — single source of truth ; `uv` recommended, plain `pip` works |
| API | FastAPI + uvicorn + pydantic v2 + pydantic-settings + slowapi |
| Frontend | React 18 + Vite + TypeScript strict + zustand + plotly.js |
| Persistence | PostgreSQL 16 + SQLAlchemy 2 async + Alembic |
| Cache + bus | Redis 7 (pub/sub + cache) |
| IB connectivity | ib_insync (async) |
| Vol models | numpy, scipy (PCHIP, norm), arch (GARCH), scikit-learn (GMM), custom SVI/SSVI |
| Secrets | AWS SSM Parameter Store + KMS CMK |
| CI | GitHub Actions — ruff, pytest, compileall, import-linter, openapi drift, vitest, Playwright, alembic round-trip |
| Deploy | Docker compose local, systemd + EC2 prod (R8) |

---

## Quickstart

**Prerequisites** : Docker Desktop (WSL2 backend) + Python 3.11 + Node 20.
AWS CLI v2 configured with profile `fxvol-dev` (see
[`infrastructure/aws/secrets-bootstrap.md`](infrastructure/aws/secrets-bootstrap.md)).

```powershell
# 1. venv + deps (one-off)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,api,quant,ib,writer]"

# 2. Load secrets from SSM into the shell (every PS session)
.\scripts\ops\load_secrets.ps1

# 3. Start the full stack
.\scripts\ops\start_stack.ps1          # build + up + alembic upgrade head + 11 logs tabs
.\scripts\ops\start_stack.ps1 -NoBuild # skip build, reuse cached images
```

Then :

- Cockpit : http://localhost/
- Admin config : http://localhost/settings
- API health : http://localhost/api/v1/health
- Extended health (DB + Redis + engines) : http://localhost/api/v1/health/extended

**Faster setup with [`uv`](https://docs.astral.sh/uv/)** (recommended) :

```powershell
uv sync --extra dev --extra api --extra quant --extra ib --extra writer
uv run pytest
```

---

## Testing

```powershell
# Python — pyproject.toml drives ruff config + pytest config + mypy config
python -m ruff check src tests                       # lint
python -m pytest                                     # unit only (76 tests, < 5s)
PYTHONPATH=src lint-imports                          # architecture contracts

# Integration suites (gated by env)
$env:DB_RUN_INTEGRATION = "1"; python -m pytest -m db_integration
$env:REDIS_RUN_INTEGRATION = "1"; python -m pytest -m redis_integration
$env:IB_RUN_INTEGRATION = "1"; python -m pytest -m integration

# Frontend
cd frontend
npm run lint && npm run typecheck                    # ESLint + tsc
npm test                                             # vitest (with 70% coverage threshold)
npm run test:e2e                                     # Playwright

# Re-runnable smoke notebooks (manual validation per container)
jupyter lab scripts/smoke/postgresql/02_setup.ipynb         # apply migrations + seed vol_config v1
jupyter lab scripts/smoke/postgresql/03_test_crud.ipynb     # CRUD per table
jupyter lab scripts/smoke/api/01_test_endpoints.ipynb       # 30 REST/WS endpoints
jupyter lab scripts/smoke/nginx/01_test_routes.ipynb        # reverse proxy + WS upgrade
jupyter lab scripts/smoke/redis/01_test_pubsub.ipynb        # cache + pub/sub
jupyter lab scripts/smoke/redis/02_test_bus_package.ipynb   # bus Python wrapper
jupyter lab scripts/smoke/db-writer/01_test_writer.ipynb    # AsyncDatabaseWriter end-to-end
```

Tests under `tests/old/` are a deliberate quarantine of historical tests
pending triage (see [`tests/STRUCTURE.md`](tests/STRUCTURE.md)) ; they are
excluded from the default pytest collection.

---

## Project structure

```
fx-volatility-trading-system/
├── pyproject.toml                  single source of truth (deps + ruff + pytest + mypy)
├── .importlinter                   architecture contracts (5 layered rules)
├── docker-compose.yml, docker-compose.override.yml
├── README.md, CLAUDE.md, LICENSE
├── .github/workflows/              ci.yml + build.yml + deploy.yml
├── src/                            (PyPA src-layout, all Python)
│   ├── api/                        → container fxvol-api
│   │   ├── main.py                 FastAPI app + lifespan (events scheduler, WS bridge)
│   │   ├── routers/                12 routers : health, admin, analytics, cockpit,
│   │   │                             dev, orders, portfolio, pricing, regime,
│   │   │                             signals, vol, ws
│   │   ├── ws/                     connection_manager + redis_bridge
│   │   ├── middleware/             logging (structlog) + rate_limit + timing
│   │   ├── schemas/                Pydantic v2 request/response classes
│   │   └── orchestration/          use-case orchestration (was: api/services/)
│   │       └── events/             FRED + ECB + BoE + FOMC + Eurostat + ONS pipeline
│   ├── engines/                    5 long-running services (was: src/services/)
│   │   ├── market_data/            → fxvol-market-data (clientID 1)
│   │   ├── vol/                    → fxvol-vol-engine    (clientID 2)
│   │   ├── risk/                   → fxvol-risk-engine   (clientID 3)
│   │   ├── db_writer/              → fxvol-db-writer
│   │   └── execution/              → fxvol-execution-engine (clientID 5, :8001)
│   ├── core/                       pure-Python algos (no I/O)
│   │   ├── vol/                    garch, har_rv, svi, ssvi, pchip_smile,
│   │   │                             fair_smile, gmm_regime, regime_engine,
│   │   │                             pca_engine, surface_pca, calibration,
│   │   │                             vrp, yang_zhang
│   │   ├── pricing/bs.py           Black-Scholes for FX options
│   │   ├── risk/greeks.py          Δ/Γ/V analytics
│   │   ├── config/                 config helpers
│   │   └── payloads.py             engine output → DB row dict (pure)
│   ├── persistence/                ONLY the DB adapter
│   │   ├── models.py               20 ORM classes (single file)
│   │   ├── db.py                   engine + AsyncSession factory
│   │   ├── writer.py               AsyncDatabaseWriter (batch INSERT + retry)
│   │   ├── alembic.ini
│   │   └── migrations/versions/    18 revisions
│   ├── bus/                        ONLY the Redis adapter
│   │   ├── client.py               connection factory (async + sync)
│   │   ├── publisher.py
│   │   ├── channels.py
│   │   └── keys.py
│   └── shared/                     cross-cutting infra
│       ├── config.py               base Settings (extended by api/config.py)
│       ├── logging.py              structlog setup
│       ├── ib_connection.py        IB sync wrapper + backoff
│       └── db_events.py            db-events Redis publisher
├── frontend/                       React + TS + Vite (15 panels, 18 dev pages)
├── infrastructure/
│   ├── docker/                     api.Dockerfile, web.Dockerfile, execution.Dockerfile, …
│   ├── nginx/                      nginx.conf + nginx-dev.conf + frontend.conf
│   ├── aws/                        secrets bootstrap doc + R8 deploy plan
│   └── ec2/                        systemd unit + provisioning
├── scripts/
│   ├── ops/                        load_secrets.{ps1,sh} + start_stack.ps1
│   ├── dev/                        dump_openapi.py + gmm_diagnostic.py
│   ├── migrations/                 backfill_iv_history_for_gmm.py + seed_events_manual.py
│   └── smoke/<service>/            re-runnable Jupyter smoke notebooks per container
├── tests/                          mirrors src/ 1-to-1
│   ├── unit/                       (api, bus, core, engines, persistence, shared)
│   ├── integration/                pipeline_<sub-system>/ (gated by markers)
│   ├── fixtures/                   shared pytest fixtures
│   └── old/                        quarantine, NOT collected by default
└── docs/
    ├── README.md                   landing page index
    ├── run-local-stack.md          local stack runbook
    ├── deployment.md               EC2 prod deploy
    ├── branch-protection.md        GitHub branch rules
    ├── preventing-spaghetti-code.md  theory + agent guard rails
    ├── structure-refactor-plan.md  the 20-step refactor that built this layout
    └── vol_trading_pca/            architecture + research-to-trade specs
```

---

## Documentation

| Document | Content |
|---|---|
| [docs/README.md](docs/README.md) | Index of all docs with one-sentence summaries |
| [docs/vol_trading_pca/project_architecture.md](docs/vol_trading_pca/project_architecture.md) | Canonical architecture : containers, data flows, ports |
| [docs/vol_trading_pca/index.md](docs/vol_trading_pca/index.md) | Vol-PCA research-to-trade roadmap (Step 1 regime → Step 2 PCA → Step 3 trade preview → Step 4 execution) |
| [docs/run-local-stack.md](docs/run-local-stack.md) | Local stack operator runbook |
| [docs/deployment.md](docs/deployment.md) | EC2 prod deploy (systemd + IAM + Let's Encrypt) |
| [docs/branch-protection.md](docs/branch-protection.md) | GitHub `main` branch ruleset |
| [docs/structure-refactor-plan.md](docs/structure-refactor-plan.md) | 20-step refactor plan : the *why* behind the layout |
| [docs/preventing-spaghetti-code.md](docs/preventing-spaghetti-code.md) | Theory : prevention principles + AI-agent guard rails |
| [infrastructure/aws/secrets-bootstrap.md](infrastructure/aws/secrets-bootstrap.md) | AWS SSM + KMS + IAM one-time setup |

---

## Contributing

**Local CI reproduction** — the commands below mirror `.github/workflows/ci.yml` :

```powershell
python -m compileall -q src
python -m ruff check src tests
PYTHONPATH=src lint-imports                                # architecture contracts
$env:PYTHONPATH = "src"; python -m pytest                  # 76 unit tests
cd frontend; npm test; npm run build
```

**Branching** : trunk-based, `main` always deployable. One short-lived feature
branch per PR, naming `<type>/<release>-<slug>` (e.g. `feat/r4-vol-router`).
Conventional Commits for messages. Squash-merge only. Branch protection rules
in [`docs/branch-protection.md`](docs/branch-protection.md).

**Architecture lint** — [`.importlinter`](.importlinter) enforces 5 layered
contracts in CI ; any PR introducing a forbidden import (e.g. `core/`
depending on `persistence/`) fails the build.

---

## License

MIT
