# Project architecture — fx-volatility-trading-system

Monorepo Python + React + Docker, 10 containers, single Postgres /
Redis / Nginx plane, 5 Python containers to ship. Microservices-
oriented but still small enough for one repo / one CI.

---

## 1. Top-level layout

```
fx-volatility-trading-system/
├── .claude/              # Claude Code hooks + settings
├── .github/              # workflows (CI / deploy-prod)
├── .venv/                # local Python virtualenv (gitignored)
├── CLAUDE.md             # AI agent instructions
├── LICENSE
├── README.md
├── .dockerignore
├── .gitignore
├── docker-compose.yml          # single prod-like compose
├── docker-compose.override.yml # dev-only : host port exposure
├── pytest.ini                  # test config
├── ruff.toml                   # lint config
├── requirements.txt            # dev + CI monolithic install
├── requirements/               # per-container slim installs (Docker)
├── src/                        # Python source code (see § 2)
├── tests/                      # pytest suite
├── frontend/                   # React + TS + Vite (one container)
├── infrastructure/             # Dockerfiles, nginx, aws, ec2 configs
├── scripts/                    # operator scripts (start_stack, load_secrets, notebooks)
├── persistence/                # alembic config + migrations (paired with src/persistence)
├── config/                     # runtime JSON configs (vol_config.json seul)
├── docs/                       # architecture + operator docs (this file lives here)
└── releases/                   # gitignored : roadmap, status, interview prep
```

**Two non-obvious choices** :

- **`persistence/` top-level + `src/persistence/`** — Alembic config lives outside the package (`persistence/alembic.ini`, `persistence/migrations/`) because `alembic` CLI expects the config at a filesystem path, not as an importable module. The Python code (models, ORM writer) lives under `src/persistence/`.
- **`requirements.txt` root + `requirements/` folder** — the monolith `requirements.txt` at root is for `pip install -r requirements.txt` dev/CI (convention every Python dev expects). The `requirements/` folder splits deps per container image (`base.txt`, `ib.txt`, `quant.txt`, `writer.txt`) so each Docker image stays minimal and has its own attack surface.

---

## 2. `src/` — Python source layout

**Adopt the src-layout** (PyPA recommendation) to prevent accidental imports of the local working copy when the package is also installed via pip. `PYTHONPATH=src` in the dev env, all Docker containers `COPY src/ /app/src/` with `WORKDIR /app` and `PYTHONPATH=/app/src`.

```
src/
├── api/                    # container: fxvol-api (FastAPI backend)
│   ├── main.py             # ASGI entry
│   ├── routers/            # HTTP endpoints grouped by domain
│   ├── services/           # business logic called by routers
│   ├── models/             # Pydantic request/response DTOs
│   ├── middleware/         # CORS, request ID, logging
│   ├── ws/                 # WebSocket handlers + Redis bridge
│   ├── dependencies.py     # FastAPI Depends() shared providers
│   └── config.py           # env var binding (pydantic-settings)
│
├── services/               # containers (background workers, one per subdir)
│   ├── market_data/        # container: fxvol-market-data   (IB ticks → Redis)
│   ├── vol/                # container: fxvol-vol-engine    (GARCH, SVI, σ_fair)
│   ├── risk/               # container: fxvol-risk-engine   (Greeks, PnL)
│   ├── db_writer/          # container: fxvol-db-writer     (Redis → Postgres)
│   └── execution/          # library : order structures + delta hedger (imported by api)
│
├── core/                   # shared domain algorithms (pure, testable)
│   ├── pricing/            # Black-Scholes, Greeks, interpolate_iv
│   ├── vol/                # SVI, SSVI, butterfly arbitrage checks, PCA
│   └── risk/               # P&L decomposition, attribution
│
├── persistence/            # SQLAlchemy async ORM + writer
│   ├── models.py           # table declarations
│   ├── db.py               # engine factory
│   ├── writer.py           # AsyncDatabaseWriter (batch INSERT)
│   └── payloads.py         # pydantic ↔ ORM serializers
│
├── bus/                    # Redis pub/sub + cache helpers
│   ├── redis_client.py     # connection factory
│   ├── keys.py             # centralized key naming
│   ├── channels.py         # pub/sub channel constants
│   └── publisher.py        # throttled PUBLISH + SET cache
│
└── shared/                 # utilities (config, logging, secrets)
```

