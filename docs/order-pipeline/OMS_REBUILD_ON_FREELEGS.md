# OMS rebuild on the free-legs line — context & directive

> Read this together with [`OMS_ARCHITECTURE.md`](./OMS_ARCHITECTURE.md)
> (the authoritative target design: ERD, FSM, reaper, invariants I1–I7,
> scenarios T1–T8, migration mapping §11).

## TL;DR
The OMS backend refactor (order FSM + reaper, forward position projection,
reconciliation breaks, reservation ledger) was implemented on a branch that had
**diverged from the working frontend line ~3 months earlier**, so it never
contained the current frontend (Signal/Trade builder, free-leg classifier, …).
A `git merge` of the two lines produces **140 conflicting files** — not viable.

**Decision: re-implement the OMS backend on the current `main` (the free-legs
line), guided by `OMS_ARCHITECTURE.md`. Do NOT git-merge the OMS branch.**

## The two lines (why we're here)
- **`main` (local) = the free-legs line — CANONICAL base.** The real working
  code: React frontend `frontend/src/voldesk/` (Signal/Trade builder, free-leg
  `classify_legs`, Contract column, Calendar/skew view, close-confirmation UX),
  the current backend + DB schema, `docs/order-pipeline/`. The Docker stack runs
  from here.
- **`origin/main` (GitHub) = the OMS line** (built earlier, PRs #196-204). Has
  the OMS backend refactor but **not** the frontend. Divergent: ~594 commits
  ahead / 166 behind, common ancestor ~3 months old. **Reference only — do not
  merge, do not build on it.** The design it implemented is captured in
  `OMS_ARCHITECTURE.md`, which lives on this (free-legs) line.

## What to build (re-apply the spec to the free-legs code)
From `OMS_ARCHITECTURE.md`, on top of the current `main`:
- **D1 — order finite-state machine + `reaper` loop.** Terminalize ghost orders;
  no more multi-hour stuck orders in a non-terminal state.
- **D2 — forward `position_projector`.** Build positions forward from the fill
  log, not back-attributed from the netted IB mirror.
- **D3 — `reconciliation_break` materialisation.** Book (fill log) vs broker
  (IB mirror); display authority = the fill log, never the mirror.
- **D4 — reservation ledger (`reserved_qty`, `available >= 0`).** Prevents
  over-close / double-close.
- Enforce invariants **I1–I7** as property tests; cover scenarios **T1–T8**.
- Data model per **§11** of the spec — adapt to the free-legs ORM classes where
  equivalents already exist (do not blindly duplicate tables).

## What NOT to touch
- The **frontend** (`frontend/src/voldesk/`) must keep working (Signal/Trade
  builder, free-leg flow). Only change API response shapes if strictly required,
  and regenerate the typed client (`npm run gen:api`) so the OpenAPI drift check
  passes.
- The working ops scripts (`scripts/local/`, `scripts/aws/`, `scripts/fxvol.ps1`)
  and `docker-compose.yml`.

## Git rules (STRICT — this is why the previous attempt went sideways)
- Work **only on local `main`** (the free-legs line). **Commits only.**
- **NEVER push to GitHub.** No `git push`, no PR, no merge, no tag.
- **NEVER `git worktree`**, never create a branch, never switch off `main`.
- Conventional Commits, **English**, **no** `Co-Authored-By` / bot name.
- Before every commit: `git branch --show-current` must print `main`.

## Recommended sequencing (one commit + tests per step)
Follow the spec's priority order:
1. **P0** — order FSM + reaper.
2. **P1** — forward position projector.
3. **P1** — reconciliation break materialisation.
4. **P2** — reservation ledger (`available >= 0`).

After each step: `python -m pytest` (unit) green, `python -m ruff check src tests`
clean, and `PYTHONPATH=src lint-imports` (architecture contracts) intact. Add an
Alembic migration for every schema change (revision id ≤ 32 chars).
