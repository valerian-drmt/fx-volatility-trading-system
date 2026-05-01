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

15-panel React cockpit on top of 5 async Python services :
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
  via `/settings` React page, hot-reloaded into services via Redis pub/sub
- Secrets in AWS SSM Parameter Store (KMS-encrypted), never on disk, loaded
  per-session by `scripts/ops/load_secrets.ps1` (Windows) or IAM role (EC2)
- Structured JSON logs (structlog), Prometheus metrics at `/metrics`,
  extended health probe exercising DB + Redis + engine heartbeats

---

## Architecture

**10 containers, 6 that ship our Python code.**

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

| Container | Runs | Source | Image / Dockerfile |
|---|---|---|---|
| `postgres` | DB 16 | — | `postgres:16-alpine` |
| `redis` | Bus + cache | — | `redis:7-alpine` |
| `nginx` | Reverse proxy | `infrastructure/nginx/` | `nginx:alpine` |
| `ib-gateway` | IB API | — | `gnzsnz/ib-gateway:latest` |
| `frontend` | React SPA | `frontend/` | `Dockerfile.web` |
| **`api`** | FastAPI REST + WS | `src/api/` + `src/core/` + `src/persistence/` + `src/bus/` | `Dockerfile.api` |
| **`market-data`** | IB ticks → Redis (clientID 1) | `src/engines/market_data/` | `Dockerfile.engines` |
| **`vol-engine`** | SVI/SSVI/GARCH/HAR/PCA/GMM (clientID 2) | `src/engines/vol/` | `Dockerfile.engines` |
| **`risk-engine`** | Greeks + delta hedge (clientID 3) | `src/engines/risk/` | `Dockerfile.engines` |
| **`db-writer`** | Redis → Postgres async sink | `src/engines/db_writer/` | `Dockerfile.engines` |
| **`execution-engine`** | Order submission HTTP (clientID 5, :8001) | `src/engines/execution/` | `Dockerfile.execution` |

Networks : `fxvol-public` (nginx), `fxvol-internal` (services), `fxvol-external` (IB outbound). The 5 Python engines live behind the `engines` compose profile (opt-in : `docker compose --profile engines up -d`).

Shared Python libs (not containers) : `src/core/` (pure-Python pricing + vol +
risk algos, no I/O), `src/persistence/` (SQLAlchemy 2 ORM in `models.py` —
20 classes — + 18 Alembic revisions), `src/bus/` (Redis pub/sub helpers +
channel/key constants), `src/shared/` (config, logging, secrets).

**Full details** : see [`docs/project-architecture.md`](docs/project-architecture.md).

---

## Tech stack

| Layer | Tech |
|---|---|
| Language | Python 3.11 + TypeScript 5 |
| API | FastAPI + uvicorn + pydantic v2 + pydantic-settings |
| Frontend | React 18 + Vite + zustand + plotly.js |
| Persistence | PostgreSQL 16 + SQLAlchemy 2 async + Alembic |
| Cache + bus | Redis 7 (pub/sub + cache) |
| IB connectivity | ib_insync (async) |
| Vol models | numpy, scipy (PCHIP, norm), arch (GARCH), custom SVI/SSVI |
| Secrets | AWS SSM Parameter Store + KMS CMK |
| CI | GitHub Actions (ruff, pytest, compileall, alembic round-trip, Playwright) |
| Deploy | Docker compose local, systemd + EC2 prod (planned R8) |

---

## Quickstart

**Prerequisites** : Docker Desktop (WSL2 backend) + Python 3.11 + Node 20.
AWS CLI v2 configured with profile `fxvol-dev` (see
[`infrastructure/aws/secrets-bootstrap.md`](infrastructure/aws/secrets-bootstrap.md)).

```powershell
# 1. venv + deps (one-off)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 2. Load secrets from SSM into the shell (every PS session)
.\scripts\load_secrets.ps1

# 3. Start the full stack
.\scripts\start_stack.ps1          # build + up + alembic upgrade head + 11 logs tabs
.\scripts\start_stack.ps1 -NoBuild # skip build, reuse cached images
```

Then :

- Cockpit : http://localhost/
- Admin config : http://localhost/settings
- API health : http://localhost/api/v1/health
- Extended health (DB + Redis + engines) : http://localhost/api/v1/health/extended

---

## Testing

```powershell
# Python unit tests
python -m pytest                                      # fast suite, no IB/DB/Redis
python -m ruff check src tests                        # lint

# Integration suites (gated by env)
$env:DB_RUN_INTEGRATION = "1"; python -m pytest -m db_integration
$env:REDIS_RUN_INTEGRATION = "1"; python -m pytest -m redis_integration
$env:IB_RUN_INTEGRATION = "1"; python -m pytest -m integration

# Frontend
cd frontend
npm test                                              # vitest
npm run test:e2e                                      # playwright

# Re-runnable smoke notebooks (manual validation per container — see scripts/<service>/)
jupyter lab scripts/smoke/postgresql/02_setup.ipynb         # apply migrations + seed vol_config v1
jupyter lab scripts/smoke/postgresql/03_test_crud.ipynb     # CRUD per table
jupyter lab scripts/smoke/api/01_test_endpoints.ipynb       # 30 REST/WS endpoints (incl. admin config)
jupyter lab scripts/smoke/nginx/01_test_routes.ipynb        # reverse proxy routes + WS upgrade
jupyter lab scripts/smoke/redis/01_test_pubsub.ipynb        # cache + pub/sub
jupyter lab scripts/smoke/db-writer/01_test_writer.ipynb    # AsyncDatabaseWriter end-to-end
jupyter lab scripts/smoke/redis/02_test_bus_package.ipynb   # bus Python wrapper (throttle, TTL, fail-fast)
```

