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

7-view React trading desk (voldesk) on top of 5 async Python engines :
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
  per-session by `scripts/local/load_secrets.ps1` (Windows) or IAM role (EC2)
- Structured JSON logs (structlog), Prometheus metrics at `/metrics`,
  extended health probe exercising DB + Redis + engine heartbeats

---

## Architecture

**11-container core stack** (6 ship our Python code) **+ optional 7-container observability stack** (Prometheus / cAdvisor / Loki / Tempo / Grafana / promtail / otel-collector, opt-in via `--profile obs`).

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

**Deployment sizing** : every service carries a compose `mem_limit` so no container can OOM-starve postgres. Core profile (nginx + frontend + api + postgres + redis) ≈ 1.1 GB → fits a `t3.small`. `engines` adds ~1.5 GB → `t3.medium` minimum; the `ib` profile adds a ~1–1.5 GB JVM on top (`t3.medium` for core+ib, avoid running everything on one small box).

Shared Python libs (under `src/`, no container of their own) :
- **`core/`** — pure pricing + vol + risk algorithms (no I/O)
- **`persistence/`** — SQLAlchemy 2 ORM (`models.py`, 28 classes) + 54 Alembic revisions + `AsyncDatabaseWriter`
- **`bus/`** — Redis pub/sub helpers + channel/key constants + connection factories
- **`shared/`** — config (`Settings`), structlog setup, IB connection wrapper, db-events publisher

