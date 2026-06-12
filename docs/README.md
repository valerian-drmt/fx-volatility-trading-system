# `docs/` — index

Landing page. Each doc here answers one question. Pick the one that
matches what you came for.

## Operator runbooks

| Doc | What it covers |
|---|---|
| [run-local-stack.md](run-local-stack.md) | Boot the full v2 stack on a developer laptop (`scripts/ops/start_stack.ps1`, expected log tabs, common failure modes). |
| [deployment.md](deployment.md) | Prod deploy on EC2 — systemd unit, IAM role, SSM secret loading, Let's Encrypt, container image promotion. |
| [branch-protection.md](branch-protection.md) | Enforced GitHub branch ruleset on `main` (required reviewers, required CI checks, no force-push). |

## Architecture & design

| Doc | What it covers |
|---|---|
| [vol_trading_pca/index.md](vol_trading_pca/index.md) | Top-level entry point for the vol-PCA research-to-trade roadmap. Step-by-step specs in `vol_trading_pca/specs/`. |
| [vol_trading_pca/project_architecture.md](vol_trading_pca/project_architecture.md) | Canonical v2 architecture reference — services, data flows, ports, container roles. |
| [vol_trading_pca/events_pipeline_spec.md](vol_trading_pca/events_pipeline_spec.md) | Multi-source economic-events pipeline (FRED + ECB + BoE + FOMC + Eurostat + ONS) — schema, dedup, scheduler. |
| [vol_trading_pca/DECISIONS.md](vol_trading_pca/DECISIONS.md) | ADR-style log of design decisions made during the research-to-trade migration. |

## Engineering policy

| Doc | What it covers |
|---|---|
| [structure-refactor-plan.md](structure-refactor-plan.md) | The 20-step refactor plan that produced the current src-layout (services → engines, pyproject.toml as the single source of truth, etc.). Read this if you want to understand *why* the layout looks the way it does. |
| [preventing-spaghetti-code.md](preventing-spaghetti-code.md) | Project-agnostic reference on what causes spaghetti code, the principles that prevent it, and the specific guard rails for AI coding agents (Claude Code, Cursor, …). Theory + policy, not codebase commentary. |

## Reading order for a new contributor

1. Repo root [`README.md`](../README.md) — features + quickstart in 5 minutes.
2. [`vol_trading_pca/project_architecture.md`](vol_trading_pca/project_architecture.md) — see how the 10 containers fit together.
3. [`run-local-stack.md`](run-local-stack.md) — boot the stack locally.
4. [`structure-refactor-plan.md`](structure-refactor-plan.md) — understand the folder vocabulary (api / bus / core / engines / persistence / shared).
5. Pick a sub-system spec from [`vol_trading_pca/specs/`](vol_trading_pca/specs/) and read its corresponding code under `src/engines/` or `src/api/`.
