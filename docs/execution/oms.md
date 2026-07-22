# Order management (OMS)

The OMS books what the desk actually holds and keeps it reconciled against the
IB mirror. Its truth model is **free legs**: a position is a pure fold of its own
fills, never reconstructed from IB's netted view. The pure invariant math lives in
[`src/core/execution/`](../../src/core/execution/); the engine that drives IB and
persists state is [`src/engines/execution/`](../../src/engines/execution/).

## The free-legs model

A structure is a set of legs; each leg's position is a **signed fold of that
leg's executions and nothing else**. The link execution → order → leg is known at
fill time (the `order_id` rides on the IB event), so it is never back-attributed
from the netted mirror.

[`core/execution/projection.py`](../../src/core/execution/projection.py)
`fold_fills(fills) -> LegFold`:

```
open_qty  = Σ signed(side, qty)      # +buy / −sell
avg_price = Σ(qty·price) / Σ qty     # vwap, None when no fills
filled_qty= Σ qty                    # ties back to order.qty_filled (I1)
```

Because the fold takes *only* fills, the mirror can never leak in as an
attribution input (invariant I7), and destroying + replaying the fills reproduces
the position exactly (T8). `persistence.projection` wraps it to read `trade_fill`
and materialise `leg_position`; `rebuild_leg` is the single writer.

## Structure submit

`api.routers.trade.submit_preview` persists `trade_structures` (state
`submitted`) + `structure_orders` (state `pending`, `limit_price` precomputed),
then POSTs `execution-engine:8001/internal/structure/submit`. The engine reads the
pending legs and places them — combo (BAG) or per-leg — see
[order-lifecycle.md](order-lifecycle.md).

## Reservation (anti-over-close)

[`core/execution/reservation.py`](../../src/core/execution/reservation.py) guards
against two fast close-clicks each closing the full open qty. Closing qty is
*reserved* on the leg:

```
available    = |open_qty| − reserved_qty        # must stay ≥ 0
try_reserve(open_qty, reserved_qty, requested)  # raises OverReserveError if > available
```

This is the O(1), race-free, restart-safe form of a stateless re-sum guard
(invariant I5). `persistence.reservation` folds it onto `leg_position`; a closing
fill releases the entry leg's reservation (`recompute_reservation`).

## Position sync vs the IB mirror

[`engines/execution/position_sync.py`](../../src/engines/execution/position_sync.py)
runs at api startup and on a periodic loop (default 30s):

- **`sync_positions_from_ib`** upserts IB → DB `open_position`, matched on the
  tuple `(symbol, instrument_type, strike, maturity, option_type)` — no con_id
  column needed.
- **`publish_portfolio_to_redis`** publishes IB-canonical marks for every open
  position on Redis hashes (`contract_marks` / `option_marks` / `unrealized_pnl`);
  the risk-engine is the sole writer of `position_snapshots`.

A guard (`RECONCILE_AUTOCLOSE`, on by default) auto-closes a booked position IB has
shown flat for over an hour, with an audit trail — but never fires on an empty or
disconnected IB snapshot.

## Reconciliation

The book (Σ `leg_position.open_qty` per contract — our forward truth) and the
broker mirror (`open_position` net per contract — the checksum) must agree. Any
gap is a **break**, materialised as data rather than a silent discrepancy.
[`core/execution/reconciliation.py`](../../src/core/execution/reconciliation.py)
is the pure classify:

| `break_type` | Condition |
|---|---|
| `missing_at_ib` | book holds it, IB flat (fill lag) |
| `unbooked_at_ib` | IB holds it, book has no record (manual / orphan) |
| `direction` | signs disagree |
| `quantity` | both hold it, sizes differ |

`classify_break` returns `None` within `BREAK_EPS` (1e-4) rounding noise;
`compute_breaks(book, broker)` diffs two signed-net-by-contract maps into the open
break list. `engines.execution.reconciler` folds the two dicts and persists the
breaks; `GET /positions/reconciliation` reuses the same classification.

## Related modules

- **reaper** ([`engines/execution/reaper.py`](../../src/engines/execution/reaper.py))
  — sweeps orphan IB orders (crash between `placeOrder` and its commit) and adopts
  them back onto their DB row by matching the durable `orderRef` idempotency key.
- **reconciler / order_reconciler** — repair stuck order state from recorded fills
  when the netted mirror cannot confirm a fill.
- **rollback_runner** — unwinds a `partial_fail` structure.

Invariant IDs (I1/I3/I4/I5/I7) refer to the OMS refactor spec enforced by the
`tests/unit/core/execution` suite.