### 2.1 The rule : one folder per containerised Python service

Among the 10 containers, **5 run our Python code** (`api`, `market-data`, `vol-engine`, `risk-engine`, `db-writer`). Each gets a dedicated folder :

| Container | Source root | Dockerfile COPY | Test folder |
|---|---|---|---|
| `fxvol-api` | `src/api/` + `src/core/` + `src/persistence/` + `src/bus/` + `src/shared/` | `COPY src/ /app/src/` (incl. execution/) | `tests/` + `tests/services/` |
| `fxvol-market-data` | `src/services/market_data/` + `src/core/` + `src/bus/` + `src/shared/` | same (ib.txt) | `tests/services/market_data/` |
| `fxvol-vol-engine` | `src/services/vol/` + core + bus + shared | same (quant.txt, adds `arch`) | `tests/services/vol/` |
| `fxvol-risk-engine` | `src/services/risk/` + core + bus + shared | same (ib.txt) | `tests/services/risk/` |
| `fxvol-db-writer` | `src/services/db_writer/` + persistence + bus + shared (**no IB**) | writer.txt (SQLAlchemy/asyncpg, no ib_insync) | `tests/services/db_writer/` |

Each service declares its own `main.py` entry point (`python -m services.vol`) and its own subscribe loop + dependencies. Cross-service imports are forbidden : `services/vol/` must not import from `services/risk/`. Everything shared goes in `core/`, `persistence/`, `bus/`, `shared/`.

### 2.2 The other 5 containers — nothing in `src/`

`postgres`, `redis`, `nginx`, `ib-gateway`, `frontend` use off-the-shelf images or maintain code outside Python :
- `frontend/` is its own top-level folder (React/TS/Vite, one Dockerfile `infrastructure/docker/Dockerfile.web`).
- `nginx`, `redis` config files live under `infrastructure/nginx/` and `infrastructure/redis/`.
- `ib-gateway` customizations (if any) under `infrastructure/ib-gateway/`.

No Python code = no `src/<svc>/` folder for them.

### 2.3 Why `src/` and not `core/` as the top-level name

