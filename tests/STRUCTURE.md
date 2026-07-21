# `tests/` — structure and conventions

> Reference doc for knowing **where to put a test** and **how to run it**.

---

## 2-level pyramid + externalized smoke

```
tests/
├── fixtures/               # reusable builders (factories for positions, vol surfaces, etc.)
│
├── unit/                   # in-process, no I/O, < 100ms per test
│   └── <mirror src/>
│
└── integration/            # real I/O (DB/Redis/HTTP), seconds per test
    └── pipeline_<sub-system>/
```

The **3rd level of the pyramid (smoke / e2e)** lives outside `tests/`: manual
validation of the full stack + Playwright on the frontend side (`frontend/e2e/`).

This split reflects the nature of the tests:

| Level | Question answered | Tool | Speed | Independence |
|---|---|---|---|---|
| unit | "does this module do its job?" | pytest | < 100ms | mocks, no I/O |
| integration | "do these N modules together produce the right output?" | pytest + containers | seconds | real DB/Redis/IB (or stubs for IB) |
| smoke | "does the user see what they should see?" | manual + Playwright | min | full stack |

---

## `tests/unit/` — mirror of `src/`

**Rule**: `tests/unit/<X>/test_<Y>.py` tests `src/<X>/<Y>.py`. Path = direct transformation.

```
src/                            tests/unit/
├── api/                  →     ├── api/
│   ├── orchestration/    →     │   ├── orchestration/
│   ├── schemas/          →     │   └── (schemas/ — Pydantic, no logic to test)
│   ├── routers/          →     │   └── (routers/ — covered by integration)
├── bus/                  →     ├── bus/
├── core/                 →     ├── core/
├── persistence/          →     ├── persistence/
├── engines/              →     ├── engines/
│   ├── db_writer/        →     │   ├── db_writer/
│   ├── execution/        →     │   ├── execution/
│   ├── market_data/      →     │   ├── market_data/
│   ├── risk/             →     │   ├── risk/
│   └── vol/              →     │   └── vol/
└── shared/               →     └── shared/
```

**No `tests/unit/postgres/`, `tests/unit/redis/`, `tests/unit/ib-gateway/` folders** because those containers use off-the-shelf images (postgres:16, redis:7, gnzsnz/ib-gateway) with no custom Python code to unit-test. Their validation goes through the manual smoke of the full stack.

**No `tests/unit/nginx/` folder** either — the nginx config is tested via `tests/integration/docker_compose/` (syntax + reload).

**No `tests/unit/frontend/` folder** — the frontend has its own framework (Vitest + Playwright) in `frontend/tests/` or `frontend/__tests__/` depending on the convention adopted later.

### Criteria for a test to qualify as "unit"

All mandatory:

