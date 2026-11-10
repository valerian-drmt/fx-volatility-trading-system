# Iteration plan

Operational plan for the open backlog: which tickets to take, in which order,
and why. Built on 2026-11-05 from the 83 open issues on project board #5
(12 epics, 71 work tickets). Revised on 2026-11-06: four defects seen while
using the desk (#376 to #379) were added as a new iteration 2 and the later
iterations renumbered. Status lives on GitHub; this file holds the ordering
and the reasoning, and is revised at the end of every iteration.

- Board: https://github.com/users/valerian-drmt/projects/5
- Sources: the audit campaign (`docs/audits/`, label `audit`), the hot / cold
  path programme (`docs/hot-cold-path/`, label `hot-cold-path`), three
  standalone tickets (#286, #287, #295), and the desk defects of 2026-11-06
  (#376 spot order stuck, #377 cross-table figures, #378 Logs tab 503,
  #379 Strike Δ chooser).

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
| XS | 0.25 | 7 | 1.75 |
| S | 0.75 | 30 | 22.5 |
| M | 2 | 32 | 64 |
| L | 4 | 6 | 24 |
| **Total work tickets** | | **75** | **~112** |

One engineer plus Claude, about 3.5 focused days per week, so an iteration is
**two weeks with about 7 engineering days**. The PR drip (2 to 3 per day) is
never the limit: an iteration ships 6 to 10 PRs. Sixteen iterations cover the
backlog; the v1.0 release gate (`docs/audits/reports/RELEASE-GATE-v1.0.md`)
can close after iteration 15 if nothing new is found.

## Ordering rules

1. **Safety before everything**: the eight P0 tickets (bounded action, secrets,
   backup, alerting) go first. The campaign summary's theme B.
2. **Enablers before the work they enable**: the test harness (#338) before the
   critical-path tests (#339); the IV lookup fix (#310) before limit
   enforcement (#315); execution publishing (#366) before the desk subscribing
   (#367); latency metrics (#370) before any hardware decision (#374); the
   cross-table reconciliation (#377) before the materialisation it feeds
   (#368).
3. **Visible wins early, defects seen in use right after the P0s**: CPU
   weights (#365) costs half a day and changes how the desk feels on day one.
   A defect the operator hits every day (#376 to #379) outranks a planned
   improvement: it is a broken promise, and it is fresh, so it is cheap to
   diagnose now. They form iteration 2 together with never-blank panels
   (#369).
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
#377 cross-table figures ──▶ #368 T4 materialise ──▶ #373 T9 write-side ; #368 closes #286
#379 pillar strikes (one live-anchored function) ──▶ #347 frontend truthfulness (removes DATA.SPOT)
#376 spot order stuck ──▶ #320 order state machine (the stuck state becomes a tested transition)
#343 restore drill ──▶ #346 runbooks
#331 pricing into core ──▶ #309 edge cases (same kernel)
#297, #287 secrets ──▶ password rotations (user action, recorded in #297)
```

## Status on 2026-11-10

Iterations 10 to 16 shipped to prod between 2026-11-10 10:00 and 17:00 UTC
(PRs #395, #401, #402, #405, #417, #418, #419). What is left of them:

| Item | Waiting on |
|---|---|
| Rollback drill, restore drill, IB paper smoke, runbook walks (#345, #346, #341) | the owner, weekend window (`docs/ops/runbooks.md`) |
| IB redistribution and FRED storage decisions (#307) | the owner (`docs/ops/data-terms.md`) |
| Response models for the remaining untyped routes (#416) | engineering, slice by slice against recorded payloads |
| Obs image upgrade (#415), ib_async migration (#403, v1.1), React Compiler lint rules (#404) | engineering |
| Host split (#374) | closed: not needed on the 2026-11-10 numbers; reopen rule on the ticket |
| Re-verification of the 12 audit epics against `main` | one pass before tagging v1.0.0 |

## The iterations

### Iteration 1 — Stop the bleeding (safety P0 + one visible win)

| Ticket | Size | Why now |
|---|---|---|
| #297 Secrets in logs and error responses | S | P0, REDIS_PASSWORD logged at every api start |
| #287 DATABASE_URL on the docker command line | S | P0, same family; rotate after both |
| #343 Prove the Postgres backup (restore drill) | S | P0, an untested backup is not a backup |
| #319 Atomic /trade/submit and close dedup (EX-01) | M | P0, a retry can place a second live close |
| #344 Healthchecks that assert real state | S | `/health` constant hides Postgres down |
| #365 T1 CPU weights; retire duplicate PCA | S | visible, half a day, no dependency |
| #310 Live IV lookup falls back to 8 %; delta hedge can never fire | M | P0, every live greek depends on it; pulled forward when #369 moved to iteration 2 |

7.5 days. Exit: no secret in any log line; a restore has been run and logged;
a timed-out close cannot be retried into a duplicate; `docker stats` shows
analytics yielding under load; one IV accessor against the current schema.

### Iteration 2 — Desk defects seen in use (2026-11-06)

| Ticket | Size | Why now |
|---|---|---|
| #376 Spot order never fills; `S<id>` row stuck in a waiting state | M | P1, the only spot ticket the desk has is broken; cause unknown, triage path in the issue |
| #377 Cross-table verification: Position breakdown vs attribution by tenor vs by trade | M | three panels, three tables, no footing test; must land before #368 materialises the wrong source |
| #379 Strike Δ button and the strike ladder disagree (live vs mock pillar strikes) | S | the trader picks 25Δ and sees the leg under 10Δ; one shared function fixes every product |
| #378 Dev Logs tab shows a raw 503 instead of the Loki-down state; check Loki on prod | XS | dev tooling, but also a signal that Loki may be down on prod |
| #369 T5 Never blank a panel | M | moved from iteration 1; same theme, the desk shows true values or says why not |

7 days. Exit: a paper spot send reaches `filled` in the blotter without a
reload; the three per-leg panels show the same number for the same leg and
`as_of` (or label why not), with the invariant tested; every product's Strike
Δ button and ladder column agree at live spot; the Logs tab says "Loki down"
with a hint, and Loki's state on prod is known; killing the api blanks no
panel.

### Iteration 3 — Bounded action I (safe submits, test harness)

| Ticket | Size | Why now |
|---|---|---|
| #318 Every IB submit path idempotent | M | P0, hedge / rollback / close / manual lack the claim |
| #320 Order state machine hardening | S | unknown statuses silently dropped; absorbs the stuck state found by #376 |
| #322 Fill journal correctness | S | concurrent fills overwrite aggregates |
| #338 Test harness foundations | M | enabler for every fix after this |

5.5 days, on purpose: the slack absorbs leftovers from iterations 1 and 2
(defects found in use tend to grow once opened). Exit: every placement goes
through `place_with_claim()`; `conftest.py` with FK-on DB fixture, frozen
clock and payload fixtures exists.

### Iteration 4 — Bounded action II (limits, kill switch, alerting)

| Ticket | Size | Why now |
|---|---|---|
| #315 Enforce greek limits fail-closed, kill switch, paper assertion | L | P0, the system is fail-open on every risk axis |
| #313 Units: aggregate greeks multiplier, vanna, EUR vs USD delta | S | the numbers the gate will read |
| #342 Arm alerting (crash-loop, disk, IB down, db-writer lag, backup age) | M | P0, nothing pages anyone |
| #317 Test for the position_sync delete guard | XS | filler |

7 days. Exit: a trade that breaches a cap is refused with the reason; the
halt flag blocks submission and cancels working orders; an alert reaches you
when an engine dies.

### Iteration 5 — Session resilience and the push loop

| Ticket | Size | Why now |
|---|---|---|
| #321 IB session resilience: 1100/1102, re-attach handlers, replay, ready gate | L | P0, handlers die on every nightly restart |
| #366 T2 Execution publishes position and account deltas | S | two helpers already exist, nobody calls them |
| #367 T3 Desk subscribes over WebSocket | M | the change the operator feels most |

6.75 days. Exit: after the 02:00 gateway restart, open orders still report
fills; a fill reaches the Open positions table in under 2 s.

### Iteration 6 — Cold path off the request path

| Ticket | Size | Why now |
|---|---|---|
| #368 T4 Materialise rollups and attribution (closes #286) | L | the last cold scans in the request path; built on the source agreed in #377 |
| #330 History retention: no cascade on close, prune jobs resilient | S | two Blockers from the data audit |
| #324 db-writer delivery semantics | M | real dedup keys, dead-letter, overflow metric |

6.75 days. Exit: every portfolio endpoint under 100 ms on prod; closing a
position keeps its history; a poison frame loses one row, not a batch.

### Iteration 7 — Measure, then consolidate pricing

| Ticket | Size | Why now |
|---|---|---|
| #370 T6 Latency metrics and the Desk fluidity panel | M | from here on every change is measured |
| #331 One Black-Scholes kernel and one tenor map in core | L | three pricers today; removes the lint exception |
| #309 Pricing edge cases (T<=0, sigma<=0, F guard) + FD tests | M | on the single kernel |

8 days. Exit: Grafana shows click-to-ack and fill-to-screen p95; one pricer,
equivalence test across the three former call sites; `test_bs.py:56` flipped.

### Iteration 8 — Data integrity

| Ticket | Size | Why now |
|---|---|---|
| #325 Schema drift gate in CI + missing uniques | M | migrations verified against models |
| #328 Staleness contract on vol read paths | M | `as_of` / `source` / `stale` everywhere |
| #327 Redis contract cleanup | M | one source of key names, timestamps on last values |
| #326 Numeric for money; no naive datetimes | S | |

6.75 days. Exit: `alembic check` green in CI; every vol payload carries its
age; grep finds no literal channel name outside `bus/`.

### Iteration 9 — Tests and truth

| Ticket | Size | Why now |
|---|---|---|
| #339 Critical-path tests (fail-closed, reconciliation, writer, WS bridge) | L | the safety net for iterations 3 to 6 |
| #347 Frontend data truthfulness | M | server timestamps, ageing clock, WS state; inherits the live-anchored pillar function of #379 |
| #316 VaR robustness and documentation | M | after limits exist |

8 days. Exit: the seven defect-pinning tests are flipped; a badge turns stale
on its own; VaR payloads name what they dropped.

### Iteration 10 — Security hardening

| Ticket | Size | Why now |
|---|---|---|
| #300 Tighten the auth boundary (execution /internal/*, typed bodies) | M | |
| #298 Container hardening (non-root, cadvisor, promtail socket) | M | also the obs waive-or-fix decision |
| #296 Rate-limit keying behind nginx + lockout | S | |
| #299 WebSocket Origin check and auth decision | S | |
| #302 IAM and workflow least privilege, SHA-pinned actions | S | |
| #301 nginx hardening | XS | |

6.5 days. Exit: audit 01 epic shows every item applied or waived.

### Iteration 11 — Config convergence and the capacity dial

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

### Iteration 12 — Quant hardening and code hygiene

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

### Iteration 13 — Supply chain

| Ticket | Size | Why now |
|---|---|---|
| #303 Python lockfile + fastapi/starlette upgrade | M | |
| #306 ib-insync to ib_async decision | M | blocks v1.0 per the release gate |
| #304 Weekly dependency scan | S | |
| #305 Pin base images, move off Node 20 | S | |
| #308 Frontend toolchain upgrade | M | |

7.5 days. Exit: CI and Docker install the same resolved set; the scan job
runs weekly; a decision on ib_async is recorded.

### Iteration 14 — Ops and frontend resilience

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

### Iteration 15 — Docs and release

| Ticket | Size | Why now |
|---|---|---|
| #350 Docs accuracy pass (12 containers / 6 engines / 8 images) | S | written against the fixed system |
| #351 Onboarding: `.env.example`, prerequisites, Known limitations | S | |
| #352 API reference (OpenAPI + WS channels) | M | |
| #307 Data-terms decisions and attribution | S | business decisions recorded |
| #353 Release mechanics (version, CHANGELOG, tag procedure) | M | last |

6.25 days. Exit: every gate in `RELEASE-GATE-v1.0.md` is green or waived in
its epic; v1.0 can be tagged.

### Iteration 16 — Decide on hardware

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
- After iteration 7: re-plan iterations 8 to 16 against the fluidity panel's
  numbers.
- After iteration 15: walk `RELEASE-GATE-v1.0.md` gate by gate.
- Defects found while using the desk get a ticket the same day and go into
  the next iteration that has not started, ahead of planned improvements
  (rule 3); if that iteration is full, its lowest-ranked ticket moves down.
