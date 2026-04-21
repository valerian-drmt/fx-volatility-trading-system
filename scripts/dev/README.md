# scripts/dev — Development and manual-test scripts

Reusable scripts invoked by developers (not by the app at runtime) to bootstrap the database, run manual smoke tests, and validate PR functionality before merging.

Each script has a single clear purpose, a top-level docstring, and is safe to run repeatedly (idempotent when possible). They are **not** part of the production code path — the app never imports from `scripts/`.

## Prerequisites

Most scripts assume :

```bash
# Postgres dev instance running
docker compose -f docker-compose.dev.yml up -d postgres

# Environment
export PYTHONPATH=src
export DATABASE_URL=postgresql+asyncpg://fxvol:fxvol@localhost:5432/fxvol
```

On Windows PowerShell replace the `export` with `$env:VAR = "value"`.

## Scripts

| Script | Purpose | First PR |
|---|---|---|
| `db_create_tables.py` | Create all ORM tables in Postgres from `persistence.models.Base.metadata`. Used before Alembic is wired up (before R1 PR #5). | R1 PR #3 |
| `db_drop_tables.py` | Drop all ORM tables. Inverse of `db_create_tables.py`. | R1 PR #3 |
| `smoke_r1_p3_core_models.py` | End-to-end functional smoke test of the R1 PR #3 ORM models : CRUD on Position, cascade to PositionSnapshot, UNIQUE on Trade.ib_order_id, JSONB roundtrip on AccountSnap. | R1 PR #3 |
| `smoke_r1_p4_vol_analytics.py` | End-to-end functional smoke test of the R1 PR #4 ORM models : nested JSONB roundtrip on VolSurface, Postgres `#>>` operator, UNIQUE (timestamp, underlying), CHECK on Signal.signal_type, JSONB arrays on BacktestRun, server_default on created_at. | R1 PR #4 |

## Adding a new script

1. Create `scripts/dev/<name>.py` with a docstring describing what the script validates
2. Add an entry to the table above with the PR that introduced it
3. Keep scripts **idempotent** when possible (re-runnable without cleanup)
4. Prefer `async` with `asyncio.run(main())` for anything touching the DB (consistent with the rest of the codebase)
