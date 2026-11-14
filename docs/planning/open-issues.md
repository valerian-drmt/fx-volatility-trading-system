# Open issues: what is left and how each one closes

State of the 24 open GitHub issues on 2026-11-14, checked against the code at
`4e061f51` (not against the ticket comments, several of which were out of
date). The analysis of the order path itself is in
`docs/audits/reports/order-lifecycle/`; this file only says what remains and
what closes each ticket.

## Where it stands (2026-11-14, end of day)

Fourteen of the fifteen code tickets are done on local `main` and close when
their commit is merged (`git update`). Nothing is deployed yet.

| Ticket | State | Commit |
|---|---|---|
| #482 package hedge | closed | shipped earlier |
| #489 one writer | done | `afebab18` |
| #485 end of a DAY order | done | `afebab18` |
| #486 close guard | done | `afebab18` |
| #490 placement owner | done, with two parts declined on the owner rule (below) | `afebab18` |
| #493 dormant features | done: both deleted | `6f7806a9` |
| #491 one door | done | `22787c66` |
| #492 close path | done | `469889ca` |
| #483 preview | done | `43b4cf81` |
| #479 ticket numbers | done | `43b4cf81` |
| #481 limit framework | superseded, remnants removed, doc rewritten | `43b4cf81` |
| #497 idempotency keys | done | `80d4c45e` |
| #495 observability | done; the alert firing tests run for the first time in CI | `d5d3c317` |
| #496 simulated broker | done: 14 scenarios | `159b635a` |
| #494 rows on prod | done: migration 073 | this commit |
| #307 data terms | D2 (FRED out of Postgres) done, `8020e648`; **D1 open**: owner decision on IB quotes for visitors | |
| #341, #345, #346 | **open**: operator on the host | |
| #498, #360, #361, #363, #364 | epics: close with their last child | |

Found on the way and fixed:

