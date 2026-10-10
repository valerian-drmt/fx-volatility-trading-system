# Audit 4 — Execution Safety

## Why

Execution is where software bugs become market orders. The classic failure modes —
duplicate submission after a retry, orphaned working orders after a crash, position
drift between internal state and the broker — are all *stateful* bugs that unit tests
rarely catch. The system runs 24/5 unattended against a live IB paper session, so every
reconnect/restart path will be exercised in production.

## Scope

- `src/engines/execution/` — `order_executor`, `position_sync`, `hedge_executor`,
  `live_submit`, `fills_handler`, `structures`.
- `src/shared/ib_connection.py` — sync wrapper + backoff (shared failure semantics).
- Order/trade persistence tables + their writer paths.
- `/orders`, `/trade`, `/trades`, `/cockpit` routers (the API-to-execution seam).

## What to check

### Idempotency & duplicates
- [ ] Order submission path: what guarantees exactly-once handoff to IB? Client order id
      strategy, dedup on retry, behavior when IB ACK is lost but the order was placed.
- [ ] API-level: double-click / double-POST on `/trade` — is there an idempotency key?
- [ ] Hedge executor: can a re-evaluation loop fire the same hedge twice under latency?

### Order lifecycle & reconciliation
- [ ] Full state machine mapped: submitted → acked → partial → filled/cancelled/rejected;
      every IB status handled, unknown statuses logged not dropped.
- [ ] Startup reconciliation: on engine restart, open orders and positions at IB are
      fetched and reconciled *before* any new order can be submitted.
- [ ] `position_sync` conflict policy: IB says X, DB says Y — who wins, and is the
      discrepancy alerted?
- [ ] Fills: partial fills accumulate correctly; out-of-order fill events handled;
      commissions/currency captured.

### Session & connectivity failures
- [ ] clientID discipline (execution = 5): reconnect storm behavior, backoff caps,
      behavior on error 1100/1102 (connectivity lost/restored) and order status gap
      replay after reconnect.
- [ ] Gateway nightly restart (~02:00 Paris): in-flight orders during the restart window.
- [ ] Fail-safe: if the risk engine is unreachable/stale (see Audit 3), does execution
      refuse new orders?

### Safety rails
- [ ] Paper-vs-live guard: what structurally prevents this stack from ever pointing at a
      live account (port/account-id assertion at connect)?
- [ ] Order sanity checks before submit: max size, max notional, price collar vs last
      market data, allowed symbols/tenors only.
- [ ] Kill switch: a documented, tested way to halt all submission and cancel working
      orders.

## Method

State-machine reconstruction from code, then failure-injection walkthroughs on the local
stack (kill the engine mid-order, drop the IB connection, replay a duplicate fill event).
Live-path checks only against paper, run by Valérian.

## Deliverable & exit criteria

- Findings report + order-lifecycle state diagram committed to `docs/execution/`.
- **Exit:** restart-reconciliation verified; no path found that submits without risk
  check + sanity rails; duplicate-submission scenarios all rejected; zero Blocker/High.