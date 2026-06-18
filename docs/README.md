# `docs/` — index

Landing page. Each doc here answers one question. Pick the one that
matches what you came for.

## Operator runbooks

| Doc | What it covers |
|---|---|
| [run-local-stack.md](run-local-stack.md) | Boot the full v2 stack on a developer laptop (`scripts/ops/start_stack.ps1`, expected log tabs, common failure modes). |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Prod deploy on EC2 — target architecture, expected GitHub secrets, SSM secret loading. |
| [docker-cheatsheet.md](docker-cheatsheet.md) | Day-to-day Docker commands — restart / recreate / rebuild individual services or the whole stack. |
| [IB_OPERATIONS.md](IB_OPERATIONS.md) | IB Gateway operations — the single-engine-set-to-IB rule, connectivity, account modes. |

## Architecture & design

| Doc | What it covers |
|---|---|
| [db-schema.md](db-schema.md) | The 24 ORM-mapped persistence tables by domain — purpose, key columns, FK relationships, write paths, dropped/folded tables. |
| [VOL_MODEL.md](VOL_MODEL.md) | Volatility engine — mathematical documentation of the `σ_mid → σ_fair → signal → trade` pipeline. |
| [ORDER_EXECUTION.md](ORDER_EXECUTION.md) | Order execution — instruments, multi-leg structure construction, the Step 4 submit/fill flow. |
| [vol_trading_pca/events_pipeline_spec.md](vol_trading_pca/events_pipeline_spec.md) | Multi-source economic-events pipeline (FRED + ECB + BoE + FOMC + Eurostat + ONS) — source pattern, dedup, scheduler. |

## Observability & performance

| Doc | What it covers |
|---|---|
| [observability/CONVENTIONS.md](observability/CONVENTIONS.md) | Prometheus metrics, per-engine `/metrics` ports, `cycle_id` propagation and log conventions. |
| [observability/RUNBOOKS.md](observability/RUNBOOKS.md) | Debug runbooks driven by the LGTM stack — stuck vol-engine cycle, slow Postgres writes, … |
| [PERFORMANCE.md](PERFORMANCE.md) | Performance notes — per-service RAM overhead of the engine split, cold-start timings. |

## Engineering policy

| Doc | What it covers |
|---|---|
| [BRANCH_PROTECTION.md](BRANCH_PROTECTION.md) | Enforced GitHub branch ruleset on `main` (required CI checks, no force-push). |
| [CONFIG.md](CONFIG.md) | Configuration files — `config/vol_config.json` + status-panel settings, and how versioned config is loaded. |

## Front-end

| Doc | What it covers |
|---|---|
| [../frontend/LIVE_UI_DESIGN_BRIEF.md](../frontend/LIVE_UI_DESIGN_BRIEF.md) | Design brief for the recruiter-facing front-end rebuild (R11) — data/action catalogue + navigation proposal. |

## Legacy (pre-v2 PyQt desktop, removed in R8)

Kept for history — they describe the single-process Qt application that the
microservice stack replaced. Not current.

| Doc | What it covered |
|---|---|
| [THREADS.md](THREADS.md) | Threading architecture of the old desktop app (main Qt loop + worker threads). |
| [UI.md](UI.md) | UI architecture of the old desktop app (window, 3-column layout). |

## Reading order for a new contributor

1. Repo root [`README.md`](../README.md) — features + quickstart in 5 minutes.
2. [`db-schema.md`](db-schema.md) — the data model the whole system revolves around.
3. [`run-local-stack.md`](run-local-stack.md) — boot the stack locally.
4. [`VOL_MODEL.md`](VOL_MODEL.md) — the quant pipeline that produces the signals.
5. Pick a sub-system and read its code under `src/engines/` or `src/api/`.
