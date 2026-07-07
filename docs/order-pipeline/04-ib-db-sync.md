# 04 — IB ↔ DB cross-check

How data from **IB** (fills, positions, order status) is matched to the **DB** (orders,
structures, the mirror). This is where most of the confusion (orphans, wrong sides, stuck
closes) comes from — all rooted in **IB nets by contract**.

## The bridge key: `ib_local_symbol`

Everything hinges on the IB **`localSymbol`** (e.g. `EUUV6 C1130`, `6EM6`):
- The DB order (`trade_order`) is created **without** it (we don't know the exact contract
  until IB qualifies + fills it).
- On the **first fill**, `fills_handler._persist_fill` stamps
  `trade_order.ib_local_symbol = fill.contract.localSymbol`.
- `position_sync` keys the IB mirror (`open_position.structure`) on that same `localSymbol`.
- `shared/contracts.py::parse_local_symbol(sym)` decodes it → `{symbol, strike, option_type,
  instrument_type, multiplier}`; `multiplier_for(symbol)` gives the USD multiplier.

So the join chain is:
```
IB execution.contract.localSymbol
   └─► trade_order.ib_local_symbol   (stamped on first fill)
          └─► trade_structure         (trade_order.structure_id)
open_position.structure  ═══ same localSymbol ═══►  resolves trade_id via the map below
```

## 1. Fills → orders (`fills_handler.py`)
- Matched by **`order_id`** (each `trade` was placed with `attach_fill_handlers(trade,
  order_id, sm)`, so the callback already knows its order).
- Deduped by **`trade_fill.ib_execution_id`** (unique) — replaying an IB event never
  double-books.
- `trade_order.qty_filled` / `avg_fill_price` are **recomputed** from the sum of that
  order's fills each event (not incremented).

## 2. IB positions → the mirror (`position_sync.py::sync_positions_from_ib`)
Runs on a loop (`SYNC_INTERVAL_S`):
1. `executor.list_positions()` → IB's **net** positions (falls back to `ib.portfolio()` if
   `ib.positions()` is empty), each with `local_symbol`, `con_id`, `position` (signed qty),
   `avg_cost`.
2. `_build_leg_to_trade_map(db)` → joins `trade_order.ib_local_symbol` → its
   `trade_structure` → `{local_symbol: (structure_type, trade_id, package_id)}`.
3. For each IB position (keyed by `local_symbol`):
   - `parse_local_symbol` → spec (strike, type, multiplier) → compute tenor / nominal / entry.
   - look up its parent structure in the map.
   - **exists in `open_position`** → UPDATE qty/side/expiry/marks/`trade_id`.
   - **new** → INSERT `open_position(entry_timestamp=now, …)`.
4. Any `open_position` row **not** in the IB snapshot → **DELETE** (IB no longer holds it).

Result columns: `structure` (localSymbol), `quantity` (abs; sign from `side`), `trade_id`
(NULL if no order matched = **orphan**), `contract_id`, marks/greeks.

> **This is why IB netting bites.** IB holds **one** net position per contract. If C1130 was
> traded by 10 structures (entries + closes), IB reports one `EUUV6 C1130` net, and
> `_build_leg_to_trade_map` attributes it to **one** `trade_id` — so a leg's side can look
> opposite to its structure, and a contract with no matching order becomes an **orphan**
> (`trade_id` NULL → shown as `— —`).

## 3. Stuck orders ← live positions (`order_reconciler.py::reconcile_stuck_orders`)
Runs every `RECONCILE_INTERVAL_S` (~60 s). Belt-and-suspenders for missed fill events:
1. find `trade_order` in `('submitted','acknowledged','partially_filled')`.
2. `_leg_matches_position(order, position)` — match to an `open_position` by
   **structure_id + side + contract_type + strike (±0.006 tol) + option_type**; each IB
   position matches **≤ 1** order.
3. if matched → **backfill**: `state="filled"`, `qty_filled=qty`,
   `avg_fill_price=position.market_price|preview_price`,
   `ib_local_symbol=position.structure`; `trade_event(order_reconciled_from_ib)`.
4. cascade `maybe_complete_structure`.

> It only promotes to **filled when IB actually holds the contract** — never a phantom fill.
> It does **not** terminalize a close that never filled → that's why dead closes linger
> `submitted` (the `#388 ⏱ 91h` case).

## 4. Stale bookings ← IB flat (`position_sync.py::reconcile_trade_positions`)
A `booked_position` IB shows **flat** for > 1 h (and only when the account is actively
reporting — `executor.account_is_reporting()` distinguishes "IB flat" from "feed dead") is
**auto-closed** (`RECONCILE_AUTOCLOSE`, default on). Guarded so it never acts on an empty/
disconnected snapshot.

## 5. The explicit book-vs-broker check (`/positions/reconciliation`)
The read-only reconciliation endpoint makes the disagreements **visible** instead of
silently trusting either side:
- **book (expected)** = signed Σ `trade_order.qty_filled` per contract (entries − closes).
- **broker (actual)** = signed Σ `open_position.quantity` per contract.
- `break = expected − actual`, classified `missing_at_ib` / `unbooked_at_ib` / `direction` /
  `quantity`. See `../ORDERS_POSITIONS_AUDIT.md §5.4`.

## Summary of the sync jobs

| Job | File / fn | Cadence | Direction | Writes |
|---|---|---|---|---|
| fill persistence | `fills_handler._persist_fill` | per IB event | IB → DB | `trade_fill`, `trade_order` |
| structure completion | `fills_handler.maybe_complete_structure` | on full fill | DB → DB | `trade_structure`, `booked_position` |
| position mirror | `position_sync.sync_positions_from_ib` | loop (~s) | IB → DB | `open_position` (upsert/delete) |
| stuck-order backfill | `order_reconciler.reconcile_stuck_orders` | ~60 s | IB → DB | `trade_order` (→ filled) |
| stale-booking close | `position_sync.reconcile_trade_positions` | loop | DB vs IB | close orders |
| explicit break view | `positions.reconciliation` (API) | on request | read-only | — |
