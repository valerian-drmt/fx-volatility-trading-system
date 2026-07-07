# 01 — Files and responsibilities

Every file the order/position pipeline touches, grouped by the container that ships it.
`PYTHONPATH=src`, so imports are `from api…`, `from engines…`, `from core…`.

## API container (`src/api/`) — receives the click, persists, dispatches

| File | Key functions | Role |
|---|---|---|
| `api/routers/trade.py` | `create_preview` (`/preview`), `submit_preview` → `_submit_preview_impl` (`/submit`), `_post_execution_engine`, `_acquire_preview_lock`, `list_submitted_structures` (`/submitted`) | The **write path**: prices a preview, persists `trade_structure` + `trade_order`, decides order type, dispatches to the exec-engine. Live-submit is gated on `IbConnectionState` + `revalidate_preview`. |
| `api/routers/positions.py` | `close_one_open_position`, `close_live_position`, `close_trade`, `_compute_breaks`, `reconciliation`, `ledger`, `_marketable_close_from_mark` | Reads positions; **closes** a leg/trade; book-vs-broker reconciliation; P&L ledger. |
| `core/trade_preview.py` | `build_from_legs`, `classify_legs`, `_strangle_delta_bucket`, `price_structure`, `compute_net_greeks` | **Pure domain**: resolve strikes off the live surface, price (Black-Scholes), name the structure. No I/O. |
| `core/pricing/bs.py`, `core/risk/greeks.py` | BS price, Δ/Γ/V | pricing/greeks used by the preview |
| `core/payloads.py` | dict builders | engine output → DB row dicts |
| `api/schemas/…`, `api/dependencies.py`, `api/auth.py` | Pydantic req/resp, `get_db_session`, `require_write` | request models + DB session + write auth gate |

## Execution-engine container (`src/engines/execution/`) — talks to IB (clientID 5, :8001)

| File | Key functions | Role |
|---|---|---|
| `engines/execution/main.py` | FastAPI app, `@app.middleware` (trace), `/internal/structure/submit`, `/internal/positions/close-by-symbol`, `/internal/orders` (GET/DELETE), `/internal/reconcile`, `/internal/positions/sync` | The engine's **internal API** the API calls; wires the loops. |
| `engines/execution/live_submit.py` | `submit_structure_live`, `_marketable_limit`, combo (BAG) branch | Per leg: qualify → **re-price marketable LMT off the live quote** → `ib.placeOrder`. |
| `engines/execution/order_executor.py` | `OrderExecutor` (one shared IB conn), `close_position_by_symbol`, `_marketable_close_price`, `list_positions`, `cancel_order`, `account_is_reporting` | The IB wrapper: place/close/cancel/list; the marketable-close pricing. |
| `engines/execution/fills_handler.py` | `attach_fill_handlers`, `_on_order_status`, `_on_execution`, `_persist_fill`, `maybe_complete_structure` | **Async fill cascade**: IB events → `trade_fill` + `trade_order` updates → `booked_position`. |
| `engines/execution/position_sync.py` | `sync_positions_from_ib`, `_build_leg_to_trade_map`, `reconcile_trade_positions` | **IB → `open_position`** mirror (match by `localSymbol`, resolve `trade_id`); auto-close stale bookings. |
| `engines/execution/order_reconciler.py` | `reconcile_stuck_orders`, `_leg_matches_position`, `reconcile_loop` | Flip a stuck `submitted` order to `filled` when IB actually holds its contract. |
| `engines/execution/order_executor.py` (`trade_to_dict`) | serialise IB `Trade` → JSON | what the API gets back |

## Shared adapters

| File | Role |
|---|---|
| `persistence/models.py` | The **20 ORM classes** — `trade_structure`, `trade_order`, `trade_fill`, `open_position`, `booked_position`, `trade_event`, `hedge_order`, … (columns in `03-db-writes.md`). |
| `persistence/db.py`, `persistence/writer.py` | async engine + session factory; `AsyncDatabaseWriter` (batch INSERT/retry) |
| `bus/publisher.py`, `bus/channels.py`, `bus/keys.py`, `bus/client.py` | Redis pub/sub + last-value cache (order events, contract marks) |
| `shared/ib_connection.py` | IB sync wrapper + backoff |
| `shared/contracts.py` | `parse_local_symbol`, `multiplier_for` — the IB `localSymbol` ↔ contract-spec bridge |
| `shared/trace.py` | correlation `trace_id` (request → order → fill) |

## Who writes Postgres?
Only the **API** (structures/orders on submit/close) and the **engines** (fills,
`open_position`, events). The frontend never writes; it reads via the API. The
`db-writer` service handles market-data/analytics writes, **not** the order tables.
