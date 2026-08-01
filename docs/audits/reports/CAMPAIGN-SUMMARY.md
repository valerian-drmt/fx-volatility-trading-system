# Pre-Release Audit Campaign — Final Summary (2026-08-01)

All 11 audits complete. Findings-only: no source file was modified by any audit, no git
state changed, no live stack / IB / prod contact, no secret read.

## Totals

| # | Audit | Blocker | High | Medium | Low | Total |
|---|---|---|---|---|---|---|
| 1 | Security | 0 | 3 | 8 | 9 | 20 |
| 2 | Quant models | 0 | 2 | 8 | 5 | 15 |
| 3 | Risk engine | 2 | 7 | 12 | 6 | 27 |
| 4 | Execution | 1 | 11 | 13 | 4 | 29 |
| 5 | Data integrity | 4 | 13 | 16 | 5 | 38 |
| 6 | Architecture | 0 | 2 | 8 | 4 | 14 |
| 7 | Test coverage | 3 | 8 | 7 | 2 | 20 |
| 8 | Infra & ops | 2 | 11 | 16 | 6 | 35 |
| 9 | Frontend | 3 | 5 | 10 | 8 | 26 |
| 10 | Dependencies | 0 | 4 | 7 | 4 | 15 |
| 11 | Docs & release | 2 | 15 | 27 | 14 | 58 |
| | **Total** | **17** | **81** | **132** | **67** | **297** |

**v1.0 is not releasable in the current state.** Per `../README.md`, Blockers must be
fixed and Highs fixed-or-waived.

## The meta-finding

**The written record describes a safer system than the one that exists**, and that is the
mechanism by which most of these survived. Three examples, each from a different audit:

- `greek-limits-spec.md` says "**Enforce** per-bucket caps"; `engines/risk/engine.py`
  contains zero occurrences of `cap` or `limit`, and `compute_caps` has exactly one caller
  — a display endpoint with no frontend consumer (D-01 / R3-08).
- README says "live IB tick stream", "Real-time"; the code requests
  `reqMarketDataType(3)` — 15-20 minutes delayed — and type 4 (delayed-**frozen**) in
  execution (D-02 / FE-03).
- README says `VolConfig` is "hot-reloaded into the engines"; `apply_config` ends with
  `del config` and the entire config tree is inert (D-10 / DI-13).

And the test suite reinforced the illusion rather than breaking it: **8 of the 10 Wave-1/2
Blockers had zero executable coverage of the defective line, 0 of 10 were caught by any
assertion, and 7 tests assert defective behaviour as the intended contract** — including
`test_bs.py::test_degenerate_inputs_return_zero`, which will turn red when QM-01 is fixed.
`tests/fixtures/` is empty but for `.gitkeep`, so no canonical payload exists and schema
drift breaks nothing.

## Five themes

Seventeen Blockers are not seventeen independent defects. Fix by theme, not by ticket.

### A — Duplicated / degenerate pricing (6 findings, 3 Blockers)
Three divergent Black-Scholes implementations (A6-02); `T<=0` returns 0.0 instead of
intrinsic (QM-01); `interpolate_iv` reads a retired schema and always returns `None`
(QM-02). Downstream: **$1.25M of delta → NULL overnight** on expiry (R3-01); the
**automated delta hedge can never fire** and fails open (R3-02); every 60s MTM re-prices
at a hardcoded **1.085** spot (DI-01). *One consolidation into `core/pricing` closes all
six.*

### B — Nothing bounds exposure (5 findings)
No greek limit enforced anywhere (R3-08); risk gate at submit fails open, 2 checks
hardcoded to pass and 2 deleted (EX-08); no kill switch exists (EX-09); no price collar
and no notional cap, on delayed-frozen quotes (EX-10); `refresh_book_state` runs only on
the mock path so live book vega stays 0.0 forever (R3-03). **The only theme whose failure
mode is unbounded *action* rather than wrong numbers.** D-01 explains why it went
unnoticed for so long.

