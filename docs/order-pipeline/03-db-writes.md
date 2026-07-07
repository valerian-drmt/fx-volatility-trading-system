# 03 — What is written to the DB, step by step

The tables and their **key columns** (from `src/persistence/models.py`), and **when
each is written** during an order's life. `__tablename__` is shown; the ORM class name
differs (e.g. `StructureOrder` → `trade_order`).

## The tables (key columns)

### `trade_structure` (ORM `TradeStructure`) — the trade
`id`, `created_at`, `preview_id`(FK), `pca_signal_id`, `structure_type` (classifier
verdict, e.g. `"long strangle 25d"` / `"butterfly"`), `product_label`, `reference_tenor`,
`expiry_date`, `base_qty`, **`state`** (submitted→partial_fill→fully_filled→closed),
`execution_mode`, `total_premium_paid_usd`, `total_commission_usd`, `first_fill_at`,
`fully_filled_at`, `closed_at`, `trace_id`.

### `trade_order` (ORM `StructureOrder`) — one order per leg
`id`, `structure_id`(FK), `leg_idx`, **`order_role`** (entry/closing/unwind/hedge),
`ib_order_id`, `ib_perm_id`, **`ib_local_symbol`** (stamped on first fill — the exact IB
contract), `contract_symbol`, `contract_type`, `contract_expiry`, `contract_strike`,
`side`, `qty`, **`order_type`** (LMT/MKT), `limit_price`, `preview_price`, **`state`**,
`submitted_at`, `acknowledged_at`, `rejected_at`, `rejection_text`, **`qty_filled`**,
`avg_fill_price`, `total_commission_usd`, `fully_filled_at`, `trace_id`.

### `trade_fill` (ORM `StructureFill`) — append-only execution events
`id`, `order_id`(FK), **`ib_execution_id`** (unique — idempotency key), `timestamp`,
`qty_filled`, `fill_price`, `commission_usd`, `exchange`, `side`, `spot_at_fill`,
`bid_at_fill`, `ask_at_fill`, `received_at`, `trace_id`.

### `open_position` (ORM `OpenPosition`) — the IB mirror, one row per netted contract
`id`, **`structure`** (the IB `localSymbol` — canonical key), `product_label`,
**`contract_id`** (IB conId), **`trade_id`**(FK→trade_structure, NULL = orphan),
`package_id`, `side`, `tenor`, `expiry`, **`quantity`** (abs; sign from `side`),
`nominal_eur`, `contract_price_entry`, `market_price`, `current_pnl_usd`,
`delta_usd`/`gamma_usd`/`vega_usd`/`theta_usd`/`vanna_usd`/`volga_usd`, `iv`,
`entry_timestamp`, `timestamp` (updated each sync).

### `booked_position` (ORM `BookedPosition`) — the desk's booking on full fill
`id`, `structure_id`(FK), `state` (open/closed), `opened_at`, greeks/premium snapshot.

### `trade_event` (ORM `TradeEvent`) — audit log
`id`, `structure_id`(FK), `event_type`, `severity`, `description`, `payload`(JSONB),
`created_at`.

## Write timeline

| Step | Trigger | Writes |
|---|---|---|
| **Preview** | `POST /trade/preview` | `trade_preview` (priced legs + greeks; discardable) |
| **Submit — persist** | `POST /trade/submit` | `trade_structure` (`state=submitted`, `trace_id`); one `trade_order` per leg (`state=pending`, `order_type`, `limit_price`, `preview_price`, `trace_id`); `trade_event(submission_attempt)` |
| **Submit — dispatch ok** | exec-engine returns | each `trade_order`: `ib_order_id`, `ib_perm_id`, `state=submitted`, `submitted_at` |
| **Order acknowledged** | IB `orderStatus` | `trade_order.state`/`acknowledged_at`; `trade_event(order_acknowledged)` |
| **Rejected** | IB `orderStatus=rejected` | `trade_order.state=rejected`, `rejection_text`; `trade_event(order_rejected)` |
| **Each fill** | IB `execDetails` | **new `trade_fill` row** (idempotent on `ib_execution_id`); `trade_order.qty_filled`/`avg_fill_price`/`total_commission_usd`; `ib_local_symbol` (first fill); `state=partially_filled`\|`filled` |
| **All entry legs filled** | fills_handler | `booked_position` (open); `trade_structure.state=fully_filled`, `fully_filled_at`, totals |
| **Position sync (~30 s)** | `ib.positions()` | **upsert/delete `open_position`** (qty, marks, greeks, `trade_id`, `timestamp`) |
| **Close — persist** | `POST /positions/{id}/close` | a **new** `trade_structure` (the close) + `trade_order` (`order_role=closing`, reverse side); `trade_event(position_close_initiated)` |
| **Close — fills** | IB | same fill cascade → the netted `open_position.quantity` shrinks on next sync |

## The idempotency + append-only guarantees
- **Fills are append-only** and deduped on `trade_fill.ib_execution_id` — replaying an IB
  event never double-counts.
- `trade_order.qty_filled` / `avg_fill_price` are **recomputed** from the sum of that
  order's `trade_fill` rows each event (not incremented blindly).
- `open_position` is **mutated in place** (it's a mirror) — it is *not* a source of truth
  for P&L; the fill log is. See `../POSITIONS_TRUTH.md` and `/positions/ledger`.