---

## Project structure

```
fx-volatility-trading-system/
├── CLAUDE.md, LICENSE, README.md
├── docker-compose.yml, docker-compose.override.yml
├── pytest.ini, ruff.toml
├── requirements.txt, requirements/  (monolith dev + per-container slim)
├── .github/workflows/               (ci.yml + deploy.yml)
├── src/                             (PyPA src-layout, all Python)
│   ├── api/                         → container fxvol-api
│   │   ├── main.py                  FastAPI app + lifespan (events scheduler, WS bridge)
│   │   ├── routers/                 12 routers : health, admin, analytics, cockpit,
│   │   │                              dev, orders, portfolio, pricing, regime,
│   │   │                              signals, vol, ws
│   │   ├── ws/                      connection_manager + redis_bridge
│   │   ├── middleware/              logging (structlog) + rate_limit + timing
│   │   ├── models/                  Pydantic v2 schemas
│   │   └── services/                thin orchestration + events/ pipeline
│   ├── services/
│   │   ├── market_data/             → fxvol-market-data (clientID 1)
│   │   ├── vol/                     → fxvol-vol-engine    (clientID 2)
│   │   ├── risk/                    → fxvol-risk-engine   (clientID 3)
│   │   ├── db_writer/               → fxvol-db-writer
│   │   └── execution/               → fxvol-execution-engine (clientID 5, :8001)
│   ├── core/                        pure-Python algos
│   │   ├── vol/                     garch, har_rv, svi, ssvi, pchip_smile,
│   │   │                              fair_smile, gmm_regime, regime_engine,
│   │   │                              pca_engine, surface_pca, calibration,
│   │   │                              vrp, yang_zhang
│   │   ├── pricing/bs.py            Black-Scholes for FX options
│   │   └── risk/greeks.py           Δ/Γ/V analytics
│   ├── persistence/
│   │   ├── models.py                20 ORM classes (single file)
│   │   ├── alembic.ini
│   │   └── migrations/versions/     18 revisions
│   ├── bus/                         publisher, channels, keys, redis_client
│   └── shared/                      config, logging, secrets, ib_connection
├── frontend/                        (React + TS + Vite)
├── infrastructure/
│   ├── docker/                      (Dockerfile.{api,engines,web,ib-stub})
│   ├── aws/                         (secrets bootstrap doc)
│   ├── ec2/                         (systemd unit + provisioning)
│   └── nginx/                       (dev + frontend + prod confs)
├── scripts/                         (start_stack, load_secrets, db_*, smoke notebooks)
├── tests/                           (unit + services/ + integration/ + sandbox_r9/)
└── docs/
    ├── project-architecture.md     (canonical architecture reference)
    ├── DEPLOYMENT.md                (EC2 prod runbook)
    ├── PERFORMANCE.md               (R7 RAM profiling)
    ├── BRANCH_PROTECTION.md         (GitHub ruleset)
    └── VOL_{MODEL_REFACTOR_PLAN,TRADING_USER_GUIDE}.md
```

---

## Documentation

| Document | Content |
|---|---|
| [docs/project-architecture.md](docs/project-architecture.md) | Canonical architecture : src-layout, per-container folders, shared libs, what/why |
| [docs/VOL_MODEL_REFACTOR_PLAN.md](docs/VOL_MODEL_REFACTOR_PLAN.md) | 6-phase research-to-trade plan (VRP + HAR-RV + SVI/SSVI + PCA + execution + frontend) |
| [docs/VOL_TRADING_USER_GUIDE.md](docs/VOL_TRADING_USER_GUIDE.md) | Operator guide for the 6-panel cockpit |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Prod deploy runbook (EC2 + systemd) |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | RAM profiling of the 4-container split |
| [docs/BRANCH_PROTECTION.md](docs/BRANCH_PROTECTION.md) | GitHub branch rules |
| [infrastructure/aws/secrets-bootstrap.md](infrastructure/aws/secrets-bootstrap.md) | AWS SSM + KMS + IAM one-time setup |

---

## Contributing

**Local CI reproduction** — the commands below mirror `.github/workflows/ci.yml` :

```powershell
python -m compileall -q src
python -m ruff check src tests
$env:PYTHONPATH = "src"; python -m pytest -m "not integration"
cd frontend; npm test; npm run build
```

**Branching** : trunk-based, `main` always deployable. One short-lived feature
branch per PR, naming `<type>/<release>-<slug>` (e.g. `feat/r4-vol-router`).
Conventional Commits for messages. Squash-merge only. Branch protection rules
in [`docs/BRANCH_PROTECTION.md`](docs/BRANCH_PROTECTION.md).

---

## License

MIT