`src/` is the PyPA-recommended convention since 2019 for Python packages : explicit, unambiguous, avoids `ImportError: attempted relative import beyond top-level package` when running tests from the repo root. Using `core/` as top-level would be non-standard and would collide semantically with `src/core/` (which is where the domain algorithms actually live — it's a distinct layer).

Adopted by : `black`, `ruff`, `attrs`, `packaging`, `uv`, `pdm`, `poetry`, and most modern Python projects created after ~2020.

---

## 3. Shared libraries inside `src/`

`core/` / `persistence/` / `bus/` / `shared/` are libraries, not services. They **do not have a container** and **must not be importable via an HTTP/pub-sub boundary**. Their contract is a plain Python import.

| Layer | Role | Who imports it |
|---|---|---|
| `core/` | Pure, stateless domain algos. Deterministic, testable without network. | `api/`, `services/vol/`, `services/risk/` |
| `persistence/` | SQLAlchemy async engine + ORM + writer. | `api/`, `services/db_writer/` |
| `bus/` | Redis pub/sub + cache façade. | every service + `api/` |
| `shared/` | Config (pydantic-settings), structured logging (structlog), secrets loader. | every service + `api/` |
| `execution/` (in `services/`) | Vol trading structures + delta hedger. Imported by `api/` as a library. Will move to its own container later if/when the hedging loop becomes async-heavy. | `api/` |

Rule of thumb : if a component has state tied to a process (event loop, Redis connection pool), it lives in `services/<svc>/`. If it's a pure computation or a façade to an external resource, it lives in `core/` / `persistence/` / `bus/`.

---

## 4. Tests layout

```
tests/
├── test_<unit>.py            # fast unit tests, one file per src/<unit>
├── services/                 # per-service tests (mirror src/services/)
│   ├── market_data/
│   ├── vol/
│   ├── risk/
│   └── db_writer/
├── integration/              # docker compose up + HTTP smoke
└── sandbox_r9/               # spike tests from the R9 sandbox branch,
                              # to be triaged at cleanup phase
```

Unit tests import modules directly (`from core.pricing.bs import bs_price`). Integration tests spin up the compose stack and hit the API via HTTP. Gated by env vars (`DB_RUN_INTEGRATION=1`, `COMPOSE_RUN_INTEGRATION=1`) to keep CI fast.

---

## 5. What we deliberately do NOT do

- ❌ **Per-service repos (polyrepo)** — too much CI + deps duplication for a 5-service system maintained by 1 dev. Monorepo with shared `requirements/*.txt` is lean.
- ❌ **Single flat package (`fxvol/`)** — would mix the HTTP tier with the background workers in one namespace, making Docker `COPY` scopes impossible to tune.
- ❌ **`libs/` or `common/` sibling of `src/`** — pulled everything under `src/` instead. Simpler for `PYTHONPATH=src` and Docker `COPY src/`.
- ❌ **No `__init__.py` in every folder** — Python 3.3+ namespace packages (PEP 420) work fine without them for namespace-only dirs. We add `__init__.py` only where a package needs to export something or control import order.
- ❌ **Circular imports between `services/<a>/` and `services/<b>/`** — by contract. If service A needs data from service B, it goes through Redis pub/sub or the Postgres DB, not a Python import.

---

## 6. Industry references

| Project | Pattern | Why it validates this choice |
|---|---|---|
| `ruff`, `black`, `attrs` | `src/<single_pkg>/` | PyPA src-layout canonical adoption |
| `tiangolo/full-stack-fastapi-template` | flat, one service | Fits single-service API ; we have more so we went `src/` + `services/` |
| `apache/airflow` | `airflow/` top-level + `providers/<svc>/` subfolder | Microservice-per-folder inside a monorepo |
| `nautechsystems/nautilus_trader` | `nautilus_trader/` single package under `src/` | Quant trading src-layout reference |
| `Uber/ludwig`, `facebook/prophet` | Monorepo with `src/` + subpackages per feature | Polysubmodule monorepo without polyrepo |
| Netflix / Uber internal (public talks) | Monorepo, 1 folder per microservice under `services/` | Validates the `services/<svc>/` pattern at scale |

---

## 7. Migration status (R9 cleanup snapshot)

As of this file's creation, the v1 PyQt thread-based pipeline has
been **removed entirely** from the sandbox branch. The repo no longer
carries the v1/v2 cohabitation that plagued the migration through
R1–R8 (documented in the queued PRs R7 + R8 which are now partially
redundant with this R9 sandbox kill).

The src-layout restructure was also completed in this same R9 batch
(`api/`, `core/`, `services/`, `shared/`, `persistence/alembic.ini` +
`persistence/migrations/` all moved under `src/`). Top-level no longer
exposes any Python code tree -- everything that Python imports lives
under `src/` and is resolved via `PYTHONPATH=src`. The two persistence
folders collapsed into one under `src/persistence/`.

Remaining work before v2.0.0 :
- Extract the R9 sandbox commits into clean PRs onto `main` (cleanup
  phase). Given the magnitude of the kill-v1 + src-layout move, the
  50+ queued R4-R8 PRs are no longer rebasable one-by-one -- they'll
  be consolidated into a small number of large PRs (likely 2-3):
  one for kill-v1, one for src-layout, one for the remaining R7/R8
  items that still have independent value (CI workflows, systemd,
  deploy workflow). Narrative tradeoff accepted by the author.
