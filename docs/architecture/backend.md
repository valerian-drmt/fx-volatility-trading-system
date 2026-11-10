# Backend layout

All Python lives under `src/` (PyPA src-layout); imports resolve against
`PYTHONPATH=src` (e.g. `from api.main import app`, `from core.vol.svi import
calibrate_svi`). `pyproject.toml` is the single source of truth for dependencies,
ruff, pytest, and mypy config. The six top-level packages split into two
**presentation** layers, two **adapters**, one **pure domain** layer, and a
**cross-cutting** layer.

![Backend modules and the import-linter contract boundaries](../diagrams/backend-modules.svg)

## Packages

| Package | Role | Highlights |
|---|---|---|
| `api/` | FastAPI app (presentation) | `main.py` (app + lifespan: events scheduler, WS bridge), 16 routers, `ws/` (connection manager + Redis→WS bridge), `middleware/`, `schemas/` (Pydantic v2), `orchestration/` (use cases + `events/` pipeline) |
| `engines/` | 5 long-running services (presentation) | `market_data/`, `vol/`, `risk/`, `db_writer/`, `execution/` — each an `engine.py` + `main.py` loop |
| `core/` | Pure domain, no I/O | `vol/` (svi, ssvi, garch, har_rv, pca_engine, gmm_regime, regime_engine, vrp, yang_zhang, …), `pricing/bs.py`, `risk/greeks.py` |
| `persistence/` | DB adapter | `models.py` (27 ORM classes), `db.py` (async engine + session), `writer.py` (`AsyncDatabaseWriter`), `migrations/versions/` (Alembic) |
| `bus/` | Redis adapter | `client.py` (async + sync factory), `publisher.py`, `channels.py`, `keys.py` |
| `shared/` | Cross-cutting infra | `config.py` (base `Settings` — env-var schema), `logging.py` (structlog), `ib_connection.py` (IB wrapper + backoff), observability helpers |

The `api` container image bundles `api` + `core` + `persistence` + `bus`; each
engine bundles its own `engines/<name>` plus the libs it needs. `core`, `bus`,
`persistence`, and `shared` have no container of their own.

## Import contracts

Dependency direction is enforced by [`import-linter`](../../.importlinter) in CI
(`lint-imports`). Five contracts:

| # | Contract | Rule |
|---|---|---|
| 1 | **core is pure** | `core` may not import `api`, `engines`, `bus`, `persistence` or `shared` (#332) — it is the only layer with no project imports: no I/O, no logger, no clock (callers pass `as_of`), deterministic. `core/contracts.py` (contract metadata) and `core/market_hours.py` (the hours rule) live here; `shared/market_hours.py` adds the clock and the operator override. |
| 2 | **bus is an adapter** | `bus` may not import `api` or `engines`. |
| 3 | **persistence is an adapter** | `persistence` may not import `api` or `engines`. |
| 4 | **engines do not depend on api** | `engines` publish to Redis and read the DB; they never call the FastAPI app. |
| 5 | **api does not depend on engine internals** | `api` may *route* to the execution-engine over HTTP but must not Python-import engine internals. No exception since #331 (the trade-preview structures live in `core/structures.py`). |

The result is a strict layering: presentation (`api`, `engines`) depends on adapters
(`bus`, `persistence`) and on `core`; adapters depend only on `core`; `core` depends
on nothing in the project. This rules out service-back-into-api loops and keeps the
numerical domain code testable in isolation.

## Error policy (#337)

| Kind | On an unexpected exception | Why |
|---|---|---|
| cyclic engine (market-data, vol, risk, analytics, db-writer) | inside a cycle: the step is logged and the cycle continues; out of the cycle loop: the process ends, compose restarts the container (`restart: unless-stopped`), the heartbeat TTL raises `EngineDown` meanwhile | a cycle is idempotent and the next one repairs the state; a dead loop must not look alive |
| execution engine side loops (position sync, heartbeat, stuck-order watcher, order reconcile, reaper, position reconcile, IB watchdog) and the analytics side loops | `shared.tasks.run_supervised`: logged as `engine_task_crashed` with the task name, counted in `engine_task_restarts_total{engine,task}`, restarted after a 1 / 5 / 30 s backoff; cancellation passes through | the HTTP server would stay up with a silently dead loop otherwise |
| one-shot tasks (fill callbacks, hedge finalisers) | `shared.tasks.log_task_result` done-callback: the exception reaches the log as `background_task_failed` | asyncio only reports an unretrieved exception at garbage collection |
| api request | the exception handler answers an opaque 500 with the trace id (#297); the request is the unit of failure | |

## Type checking (#335)

`python -m mypy src/shared src/bus src/persistence` is a blocking CI step (zero
errors); the whole-tree run stays advisory. Counts on 2026-11-10: core 15,
api 59, engines 96. Order of the burn-down: `core` (small, pure, the numbers
depend on it), then `api`, then `engines`; a package joins the gated command
when its count reaches zero, with a `[[tool.mypy.overrides]]` block if it
needs stricter settings than the baseline.

## Related

- [overview.md](overview.md) — how these packages map to containers.
- [data-flow.md](data-flow.md) — how the packages exchange data at runtime.
- [database.md](database.md) — the `persistence` ORM models in detail.