- The preview's premium, maximum loss and cost were those of ONE contract,
  whatever the size, and the funds check used that figure (#483).
- The api could write "submitted" over a fill on a close (#489).
- Automatic exit could not work at all: the engine refused its orders (#493).
- Two closes of 2026-11-04 were recorded "rejected" and had filled (#494).
- The disk alert had been firing for three days on a disk 64 % full.

What is not proven:

- Nothing here was run against IB. The simulated broker speaks the library's
  objects, not the broker's behaviour.
- The alert firing tests (`obs/alerts_test.yml`) have not been executed:
  no `promtool` on this machine. Their first run is the CI of the merge.
- Migrations 071, 072 and 073 ran on SQLite in tests, not on Postgres.
- No screen was looked at: the ticket's new number source is tested as a
  function, not as a rendered page.

Two owner rules decide several tickets:

- **The app is not the trader** (2026-11-13). No limit, reference level, size
  or confirmation computed by the code. The app refuses on facts (market,
  account, broker) and on limits the trader typed. It does not trade on its
  own either: no automatic exit, no automatic unwind.
- **A trade is its options; a future is one line per contract** (2026-11-13).

## Who closes what

| Kind | Tickets |
|---|---|
| Code, done by Claude | #479 #481 #483 #485 #486 #489 #490 #491 #492 #493 #494 #495 #496 #497 |
| Already shipped, closes on review | #482 |
| Needs the operator on the host (weekend window) | #341 #345 #346 |
| Needs one owner decision | #307 (D1 only) |
| Epics, close with their last child | #498 #360 #361 #363 #364 |

## Order path (epic #498)

### #479 Ticket shows the server's numbers
Left: the legs table (strike, vol, premium per leg) and the hedge commission
still come from the browser model once a preview exists; delta, gamma and
vega fall back to the browser model when the budget is missing; no test
renders the ticket.
Closes with: legs and commission read from the preview; no browser fallback
beside a preview id (a missing number shows as missing); a rendered test that
fails if a browser greek appears next to a preview id.

### #481 Limit framework
Superseded by the owner rule. What the ticket asked (limits derived from
capital, warning level, confirmation, proposed size) was built, then removed
on the owner's instruction.
Left: dead remnants (`confirm_limits`, `limit_warn_fraction`,
`needs_confirmation`, `DEFAULT_MAX_ORDER_CONTRACTS`, the `limits_confirmed`
event) and `docs/strategy/risk.md`, which still describes capital-derived
caps enforced by the gate.
Closes with: remnants removed, `risk.md` rewritten to what the gate does
(facts, plus the trader's own `hard_max_*` limits, off until set).

### #482 Structure and hedge are one package
Shipped (PR #505, #507, #509) and used on prod on 2026-11-13 (option at
14:16:39 UTC, its hedge at 14:16:40). Every acceptance line holds. Known
limit, kept in `docs/execution/order-lifecycle.md`: on a multi-leg structure
filled unevenly the hedged share is counted in contracts.
Closes now.

### #483 Preview prices each leg correctly
Found in the code:
- `state` can read `valid_for_submit` while `submit_blockers` is not empty.
- The no-arbitrage check is `has_arb_violation=False` and the liquidity
  check is `min_quoted_size=99`: constants. The gate's own arbitrage flag
  (`surface_meta.butterfly_ok`) is never written by anything, so it always
  passes too.
- The vega check and the premium use the figure of ONE unit of the
  structure, not the size (`final_qty / base_qty` is always 1 in manual
  mode). The funds check therefore compares one unit's premium with the
  account.
- Commission: 2.1 in the browser, 2.0 / 2.40 / 0.60 on the server.
- A "3M" leg is priced on 90 days whatever the listed expiry.
Closes with: one verdict (`valid_for_submit` only when nothing blocks); the
surface's arbitrage flags carried into the preview and read by the gate; a
check that cannot be evaluated reported as not evaluated; premium and vega at
the size; one commission table on the server, shown by the ticket; the leg
priced to the expiry the contract will have.

### #485 Reaper and the end of a DAY order
Left: an order IB ends at the close of the day is `cancelled` from the status
handler and `expired` from the reaper; the "never placed" branch writes
without the lock.
Closes with: one rule in `end_order`: an order that ends unfilled without a
cancel request of ours is `expired`, whoever sees it first; every branch of
the reaper under the lock.

### #486 Close guard
Left: the guard keys on side, expiry, strike and kind (a 6E and an M6E future
of the same month share one budget); the close row is created without the
contract's symbol.
Closes with: the close row carries the position's IB symbol from creation and
the guard keys on it.

### #489 One writer for an order's state
Found in the code:
- The api overwrites a fill: after the engine answers, it writes
  `state = "submitted"` on a close without re-reading the row, while the fill
  handler is already live.
- Two api paths reject a close without releasing the reservation.
- The combo path and the reconciler write an ended state through a variable,
  which the test's pattern cannot see; the test's list is stale and passes
  because it only checks a subset.
- The lock is a dictionary in the engine's memory: the api cannot take it.
- `acknowledged` and `partial_fill` are still allowed and listed, never
  written.
Closes with: every ended state through `end_order`; `mark_submitted` as the
one function for the pending to submitted edge, which never moves a row
backwards; a database row lock (`SELECT ... FOR UPDATE`) inside both, so the
api and the engine exclude each other; the two dead states removed from the
lists and the constraints; a test that fails on any direct assignment.

### #490 Placement has an owner
The four gaps of the audit have owners (reaper, reconciler, partial-failure
cancel, executions recovery). Two parts of the original design are declined,
on the owner rule:
- **No late placement.** A worker that places an order minutes after the
  click sends a stale price the trader no longer watches. A row that did not
  leave is ended and its reservation freed; the trader sends it again.
- **No automatic unwind of a filled leg.** Selling a leg the trader bought
  is a trade. A partial placement cancels what is still working, raises a
  critical event and shows on the position line; the trader decides.
Left: on the per-leg path the fill handlers are attached after an `await`
that follows `placeOrder`.
Closes with: handlers attached before anything can yield; the two decisions
written in `docs/execution/order-lifecycle.md`.

### #491 One door
Left: the kill switch is a dependency of six engine routes; the package
hedge, its retry loop and the per-leg submit place without reading it. The
"caps" the ticket wanted in the engine no longer exist.
Closes with: `engines/execution/door.py`, one function every placement calls
(kill switch, session ready, paper account); a test that fails when a file
calls `placeOrder` without it.

### #492 Close path
Left: an operator limit and the outside-hours future limit are rounded, not
snapped to the tick. The half-closed flag exists (`legs open / total` on the
line).
Closes with: one tick table in `core`, every limit snapped through it.

### #493 Dormant features
Decision (owner rule): **delete both**. Automatic exit and the monitor's
hedges are the app trading on its own. Found on the way: automatic exit
cannot work at all (its closing rows are refused by the engine as "no entry
orders" and the position stays `closing`), and the monitor writes a
`hedge_order` row every cycle that nothing sends.
Closes with: the flag, the dispatch, the `/hedge` route, the monitor's hedge
rows and `close-manual` removed; exit alerts stay as alerts.

### #494 Order rows left on prod
Migration 069 ran. The three `partial_fail` trades, read on prod on
2026-11-14: none is a multi-leg order with a leg out alone. Each is a ticket
of one close written on 2026-11-04 by the api's old failure path.

| Trade | Order | What the row says | What happened |
|---|---|---|---|
| 81 | 102, SELL 89 EUUF7 C1150 | `rejected`, "execution-engine unreachable" | filled 89 of 89 |
| 82 | 103, BUY 100 EUUF7 C1180 | `rejected`, same | filled 100 of 100 |
| 78 | 97, SELL 10 | `cancelled` | nothing filled; the leg it closed still shows 10 reserved |

The fills are booked and the legs flat: the book is right, the labels are
wrong. Migration 073 relabels an order whose fill rows cover its quantity as
`filled`, gives a `partial_fail` trade the state its orders say, and
recomputes every reservation. Rules, not ids; idempotent.

### #495 Observability of the order path
Missing: refusals by reason, orders waiting by state and age, firing tests,
a panel, a runbook section.
Closes with: `order_refusals_total{reason}`, `orders_waiting{state}` and
`orders_oldest_waiting_seconds`; alerts `OrderStuck` and
`GateRefusingEverything`; `promtool test rules` in CI for those and
`ReconciliationBreakOpen`; a Grafana panel; "an order is stuck" in
`docs/ops/runbooks.md`.

### #496 Tests against a simulated broker
`tests/fixtures/ib.py` records calls and never fills.
Closes with: a simulator (status sequences, partial and full fills,
executions, cancel, IB-held orders, disconnect) and one scenario test per
case S2 to S8 of the audit, each ending on "the book equals the broker".

### #497 Idempotency keys
Only the structured submit is protected.
Closes with: an `Idempotency-Key` header on close, trade close, spot, manual
ticket and cancel; the first answer stored and returned on a repeat; the
browser sends one key per click.

## Audit campaign

### #341 Paper end-to-end
Left: run the read-only IB paper suite once and record it. Needs the prod
gateway stopped (one IB session per user).
Operator: `docs/ops/runbooks.md` 4.5, then paste the result line in
`tests/STRUCTURE.md`.

### #345 Deploy pipeline safety
Left: the rollback drill and one run of `setup.sh` on the host for
unattended upgrades. Operator: `docs/ops/runbooks.md` section 2.

### #346 Ops runbooks
Left: walk the four procedures once (reboot, rollback, restore, IB
recovery). Operator, same weekend window as #345.

### #307 Data terms
- D2 (FRED storage): the terms forbid keeping FRED data in a database.
  Closes with: FRED rows in a 24 h Redis cache, none in Postgres, existing
  rows deleted by a migration.
- D1 (IB quotes on the public desk): owner decision, because it changes what
  a visitor sees. Recommendation: raw quotes (`/ws/ticks`, `/bars`, the top
  bar) behind the trader login, derived analytics public.

### Epics #360 #361 #363 #364
Each closes with its last child: #360 with #341, #361 with #345 and #346,
#363 with #307, #364 with #346.

## Order of work

1. #489, #485, #486 (the state of an order: everything else rests on it).
2. #491, #490, #492, #493 (who may place, and what is removed).
3. #483, #479, #481 (what the ticket shows).
4. #497, #495, #496 (protection, observation, proof).
5. #494, #307 D2.
