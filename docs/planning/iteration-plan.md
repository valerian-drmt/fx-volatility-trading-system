# Iteration plan

Operational plan for the open backlog: which tickets to take, in which order,
and why. Built on 2026-11-05 from the 83 open issues on project board #5
(12 epics, 71 work tickets). Status lives on GitHub; this file holds the
ordering and the reasoning, and is revised at the end of every iteration.

- Board: https://github.com/users/valerian-drmt/projects/5
- Sources: the audit campaign (`docs/audits/`, label `audit`), the hot / cold
  path programme (`docs/hot-cold-path/`, label `hot-cold-path`), and three
  standalone tickets (#286, #287, #295).

## How to use this file

1. At the start of an iteration, move its tickets to **Ready** on the board;
   everything else stays in **Backlog**. Ready means "this iteration", nothing
   else.
2. Work top to bottom inside the iteration. A ticket is done when its
   acceptance list is checked and the PR is merged through `git update`.
3. At the end of the iteration: close the problems the tickets closed (status
   column in `docs/hot-cold-path/01-problems.md`), tick the epic checklists,
   move unfinished tickets to the next iteration here, and commit this file.
4. Filler tickets (marked below) are picked when a planned ticket is blocked.

## Capacity model

| Size | Engineering days | Count open | Days |
|---|---|---|---|
| XS | 0.25 | 6 | 1.5 |
| S | 0.75 | 29 | 21.75 |
| M | 2 | 30 | 60 |
| L | 4 | 6 | 24 |
| **Total work tickets** | | **71** | **~107** |

One engineer plus Claude, about 3.5 focused days per week, so an iteration is
**two weeks with about 7 engineering days**. The PR drip (2 to 3 per day) is
never the limit: an iteration ships 6 to 10 PRs. Fifteen iterations cover the
backlog; the v1.0 release gate (`docs/audits/reports/RELEASE-GATE-v1.0.md`)
can close after iteration 14 if nothing new is found.

## Ordering rules

1. **Safety before everything**: the eight P0 tickets (bounded action, secrets,
   backup, alerting) go first. The campaign summary's theme B.
2. **Enablers before the work they enable**: the test harness (#338) before the
   critical-path tests (#339); the IV lookup fix (#310) before limit
   enforcement (#315); execution publishing (#366) before the desk subscribing
   (#367); latency metrics (#370) before any hardware decision (#374).
3. **Visible wins early**: CPU weights (#365) and never-blank panels (#369)
   cost a day together and change how the desk feels on day one.
4. **Hot path before cold path**: anything an operator waits on outranks
   anything that is merely slow.
5. **Docs last, written against the fixed system**: the docs and release
   tickets close the plan, except the runbook (#346), which follows the restore
   drill it documents.
6. **One area per PR, one PR per ticket** unless a ticket says two.

## Dependency map

```
#338 harness ──▶ #339 critical tests
#310 IV lookup ──▶ #315 enforce limits ──▶ #316 VaR robustness
#366 T2 publish ──▶ #367 T3 subscribe
#366 T2 publish ──▶ #370 T6 metrics ──▶ #374 T10 split hosts (only if needed)
#365 T1 weights ──▶ #371 T7 capacity dial ──▶ #372 T8 chain knob
#329 hot-reload ──▶ #371 T7 capacity dial
#368 T4 materialise ──▶ #373 T9 write-side ; #368 closes #286
#343 restore drill ──▶ #346 runbooks
#331 pricing into core ──▶ #309 edge cases (same kernel)
#297, #287 secrets ──▶ password rotations (user action, recorded in #297)
```

## The iterations

### Iteration 1 — Stop the bleeding (safety P0 + two visible wins)

| Ticket | Size | Why now |
|---|---|---|
| #297 Secrets in logs and error responses | S | P0, REDIS_PASSWORD logged at every api start |
| #287 DATABASE_URL on the docker command line | S | P0, same family; rotate after both |
| #343 Prove the Postgres backup (restore drill) | S | P0, an untested backup is not a backup |
| #319 Atomic /trade/submit and close dedup (EX-01) | M | P0, a retry can place a second live close |
| #344 Healthchecks that assert real state | S | `/health` constant hides Postgres down |
| #365 T1 CPU weights; retire duplicate PCA | S | visible, half a day, no dependency |
| #369 T5 Never blank a panel | M | visible, closes the $100k-mock class of failure |

7.5 days. Exit: no secret in any log line; a restore has been run and logged;
a timed-out close cannot be retried into a duplicate; `docker stats` shows
analytics yielding under load; killing the api blanks no panel.

### Iteration 2 — Bounded action I (correct numbers, safe submits)

| Ticket | Size | Why now |
|---|---|---|
| #310 Live IV lookup falls back to 8 %; delta hedge can never fire | M | P0, every live greek depends on it |
| #318 Every IB submit path idempotent | M | P0, hedge / rollback / close / manual lack the claim |
| #320 Order state machine hardening | S | unknown statuses silently dropped |
| #322 Fill journal correctness | S | concurrent fills overwrite aggregates |
| #338 Test harness foundations | M | enabler for every fix after this |

7.5 days. Exit: one IV accessor against the current schema; every placement
goes through `place_with_claim()`; `conftest.py` with FK-on DB fixture,
frozen clock and payload fixtures exists.

### Iteration 3 — Bounded action II (limits, kill switch, alerting)

| Ticket | Size | Why now |
|---|---|---|
| #315 Enforce greek limits fail-closed, kill switch, paper assertion | L | P0, the system is fail-open on every risk axis |
| #313 Units: aggregate greeks multiplier, vanna, EUR vs USD delta | S | the numbers the gate will read |
| #342 Arm alerting (crash-loop, disk, IB down, db-writer lag, backup age) | M | P0, nothing pages anyone |
| #317 Test for the position_sync delete guard | XS | filler |

7 days. Exit: a trade that breaches a cap is refused with the reason; the
halt flag blocks submission and cancels working orders; an alert reaches you
when an engine dies.

### Iteration 4 — Session resilience and the push loop

| Ticket | Size | Why now |
|---|---|---|
| #321 IB session resilience: 1100/1102, re-attach handlers, replay, ready gate | L | P0, handlers die on every nightly restart |
| #366 T2 Execution publishes position and account deltas | S | two helpers already exist, nobody calls them |
| #367 T3 Desk subscribes over WebSocket | M | the change the operator feels most |

6.75 days. Exit: after the 02:00 gateway restart, open orders still report
fills; a fill reaches the Open positions table in under 2 s.

### Iteration 5 — Cold path off the request path

| Ticket | Size | Why now |
|---|---|---|
| #368 T4 Materialise rollups and attribution (closes #286) | L | the last cold scans in the request path |
| #330 History retention: no cascade on close, prune jobs resilient | S | two Blockers from the data audit |
| #324 db-writer delivery semantics | M | real dedup keys, dead-letter, overflow metric |

6.75 days. Exit: every portfolio endpoint under 100 ms on prod; closing a
position keeps its history; a poison frame loses one row, not a batch.

### Iteration 6 — Measure, then consolidate pricing

| Ticket | Size | Why now |
|---|---|---|
| #370 T6 Latency metrics and the Desk fluidity panel | M | from here on every change is measured |
| #331 One Black-Scholes kernel and one tenor map in core | L | three pricers today; removes the lint exception |
| #309 Pricing edge cases (T<=0, sigma<=0, F guard) + FD tests | M | on the single kernel |

8 days. Exit: Grafana shows click-to-ack and fill-to-screen p95; one pricer,
equivalence test across the three former call sites; `test_bs.py:56` flipped.

### Iteration 7 — Data integrity

| Ticket | Size | Why now |
|---|---|---|
| #325 Schema drift gate in CI + missing uniques | M | migrations verified against models |
| #328 Staleness contract on vol read paths | M | `as_of` / `source` / `stale` everywhere |
| #327 Redis contract cleanup | M | one source of key names, timestamps on last values |
| #326 Numeric for money; no naive datetimes | S | |

6.75 days. Exit: `alembic check` green in CI; every vol payload carries its
age; grep finds no literal channel name outside `bus/`.

### Iteration 8 — Tests and truth

| Ticket | Size | Why now |
|---|---|---|
| #339 Critical-path tests (fail-closed, reconciliation, writer, WS bridge) | L | the safety net for iterations 2 to 5 |
| #347 Frontend data truthfulness | M | server timestamps, ageing clock, WS state |
| #316 VaR robustness and documentation | M | after limits exist |

8 days. Exit: the seven defect-pinning tests are flipped; a badge turns stale
on its own; VaR payloads name what they dropped.

### Iteration 9 — Security hardening

| Ticket | Size | Why now |
|---|---|---|
| #300 Tighten the auth boundary (execution /internal/*, typed bodies) | M | |
| #298 Container hardening (non-root, cadvisor, promtail socket) | M | also the obs waive-or-fix decision |
| #296 Rate-limit keying behind nginx + lockout | S | |
| #299 WebSocket Origin check and auth decision | S | |
| #302 IAM and workflow least privilege, SHA-pinned actions | S | |
| #301 nginx hardening | XS | |

6.5 days. Exit: audit 01 epic shows every item applied or waived.

### Iteration 10 — Config convergence and the capacity dial

| Ticket | Size | Why now |
|---|---|---|
| #329 VolConfig hot-reload convergence | M | prerequisite of the dial |
| #333 Single env-var schema | M | 51 raw env reads, two names for one URL |
| #371 T7 Capacity dial in Settings | M | your runtime brake, now safe to add |
| #373 T9 Write-side hygiene | S | |
| #372 T8 Chain-subscription knob | XS | |
| #334 Retire VOL_INLINE_ANALYTICS, stale references | XS | |

7.25 days. Exit: a Settings change reaches the analytics engine within one
cycle without a restart; history row growth halved.

### Iteration 11 — Quant hardening and code hygiene

| Ticket | Size | Why now |
|---|---|---|
| #311 SVI/SSVI static-arbitrage hardening | M | |
| #312 Yang-Zhang / HAR / GARCH input sanitising | S | |
| #314 Regime and signal threshold alignment | S | |
| #332 Core deterministic and isolated | S | |
| #336 Logging unified on structlog | S | |
| #335 mypy gated per package | S | |
| #337 Engine error policy documented, tasks supervised | XS | |

6 days. Exit: audit 02 and 06 epics mostly applied.

### Iteration 12 — Supply chain

| Ticket | Size | Why now |
|---|---|---|
| #303 Python lockfile + fastapi/starlette upgrade | M | |
| #306 ib-insync to ib_async decision | M | blocks v1.0 per the release gate |
| #304 Weekly dependency scan | S | |
| #305 Pin base images, move off Node 20 | S | |
| #308 Frontend toolchain upgrade | M | |

7.5 days. Exit: CI and Docker install the same resolved set; the scan job
runs weekly; a decision on ib_async is recorded.

### Iteration 13 — Ops and frontend resilience

| Ticket | Size | Why now |
|---|---|---|
| #345 Deploy pipeline safety (rollback with schema, prune doc) | S | |
| #346 Ops runbooks and inventory | S | after the restore drill (#343) |
| #348 Frontend contract and resilience | M | |
| #349 Frontend hygiene (source maps, a11y, ES label) | S | |
| #340 Coverage gates per package | S | |
| #341 Real e2e round trip; paper-e2e skeletons | M | |
| #323 Alert on reconciliation breaks | XS | |

7.25 days. Exit: a rollback has been exercised; the compose e2e job hits the
real stack.

### Iteration 14 — Docs and release

| Ticket | Size | Why now |
|---|---|---|
| #350 Docs accuracy pass (12 containers / 6 engines / 8 images) | S | written against the fixed system |
| #351 Onboarding: `.env.example`, prerequisites, Known limitations | S | |
| #352 API reference (OpenAPI + WS channels) | M | |
| #307 Data-terms decisions and attribution | S | business decisions recorded |
| #353 Release mechanics (version, CHANGELOG, tag procedure) | M | last |

6.25 days. Exit: every gate in `RELEASE-GATE-v1.0.md` is green or waived in
its epic; v1.0 can be tagged.

### Iteration 15 — Decide on hardware

| Ticket | Size | Condition |
|---|---|---|
| #374 T10 Split hosts | M | only if the fluidity panel (#370) still shows p95 above 2 s during PCA cycles |

## Filler, runs alongside any iteration

| Ticket | Size | Notes |
|---|---|---|
| #295 Diagram-first rewrite of docs/ | L | one folder per PR; pick a folder whenever a planned ticket is blocked |

## Epics

The twelve epics (#354 to #364 audits, #375 hot / cold) are containers: they
close when their checklists are ticked. They are never "worked on" directly
and stay out of the Ready column.

## Review cadence

- End of each iteration (every second Friday): tick epics, update problem
  statuses, move leftovers, commit this file.
- After iteration 6: re-plan iterations 7 to 15 against the fluidity panel's
  numbers.
- After iteration 14: walk `RELEASE-GATE-v1.0.md` gate by gate.
