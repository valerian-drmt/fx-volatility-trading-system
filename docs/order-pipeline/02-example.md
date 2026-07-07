# 02 — Worked example: one butterfly, click → live fill

Follow a single **live butterfly** (3 call legs: long low + short 2× ATM body + long
high) from the click to a booked position, naming every function and every DB write.
File paths are relative to `src/`.

## Step 0 — the UI builds free legs
`frontend/.../orderLegs.ts::builderToLegs("Butterfly", …)` emits 3 legs (no product name,
no strikes — just `delta_pillar`s):
```
[ {call, BUY,  25dp, 3M}, {call, SELL, atm, 3M, qty_factor:2}, {call, BUY, 25dc, 3M} ]
```

## Step 1 — PREVIEW  `POST /api/v1/trade/preview`
`api/routers/trade.py::create_preview`:
1. loads limits/regime/book/surface (`_read_surface_redis` → Redis `latest_vol_surface:EURUSD`).
2. `core/trade_preview.py::build_from_legs(legs, surface)` → resolves each `delta_pillar`
   to a **strike off the live surface**, builds a `Structure`; `classify_legs` names it
   → `structure_type = "butterfly"`.
3. `price_structure` + `compute_net_greeks` → premium, max-loss, Δ/Γ/V/Θ.
4. `run_pre_submit_checks` → max-loss vs capital, vega vs book, IV freshness, no-arb…
5. **writes `trade_preview`**: `TradePreviewRow(preview_id="tp_ab12cd34ef56",
   structure_full_payload=<whole priced dict>, pre_submit_checks=[…],
   state="valid_for_submit")`. Nothing else persisted yet.

→ UI shows the ticket (greeks, premium, checks).

## Step 2 — SUBMIT  `POST /api/v1/trade/submit {preview_id, execution_mode:"live"}`
`api/routers/trade.py::submit_preview → _submit_preview_impl`:
1. `_acquire_preview_lock(preview_id)` (Redis `SET … EX 10 NX`) — no double-submit.
2. gate: `IbConnectionState.is_connected` must be true, else `trade_event(submission_blocked)` + 503.
3. gate: `revalidate_preview(...)` (not expired, signal still actionable, IV fresh), else 400.
4. **persist (all in one commit):**
   - `trade_structure(state="submitted", structure_type="butterfly", base_qty=10, trace_id)`
   - `trade_event(submission_attempt)`
   - **3× `trade_order`** (`order_role="entry"`, `state="pending"`), each with its
     **order type**:
     - options with a premium → `order_type="LMT"`,
       `limit_price = round(preview_price × (1 ± MARKETABLE_LIMIT_BUFFER), 4)`
       (BUY +, SELL −; buffer default **0.25**),
     - no premium → `MKT`.
   - `trade_preview.user_action="submitted"`.
5. **dispatch**: `_post_execution_engine("/internal/structure/submit", {"structure_id": 389})`
   — note the payload is **just the id**; the engine loads the legs from the DB.

## Step 3 — EXECUTION-ENGINE places the orders
`engines/execution/main.py` `/internal/structure/submit` → `live_submit.py::submit_structure_live(sm, executor, structure_id=389)`:
1. loads `trade_structure` #389 + its 3 `entry` `trade_order` rows.
2. `trade_event(live_submit_attempt, {n_orders:3, combo_eligible})`.
   (combos are **off** by default → per-leg path.)
3. **per leg:**
   - `build_contract_kwargs(...)` → `ib.qualifyContractsAsync(contract)` (fills conId/exchange; futures retry on GLOBEX).
   - re-price: `_marketable_limit(ib, contract, side, fallback=order.limit_price)` —
     `reqTickersAsync` → BUY = ceil(**ask**), SELL = floor(**bid**), tick-snapped; falls
     back to the preview limit if no quote. **`order.limit_price` is overwritten** with this.
   - `trade = ib.placeOrder(contract, LimitOrder|MarketOrder)`.
   - `attach_fill_handlers(trade, order.id, sm)` — wires `statusEvent` + `fillEvent`.
   - **update `trade_order`**: `ib_order_id`, `ib_perm_id`, `state="submitted"`, `submitted_at`.
4. returns `{orders:[{order_id, perm_id, status}…]}` → the API stamps ids and returns
   `{structure_id:389, state:"submitted", trace_id}` to the UI.

→ Blotter now shows `#389 Butterfly 3M Entry 10 submitted`.

## Step 4 — FILLS arrive (async, one leg at a time)
IB fires events on each `trade` → `fills_handler.py`:
- `_on_order_status(trade, order_id, sm)` maps `orderStatus.status` → `trade_order.state`
  (+ `trade_event(order_acknowledged|rejected|cancelled)`). It **re-binds `trace_id`**.
- `_on_execution(trade, fill, order_id, sm)` → `_persist_fill`:
  1. idempotent on `trade_fill.ib_execution_id` (dedupe).
  2. **insert `trade_fill`** (`qty_filled`, `fill_price`, `commission_usd`, `side`, `trace_id`).
  3. stamp `trade_order.ib_local_symbol` on the **first** fill (e.g. `"EUUV6 C1130"`) — the
     exact IB contract, and the key `position_sync` later matches on.
  4. recompute `trade_order.qty_filled` / `avg_fill_price` / commissions from the **sum of
     that order's fills**; set `state = filled | partially_filled`.
  5. on **all entry legs filled** → `maybe_complete_structure(sm, 389)`:
     `trade_structure.state="fully_filled"`, totals; **create `booked_position(state="open")`**;
     `trade_event(structure_filled)`.

> **The butterfly reality (why yours go naked):** the low wing (C1130) and body (C1150)
> fill; the **high wing (deep OTM 25Δc→ e.g. C1195) may not** on paper (thin liquidity)
> → that leg stays `submitted`, `qty_filled=0`, no `ib_local_symbol` → the structure is
> `partial_fill`, and the panel reads `1/3 legs ⚠ naked`. See `05-states-and-verify.md`.

## Step 5 — the MIRROR catches up (~30 s)
`position_sync.py::sync_positions_from_ib` (loop):
1. `executor.list_positions()` → IB's **net** positions keyed by `localSymbol`.
2. `_build_leg_to_trade_map(db)` joins `trade_order.ib_local_symbol → trade_structure` to
   resolve each contract's `trade_id`.
3. **upsert/delete `open_position`** rows: `quantity`, `side`, marks/greeks, `trade_id`,
   `contract_id`. IB nets C1130 across *all* butterflies → **one** `open_position` row for
   C1130, attributed to a `trade_id`.

→ Open positions panel (`/positions/structured`) now shows the butterfly with its live
legs; P&L via `/positions/ledger`; any book-vs-broker gap via `/positions/reconciliation`.

## The mock path (for contrast)
`execution_mode="mock"` skips IB entirely: `_submit_preview_impl` writes the
`trade_order`s already `state="filled"`, synthesises `trade_fill` rows at the preview
price, marks the structure `fully_filled`, creates the `booked_position`, and refreshes
`BookStateSnapshot` — all in the one request. No exec-engine, no `open_position` (no IB).
