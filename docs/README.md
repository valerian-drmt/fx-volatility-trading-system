# `docs/` — index

Landing page. Each doc here answers one question. Pick the one that
matches what you came for.

## Operator runbooks

| Doc | What it covers |
|---|---|
| [run-local-stack.md](run-local-stack.md) | Boot the full v2 stack on a developer laptop (`scripts/local/stack.ps1`, expected log tabs, common failure modes). |
| [docker-cheatsheet.md](docker-cheatsheet.md) | Day-to-day docker compose commands : restart, rebuild, logs, exec, Redis inspection, IB Gateway recycle. |
| [db_schema_drift_workflow.md](db_schema_drift_workflow.md) | Once the **DB Schema** dev tab surfaces drift between `models.py` (ORM) and the live Postgres, how to feed the fix back through alembic. |
| [branch-protection.md](branch-protection.md) | Enforced GitHub branch ruleset on `main` (required reviewers, required CI checks, no force-push). |

## Architecture & design

| Doc | What it covers |
|---|---|
| [vol_trading_pca/events_pipeline_spec.md](vol_trading_pca/events_pipeline_spec.md) | Multi-source economic-events pipeline (FRED + ECB + BoE + FOMC + Eurostat + ONS) — schema, dedup, scheduler. Kept as the canonical design reference for `src/api/orchestration/events/`. |

## Observability

| Doc | What it covers |
|---|---|
| [observability/CONVENTIONS.md](observability/CONVENTIONS.md) | Naming + label cardinality rules for metrics, log fields, and spans. |
| [observability/RUNBOOKS.md](observability/RUNBOOKS.md) | Operator playbooks for the obs stack (Loki, Prometheus, Tempo, Grafana). |

## Where the rest lives

- **Git workflow + commit conventions + PR cadence** → `releases/git_management/WORKFLOW.md` (single source of truth).
- **Architecture diagram + container roles + folder layout** → repo root `README.md` and the in-app **Stack** dev tab.
- **Live ER diagram + drift detection** → in-app **DB Schema** dev tab (introspects `Base.metadata` at runtime ; no static schema doc to drift against).
- **API contract** → `http://localhost/docs` (FastAPI Swagger) + generated `frontend/src/api/schema.d.ts`.

## Reading order for a new contributor

1. Repo root [`README.md`](../README.md) — features + quickstart in 5 minutes.
2. [`run-local-stack.md`](run-local-stack.md) — boot the stack locally.
3. In-app dev console → **Stack** tab — see the 17 containers and their wiring.
4. In-app dev console → **DB Schema** tab — see every table and FK without reading code.
5. Pick a subsystem under `src/engines/` or `src/api/` and read its code.