### C — Stale data presented as live (5 findings, 3 Blockers)
Backend sends real data timestamps; frontend discards them and re-stamps `Date.now()`
(FE-01); freshness computed once at fetch, never re-evaluated (FE-02); "Market open"
asserted from a never-expiring `latest` dict republished at 5 msg/s (FE-03); no staleness
field on any vol read path (DI-14); WS status tracked correctly but thrown away before
render (FE-04). **Every night and weekend the public desk shows a green dot, "Market
open", and Friday's close as a live 5-decimal mid** — on data that is 15-20 min delayed
even during the session.

### D — Nothing observes the system (4 findings, 1 Blocker)
Zero alerting anywhere — SNS topic exists, nothing publishes to it (OPS-01); with Redis
pub/sub (no backlog) and Docker never restarting `unhealthy` containers, db-writer
downtime is **permanent silent loss of the primary time series**. `/api/v1/health` returns
a hardcoded `{"status":"OK"}` and is both the deploy smoke assertion and the container
healthcheck, so both pass with Postgres down or the old image still serving (OPS-12).
Nothing served over HTTP identifies the running build (D-08). The watchdog covers 2 of 6
engines and omits execution-engine (OPS-09).

### E — The safety net was never tested (3 findings, 3 Blockers)
The coverage inversion above (T7-01/T7-02); every DB unit test runs on in-memory SQLite
with `PRAGMA foreign_keys` never enabled (T7-03); **the Postgres backup has never been
restored** and its presence on the host is unverified — `RESTORE.md`'s own log reads
"none yet — run once before go-live" (OPS-02). No round-trip test exists: the
`frontend-e2e-compose` job boots the full stack for 20 minutes, then intercepts every
`/api/v1/*` call.

## Independent Blockers (outside the themes)

- **EX-01** — 10s HTTP budget on a close handler that also runs a full position sync; the
  timeout stamps the order `rejected`, a state the close-stacking guard ignores, so the
  retry places **a second live close of the same lots**.
- **DI-02** — db-writer idempotency is nominal: 4 of 5 `IDEMPOTENT_TABLES` key
  `ON CONFLICT` on a producer-side `datetime.now()`.
- **DI-03** — closing a position cascades away its entire greeks/PnL history
  (~300k rows/week/position); the code comment says the trail survives "in snapshots" —
  that *is* the cascaded table.
- **DI-35** — one 90-day-old referenced `pca_signal_id` rolls back the shared session and
  a bare `except` swallows it ⇒ both prune jobs stop **permanently and silently**.

## Recommended sequencing

1. **Theme B** — bounded action first. Kill switch, notional/collar caps, fail-closed risk
   gate, EX-01's timeout budget. Highest consequence, smallest surface.
2. **Theme E prerequisites** — canonical payload fixtures, frozen clocks, real-shape IB
   mocks, FK-enabled DB tests, and a restore drill. These must exist *before* the other
   fixes, or nothing verifies them.
3. **Theme A** — one pricing consolidation, six findings, three Blockers.
4. **Theme D** — alerting + a real health endpoint + build identification.
5. **Theme C** — largely mechanical: thread timestamps the backend already sends, render
   the WS status the hook already tracks.
6. **Docs last** — rewrite against the fixed system, not the intended one.

## Owed to Valérian (cannot be done by audit)

- **Rotate `REDIS_PASSWORD`** — F-02: logged in cleartext at every API startup into stdout
  and Loki for the full retention window.
- **~60 live-verification drills** across audits 3/4/5/8/9, each with exact commands:
  autogenerate diff, kill-db-writer-mid-batch, poison-message, DI-35's blocking-FK query,
  WS disconnect drills, reboot survival, **the first-ever backup restore**.
- **Three legal/business decisions**: IB market-data redistribution on the public endpoint
  (DEP-04); FRED ToU prohibiting database storage of their content; ib-insync → ib_async
  migration before v1.0 (DEP-03).

## Deliverables produced

Per-audit reports `01`–`11` in this folder, plus `WAVE1-SUMMARY.md`, `WAVE2-SUMMARY.md`,
`08-runbook-draft.md` (reboot / rollback / restore / IB recovery, unverified steps marked)
and `RELEASE-GATE-v1.0.md` (every gate with honest current status).
