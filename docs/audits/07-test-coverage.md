# Audit 7 — Test Coverage

## Why

Coverage percentage is not the point — *what* is covered is. A suite can be green at 80%
while the order-submission retry path, the arbitrage checks, and the reconnect logic sit
in the uncovered 20%. This audit maps tests to the **critical paths identified by audits
2–5** and closes the gap between "tested" and "the dangerous parts are tested". It runs
after fixes from earlier audits land, so it measures the final pre-release state.

## Scope

- `tests/unit/`, `tests/integration/`, `tests/fixtures/` vs `src/` (1-to-1 mirror per
  `tests/STRUCTURE.md`).
- Frontend: vitest suites + MSW handlers, playwright e2e specs.
- CI wiring: which suites actually gate merges, coverage thresholds.

## What to check

### Critical-path coverage (the core of this audit)
Build a matrix: for each critical path below, name the test(s) that exercise it — "none"
is a finding whose severity matches the path's risk.

- [ ] Pricing/greeks edge cases + finite-difference properties (from Audit 2).
- [ ] Surface no-arbitrage checks on calibration output (from Audit 2).
- [ ] Risk limit enforcement + fail-closed on stale data (from Audit 3).
- [ ] Order idempotency, restart reconciliation, duplicate-fill handling (from Audit 4).
- [ ] db-writer retry/idempotency, malformed-payload handling (from Audit 5).
- [ ] IB reconnect/backoff behavior in `shared/ib_connection.py`.
- [ ] WS bridge: Redis → WebSocket fan-out, client disconnect cleanup.
- [ ] Events pipeline: scheduler + each source parser against fixture payloads.

### Suite quality
- [ ] Tests that can't fail (no assertions, over-mocked to tautology) — sample review.
- [ ] Mock realism: IB mocks reflect actual ib_insync shapes; MSW handlers match real
      API responses (generated schema drift check covers types, not values).
- [ ] Flakiness: repeated-run pass (`pytest -p no:randomly` vs randomized, `--count` on
      suspect files); time-dependent tests use frozen clocks.
- [ ] Integration markers: gated suites still pass against live local resources
      (`DB_RUN_INTEGRATION`, `REDIS_RUN_INTEGRATION`); IB suite runs once against paper.

### Thresholds & CI
- [ ] Backend coverage measured per-package, not just global — `core/` and
      `engines/execution/` deserve higher bars than `api/routers/`.
- [ ] Frontend ≥70% gate: check it's not satisfied by trivial component tests while
      stores/hooks are dark.
- [ ] e2e: the compose-based suite covers at least one full round-trip
      (UI → api → redis → engine → back).

## Method

Coverage run with per-package report, then manual matrix construction against the
audit 2–5 findings lists. New tests written here are the *output* (filed as issues or
added directly per severity), not fixes smuggled into the audit.

## Deliverable & exit criteria

- Critical-path × test matrix committed to `tests/` docs; per-package coverage baseline
  recorded for future comparison.
- **Exit:** every Blocker/High-risk path from audits 2–5 has at least one real test;
  no known-flaky test left unquarantined.