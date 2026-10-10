# Audit 6 — Architecture & Code Quality

## Why

The v2 refactor established hard layer boundaries (core pure, adapters isolated, engines
independent of api) enforced by import-linter. This audit verifies the boundaries are
real *in substance*, not just in import graphs — and that the codebase entering v1.0
doesn't carry dead weight from the R8/v2 migrations (removed desktop app, renamed
modules) that will confuse every future change.

## Scope

Entire `src/` tree, `.importlinter`, `pyproject.toml` tooling config, plus repo hygiene
(orphan files, legacy scripts).

## What to check

### Boundary substance (beyond import-linter)
- [ ] `core/` purity in behavior, not just imports: no hidden I/O (file reads, env vars,
      network, `datetime.now()` where injection is expected), deterministic functions.
- [ ] The one allow-listed exception (cockpit → `engines.execution.structures`) is still
      data-classes-only; nothing behavioral leaked through it.
- [ ] Duplicated domain logic across layers (e.g. tenor math or unit conversions
      reimplemented in api/engines instead of imported from core).
- [ ] `shared/` hasn't become a dumping ground: each module there is genuinely
      cross-cutting infra.

### Dead code & migration residue
- [ ] Unreferenced modules/functions (vulture or grep-based sweep), especially anything
      referencing the removed Qt desktop app or pre-rename paths (`api/models/`,
      `api/services/`, `src/services/`, `redis_client`, `db_queue`).
- [ ] Legacy test dirs (`tests/old`) — status and plan.
- [ ] Feature flags / commented-out blocks that no longer have a future.

### Consistency & typing
- [ ] mypy is advisory in CI — measure the actual error count per package; propose
      which packages can flip to enforced (start with `core/`, the pure layer).
- [ ] Pydantic v2 idioms consistent across `api/schemas/`; no v1 leftovers.
- [ ] Error-handling policy consistent: engines' top-level loops catch-and-log vs
      crash-and-restart — one deliberate policy, not per-file accident.
- [ ] Async hygiene: no blocking calls (sync IB wrapper, CPU-heavy calibration) on the
      event loop in api; executors/threads used where needed.
- [ ] Logging: structlog everywhere, consistent event naming, no `print()`.

### Config & duplication
- [ ] `shared/config.py` is truly the single env-var schema — no `os.environ` reads
      scattered elsewhere.
- [ ] Container/module boundaries in docs match reality (CLAUDE.md/README table vs
      actual Dockerfile contents).

## Method

Static analysis pass (ruff --select ALL as a *survey*, vulture, import-linter, mypy
per-package counts) + manual review of flagged areas. Metrics recorded so v1.x audits
can compare.

## Deliverable & exit criteria

- Findings report + a "mypy enforcement roadmap" (which packages flip strict, in order).
- **Exit:** no behavioral leaks through boundaries; migration residue removed or
  ticketed; zero Blocker/High (rare in this audit — most findings will be Medium).