- ✅ No network I/O (no `redis.from_url`, no `httpx.get`, no socket)
- ✅ No disk I/O outside tmp (no real SQLite writes; pytest's `tmp_path` fixture OK)
- ✅ No Docker container
- ✅ Mocks/stubs for external dependencies (`AsyncMock` for ib_insync, `fakeredis` or MagicMock for Redis)
- ✅ < 100ms per typical test (the total for `tests/unit/` must stay < 30s)

**Simple reviewer rule**: if you see `import redis` or `import psycopg` or `from ib_insync import IB` in the test, it's integration, not unit. Exception: importing types for annotations does not count.

---

## `tests/integration/` — by pipeline (sub-system)

**Rule**: group by **partial end-to-end data path**, not by individual container nor by isolated edge. Each pipeline represents a sub-system that must work together.

| Folder | Pipeline under test | Containers involved |
|---|---|---|
| `pipeline_redis_bus/` | Redis producers + consumers (`bus.publisher`, channels, cache TTL) | redis + 1-2 in-process Python producers/consumers |
| `pipeline_db_writer/` | Redis events → db-writer → Postgres (idempotency, retry, shutdown) | postgres + redis + db-writer |
| `pipeline_vol/` | ib-stub → market-data → redis → vol-engine → postgres (SVI fit, signal generation) | ib-stub + market-data + redis + vol-engine + postgres |
| `pipeline_risk/` | spot+surface in redis → risk-engine → greeks+pnl_curve out (full cycle) | redis + risk-engine + (postgres for future positions stub) |
| `pipeline_api_serving/` | REST endpoints reading DB+Redis: `/health`, `/api/v1/portfolio/header`, etc. | postgres + redis + api + (optional nginx) |
| `pipeline_ws_bridge/` | engine PUBLISH → api SUBSCRIBE → WS broadcast → client receive | one engine + redis + api + nginx + websocket client |
| `ci_workflows/` | tests on `.github/workflows/*.yml` (existence, structure, triggers) | no container (static YAML analysis) |
| `docker_compose/` | tests on `docker-compose.yml` (`compose config` syntax, expected services, well-defined healthchecks) | docker daemon but no container actually up |

### Why not one folder per container pair (edge)

Too fine-grained: there are 14+ edges in the graph. You end up with 14 folders of 1-2 files each, and semantic cohesion is lost ("test_redis_market_data" vs "test_redis_vol_engine" are nearly identical but duplicated).

**Conversely**: `pipeline_vol/` groups `test_market_data_writes_spot.py`, `test_vol_reads_spot_writes_surface.py`, `test_vol_signal_publishes.py` — all readable in the same folder as one logical chain.

### Why not one folder per scenario (e2e)

`pipeline_api_serving/` is not e2e: it tests the api in isolation from the frontend. E2e (user clicks a button, sees a number change) is the manual stack validation or Playwright on the frontend side.

---

## `tests/fixtures/` — reusable builders

To avoid duplicating setup data in every test:

```python
# tests/fixtures/positions.py
from persistence.models import OpenPosition

def make_long_call(strike=1.17, qty=1, ...):
    return OpenPosition(
        symbol="EURUSD", instrument_type="FOP",
        right="C", strike=strike, quantity=qty, ...
    )
```

Importable from any test:

```python
from tests.fixtures.positions import make_long_call
```

---

## Pytest configuration

Lives in `pyproject.toml § [tool.pytest.ini_options]` (single source of
truth, cf. CLAUDE.md). Markers and `testpaths` are already defined there:

```toml
[tool.pytest.ini_options]
testpaths = ["tests/unit", "tests/integration"]
markers = [
    "integration: requires real IB Gateway (IB_RUN_INTEGRATION=1)",
    "db_integration: requires real Postgres (DB_RUN_INTEGRATION=1)",
    "redis_integration: requires real Redis (REDIS_RUN_INTEGRATION=1)",
]
```

CI exercises these markers in `.github/workflows/ci.yml` (job
`live-integration` for db + redis, manual `integration` job for IB).

---

## Common commands

```bash
# The whole suite (unit + integration)
python -m pytest

# Unit tests only (fast, dev loop)
python -m pytest tests/unit

# A single unit module
python -m pytest tests/unit/core/ -v

# A specific integration pipeline
python -m pytest tests/integration/pipeline_db_writer/ -v
```

---

## Design decisions (FAQ)

**Q: Why not a `tests/smoke/` in pytest?**
A: The project's smoke tests are **interactive** (notebooks with OK/FAIL output + troubleshooting markdown). Recoding that in pytest would be redundant. Keep the separation: pytest = automatic CI-friendly, notebooks = manual inspection.

**Q: What if a test is both unit AND integration?**
A: Probably two distinct tests. The unit layer mocks the I/O; the integration layer actually exercises it. If you can't split them, classify as integration (the wider safety net wins).

**Q: Is `tests/unit/engines/` mandatory or can I flatten to `tests/unit/{db_writer,market_data,risk,vol}/`?**
A: Mirror `src/`. `src/engines/` exists to group the 5 engines, so `tests/unit/engines/` does too. Path-to-path mapping consistency > saving depth.