Dependency direction is enforced by [`import-linter`](https://import-linter.readthedocs.io/) in CI ; see [`.importlinter`](.importlinter) for the 5 contracts.

**Full architecture** : the in-app **Stack** dev tab (`/dev` → 🐳 Stack · Health · Redis) renders the 17 containers, their wiring, and live health probes — single canonical view, no static diagram to drift against.

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
| Deploy | Docker compose local (11-container core, `obs` profile optional) |

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
.\scripts\ops\stack.ps1          # build + up + alembic upgrade head (+ obs profile)
.\scripts\ops\stack.ps1 -NoBuild # skip build, reuse cached images
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

### PyCharm run configurations

The repo ships ready-to-use **Run/Debug configurations** under
[`.idea/runConfigurations/`](.idea/runConfigurations) (version-controlled, so
they load automatically when the project is opened in PyCharm — no setup
command needed). They appear in the Run dropdown grouped in two folders :

| Folder  | Wraps                      | Configurations |
|---------|----------------------------|----------------|
| `Local` | `scripts/local/stack.ps1` | Up · Up (no build) · Up (fast) · Down · Down (wipe volumes) · Refresh · Rebuild: frontend · Rebuild: api |
| `EC2`   | `scripts/aws/ec2.ps1`         | Health · Containers · Deploy · Connect (SSM) · Logs: api · Restart: nginx · Instance: status/stop/start |

If they don't show up after pulling, run **File → Reload All from Disk**. They
target Windows PowerShell (`powershell.exe`) and run in the integrated terminal.

> Secrets still come from `load_secrets.ps1`, which must be **dot-sourced in
> your shell** (`. .\scripts\ops\load_secrets.ps1`) — it is intentionally not a
> run configuration, since a child process can't export env vars back to PyCharm.

---

## Testing

```powershell
# Python — pyproject.toml drives ruff config + pytest config + mypy config
python -m ruff check src tests                       # lint
python -m pytest                                     # unit suite (~410 tests, < 10s)
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
```

The nginx config parse-test lives at
`tests/unit/infrastructure/test_nginx_config_syntax.py` — collected with the
unit suite and also run explicitly by the `nginx-config` CI job. The old
`tests/old/` quarantine has been emptied and removed (cf.
[`tests/STRUCTURE.md`](tests/STRUCTURE.md)).

---

## Project structure

```
fx-volatility-trading-system/
├── pyproject.toml                  single source of truth (deps + ruff + pytest + mypy)
├── .importlinter                   architecture contracts (5 layered rules)
├── docker-compose.yml, docker-compose.override.yml
├── README.md, CLAUDE.md, LICENSE
├── .github/workflows/              ci.yml + build.yml + codeql.yml + security-scan.yml
├── src/                            (PyPA src-layout, all Python)
│   ├── api/                        → container fxvol-api
│   │   ├── main.py                 FastAPI app + lifespan (events scheduler, WS bridge)
│   │   ├── routers/                16 routers : admin, analytics, auth, cockpit,
│   │   │                             dev, health, orders, portfolio_panel,
│   │   │                             positions, pricing, regime, signals, trade,
│   │   │                             trades, vol, ws
│   │   ├── ws/                     connection_manager + redis_bridge
│   │   ├── middleware/             logging (structlog) + rate_limit + timing
│   │   ├── schemas/                Pydantic v2 request/response classes
│   │   └── orchestration/          use-case orchestration
│   │       └── events/             FRED + ECB + BoE + FOMC + Eurostat + ONS pipeline
│   ├── engines/                    5 long-running services
│   │   ├── market_data/            → fxvol-market-data (clientID 1)
│   │   ├── vol/                    → fxvol-vol-engine    (clientID 2)
│   │   ├── risk/                   → fxvol-risk-engine   (clientID 3)
│   │   ├── db_writer/              → fxvol-db-writer
│   │   └── execution/              → fxvol-execution-engine (clientID 5, :8001)
│   ├── core/                       pure-Python algos (no I/O)
│   │   ├── vol/                    garch, har_rv, svi, ssvi, pchip_smile,
│   │   │                             gmm_regime, regime_engine, pca_engine,
│   │   │                             fair_term, surface_z, tenors,
│   │   │                             feature_enrichment, vrp, yang_zhang
│   │   ├── pricing/bs.py           Black-Scholes for FX options
│   │   ├── risk/greeks.py          Δ/Γ/V analytics
│   │   ├── config/                 config helpers
│   │   └── products.py             Murex-style product label dual-column
│   ├── persistence/                ONLY the DB adapter
│   │   ├── models.py               28 ORM classes (single file)
│   │   ├── db.py                   engine + AsyncSession factory
│   │   ├── writer.py               AsyncDatabaseWriter (batch INSERT + retry)
│   │   ├── alembic.ini
│   │   └── migrations/versions/    54 revisions
│   ├── bus/                        ONLY the Redis adapter
│   │   ├── client.py               connection factory (async + sync)
│   │   ├── publisher.py
│   │   ├── channels.py
│   │   └── keys.py
│   └── shared/                     cross-cutting infra
│       ├── config.py               base Settings (extended by api/config.py)
│       ├── logging.py              structlog setup
│       ├── ib_connection.py        IB sync wrapper + backoff
│       ├── observability.py        Prometheus metrics + OTel tracing
│       └── db_events.py            db-events Redis publisher
├── frontend/                       React + TS + Vite (7 voldesk views +
│                                     9 dev tabs : Stack / WS / DB Explorer /
│                                     DB Schema / Logs / Migrations / PCA /
│                                     Trade / Portfolio)
├── infrastructure/
│   ├── docker/                     api.Dockerfile, web.Dockerfile, execution.Dockerfile, ib-stub
│   ├── nginx/                      nginx.conf + nginx-dev.conf + frontend.conf
│   ├── ib-gateway/                 local IB gateway image build instructions
│   ├── postgres/                   init.sql
│   ├── redis/                      redis.conf (hardened)
│   └── aws/                        SSM secrets bootstrap + KMS / IAM / S3 reference
├── scripts/                        human-run only (not shipped, not CI-collected)
│   ├── ops/                        stack.ps1 + ec2.ps1 + load_secrets.{ps1,sh}
│   ├── db/                         seed_* + backfill_iv_history_for_gmm.py
│   └── dev/                        dump_openapi.py + gmm_diagnostic.py + check_orders.py + compute_context_baseline.py
├── obs/                            Prometheus / Loki / Tempo / Promtail / OTel-collector
│                                     configs + Grafana dashboards + datasources
├── tests/                          mirrors src/ 1-to-1
│   ├── unit/                       (api, bus, core, engines, persistence, shared)
│   ├── integration/                pipeline_<sub-system>/ (gated by markers)
│   └── fixtures/                   shared pytest fixtures
└── docs/
    ├── README.md                   landing page index
    ├── run-local-stack.md          local stack runbook
    ├── docker-cheatsheet.md        day-to-day docker compose commands
    ├── db_schema_drift_workflow.md ORM ⇄ DB drift fix via alembic
    ├── branch-protection.md        GitHub branch rules
    ├── observability/              metric naming conventions + obs runbooks
    └── vol_trading_pca/            events pipeline architecture spec
```

---

## Documentation

| Document | Content |
|---|---|
| [docs/README.md](docs/README.md) | Index of all docs with one-sentence summaries |
| [docs/run-local-stack.md](docs/run-local-stack.md) | Local stack operator runbook |
| [docs/docker-cheatsheet.md](docs/docker-cheatsheet.md) | Day-to-day docker compose commands |
| [docs/db_schema_drift_workflow.md](docs/db_schema_drift_workflow.md) | How to feed DB-schema-drift fixes back through alembic |
| [docs/branch-protection.md](docs/branch-protection.md) | GitHub `main` branch ruleset + required status checks |
| [docs/observability/CONVENTIONS.md](docs/observability/CONVENTIONS.md) | Metric naming + label cardinality rules |
| [docs/observability/RUNBOOKS.md](docs/observability/RUNBOOKS.md) | Loki / Prometheus / Tempo / Grafana operator playbooks |
| [docs/vol_trading_pca/events_pipeline_spec.md](docs/vol_trading_pca/events_pipeline_spec.md) | Multi-source economic-events pipeline architecture |
| [tests/STRUCTURE.md](tests/STRUCTURE.md) | Test layout + pytest configuration reference |
| [infrastructure/aws/secrets-bootstrap.md](infrastructure/aws/secrets-bootstrap.md) | AWS SSM + KMS + IAM one-time setup |
| Live ER diagram, drift detection, log tail, alembic chain | in-app **dev console** (`/dev`) — DB Schema / Logs / Migrations tabs |

---

## Contributing

**Local CI reproduction** — the commands below mirror `.github/workflows/ci.yml` :

```powershell
python -m compileall -q src
python -m ruff check src tests
PYTHONPATH=src lint-imports                                # architecture contracts
$env:PYTHONPATH = "src"; python -m pytest                  # ~410 unit tests
cd frontend; npm test; npm run build
```

**Branching** : trunk-based, `main` always deployable. One short-lived feature
branch per PR, naming `<type>/<release>-<slug>` (e.g. `feat/r10-dev-tabs`).
Conventional Commits for messages. Squash-merge only. Branch protection rules
in [`docs/branch-protection.md`](docs/branch-protection.md).

**Architecture lint** — [`.importlinter`](.importlinter) enforces 5 layered
contracts in CI ; any PR introducing a forbidden import (e.g. `core/`
depending on `persistence/`) fails the build.

---

## License

MIT
