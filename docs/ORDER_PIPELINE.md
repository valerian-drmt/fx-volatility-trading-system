# Order pipeline — full audit

How a trade goes from a click in the Order builder to a live IB position, and how
each product is constructed. Source of truth for the execution path. Last audited
against the code on 2026‑07‑03.

---

## 1. End‑to‑end flow

```
Order builder (UI)
  │  builderToLegs()  → free legs (contract_type, side, tenor, delta_pillar | strike)
  ▼
POST /api/v1/trade/preview        (createTradePreview)
  │  build_from_legs → resolve strikes off the LIVE surface, price (BS), greeks
  │  classify_legs → structure_type / product_label ("long strangle 25d", …)
  ▼
POST /api/v1/trade/submit         (submitTrade, execution_mode="live")
  │  persist trade_structure (state=submitted) + trade_order rows (state=pending)
  │      → option legs = LMT (marketable), future legs = MKT   [trade.py]
  │  dispatch → execution-engine  POST /internal/structure/submit
  ▼
execution-engine  submit_structure_live()        [live_submit.py]
  │  per leg: qualify contract → re-price LMT off the LIVE quote → ib.placeOrder
  │  attach_fill_handlers (status + fill events)
  ▼
IB Gateway (paper, clientID 5)   → fills arrive async
  │
  ├─ fills_handler:  trade_order → filled ; maybe_complete_structure → booked_position
  └─ position_sync:  IB positions → open_position (the mirror the UI reads)
  ▼
Open positions panel  ← /positions/open (mirror) + /positions/structured (booking view)
```

Two independent truths, by design:
- **trade_order.state** = the order lifecycle (submitted → filled / rejected).
- **open_position** = what IB actually holds, mirrored every ~30 s by `position_sync`.
  The panel reads this — a leg shows only when IB truly holds it.

---

## 2. Product creation

The UI never sends strikes/premiums (except a hand‑typed vanilla strike). It sends
**free legs** with a `delta_pillar`; the backend resolves the strike from the live
surface and classifies the shape. Authoritative map = `frontend/.../orderLegs.ts`.

| Product (dropdown) | Legs sent (`builderToLegs`) | Notes |
|---|---|---|
| **Vanilla Call** | `call · side · strike` | hand‑typed strike (anchored to live ATM) |
| **Vanilla Put** | `put · side · strike` | idem |
| **Straddle/Strangle** · 50Δ (ATM) | `call · atm` + `put · atm` | = **straddle** (same strike) |
| **Straddle/Strangle** · 10Δ/25Δ | `put · Ndp` + `call · Ndc` | = **strangle** (OTM put + OTM call) |
| **Risk Reversal** | `call · Ndc` + `put(opp) · Ndp` | long one wing / short the other |
| **Call Spread** | `call · atm` + `call(opp) · Ndc` | long ATM / short higher call (ATM→25Δ guard) |
| **Put Spread** | `put · atm` + `put(opp) · Ndp` | long ATM / short lower put |
| **Butterfly** | `call · Ndp` + `call(opp) · atm ×2` + `call · Ndc` | 3 calls (low + 2× ATM body + high) |
| **Calendar** | `call(opp) · near` + `call · far` | same strike, two expiries |
| **Future** | `future · side · full\|micro` | 6E (€125k) or M6E (€12.5k) |

`Straddle` and `Strangle` still exist internally as fallbacks (signal prefills) but
are hidden from the dropdown — merged into **Straddle/Strangle** with a Δ selector.

### Δ / wing guards
- **ATM‑degeneracy guard**: for spreads / butterfly / strangle, an ATM wing selection
  would collapse two legs onto ATM (zero width, zero greeks). These force **25Δ** when
  ATM is picked. The straddle branch of Straddle/Strangle is the *only* place ATM is
  intentional (both legs ATM).
- **Vanilla strike** defaults to the **live ATM** (not the mock spot), snapped to the
  0.005 grid, so it qualifies at IB. Stops auto‑anchoring once hand‑typed.

### Classification → name
`classify_legs(legs, spot)` names the shape from the legs:
- 2 calls, opposite sides → `call spread` ; 2 puts → `put spread`
- call+put same side, same strike → `straddle` ; different strikes → `strangle <bucket>`
  where the bucket (`10d` / `25d`) comes from the OTM legs' **BS |delta|**.
- 3 same‑type, 2 sides → `butterfly` ; call+put opposite sides → `risk reversal`.

Frontend `formatStructLabel` turns these into display names: `long strangle 25d` →
**"Strangle 25Δ"**, `long straddle` → **"Straddle"**, etc.

---

## 3. How orders are sent

### 3.1 Order type (persisted at submit — `api/routers/trade.py`)
- **Futures** → `MKT` (deep, tight book, no cap issue).
- **Options** → **marketable `LMT`**. A *market* order on an option triggers IB's
  option **price‑cap protection** → BUY legs come back `Inactive` on wide spreads →
  naked half‑fills. So options cross the spread instead.
  - limit = `preview_premium × (1 ± MARKETABLE_LIMIT_BUFFER)` (buffer default **0.25**;
    BUY +, SELL −), rounded to the **0.0001 tick** (IB rejects sub‑tick with
    *Warning 110*). `preview_price` is already the premium in price points
    (`CONTRACT_MULTIPLIER = 1`) — **no** divide by the FOP multiplier.
  - falls back to `MKT` only if there's no premium to price from.

### 3.2 Re‑pricing at execution (`live_submit.py` → `_marketable_limit`)
The persisted limit is a *fallback*. At submit time, each option leg is **re‑priced
off IB's live quote**: **BUY → the ask, SELL → the bid**, snapped to the tick. The
theoretical premium mis‑prices real options (esp. OTM), so a `premium × 0.75` SELL
sits above the bid and never crosses — pricing off the actual quote fixes it. If no
quote is available, it uses the stored limit.

### 3.3 Combo (BAG) path — OFF by default
`EXECUTION_USE_COMBO` (default **0**). When on, combo‑eligible option structures go
as one IB BAG (all‑or‑nothing). **Disabled** because IB **paper** mangles BAGs (legs
fill then net to flat → book drift). The per‑leg path is the reliable default; the
**naked‑residual flag** surfaces any half‑fill. Combo fills report on the BAG conId,
so `_book_combo_filled` books legs off the combo's Filled status.

### 3.4 Bundled delta hedge
The Order builder's "Delta hedge" checkbox fires a **second, linked order** — a 6E
futures hedge (its own MKT order) after the structure. Best‑effort: a hedge failure
doesn't roll back the structure. Disabled for the Future product (a future is already
delta‑one).

---

## 4. Fills → positions

- **`fills_handler`**: on each IB fill, persists `trade_fill`, updates
  `trade_order` (qty_filled, avg_fill_price, state). When **all entry legs are
  filled**, `maybe_complete_structure` creates the `booked_position`.
- **`position_sync`** (every ~30 s): reads IB positions → upserts/deletes
  `open_position` (the UI mirror). Also `reconcile_trade_positions`.

The panel reads `open_position` (raw IB truth) + `/positions/structured` (grouped by
`trade_structure`, per‑structure attribution by `trade_id`, with the naked flag).

### 4.1 Frontend refresh (why Open positions used to "update weirdly")
The panel renders from **two independently‑polled sources**: the leg rows come from
the desk `trade` slice (`/positions/open`, polled on `TRADE_POLL_MS = 15 s`), the
group name / `N/M legs` / naked flag come from `/positions/structured`. These were
polled at **different intervals** (15 s vs 60 s), so for up to ~45 s after a fill the
leg rows were present but the structure context wasn't → the group name fell back to
`inferStructureName(legs)` and the counts mismatched the visible legs. Fixes:
- **Both sources poll on the same 15 s cadence** (`structured` aligned to
  `TRADE_POLL_MS`) so labels and rows move together — no more flicker.
- **On a successful send**, `addOrder` force‑refetches all three sources at once
  (`submitted.reload()` blotter + `structured.reload()` + `reloadTrade()` positions
  mirror, exposed on the desk‑data context) instead of letting each timer fire on its
  own — the new trade appears in one coherent step, not staggered.

(The position itself still only appears once IB fills and `position_sync` mirrors it,
~up to 30 s later — the reload just removes the client‑side desync/staleness on top.)

---

## 5. Closing

`closeContract(pos_id, qty)` (one leg) / `closeTrade(trade_id)` (all open legs) →
`close_one_open_position` → execution‑engine `/internal/positions/close-by-symbol`.
The reverse order (SELL to close a long) is priced the **same marketable way** as an
entry.

**Options always cross with a marketable `LMT`, in RTH too.** The API prices the
close off the position's **mark** — `_marketable_close_from_mark`: closing a long →
SELL at `mark × (1 − MARKETABLE_LIMIT_BUFFER)` (through the bid), closing a short →
BUY at `mark × (1 + buffer)` (through the ask), snapped to the 0.0001 tick. Futures
inside RTH → `MKT`; futures outside RTH → `LMT` at `mark × (1 ± 5 bps)`.

> **Why not a plain MKT during RTH?** A market order on an option hits IB's option
> **price‑cap** and only *dribbles* partial fills (e.g. 7 of 17 over ~15 min), resting
> `Submitted` and never completing — the cap is not an RTH thing. The exec‑engine's
> `_marketable_close_price` (off the live quote) *tries* to fix this, but IB **paper
> returns no bid/ask** for these FOPs, so it degraded to a `MarketOrder` → same hang.
> Pricing off the **mark we already hold** (not a live quote that may be empty)
> guarantees a real crossing limit reaches IB.

**Stacking guard.** A close only reads `filled` once IB fills it (~30 s for options),
but the panel still shows the open position meanwhile — so the operator re‑clicks and
each click used to stack **another full‑size close** (seen live: ~7 overlapping SELL
orders on one 18‑lot, 24 orders resting at IB). `close_one_open_position` now sums the
**live closing qty already covering this exact contract** (`order_role='closing'`,
reverse side, same expiry+strike, state ∈ pending/submitted/partially_filled) and
returns **409** if `already_closing + qty > open_qty` — refusing to over‑close /
flip the book. Cancel a stuck close via `DELETE /internal/orders/{id}` (exec‑engine).

---

## 6. Reconciliation (book ↔ IB)

- **`order_reconciler`** (60 s + `POST /internal/reconcile`): a stuck `submitted`
  order whose contract IB actually holds (matched to `open_position` by side + type +
  strike) is flipped to **filled** — never a phantom fill.
- **`reconcile_trade_positions`**: a booked position IB shows **flat** (for >1h, and
  only when the account is actively reporting) is **auto‑closed** (`RECONCILE_AUTOCLOSE`,
  default on) — guarded so it never acts on an empty/disconnected IB snapshot.
- **`account_is_reporting()`** distinguishes "IB flat" from "feed dead".
- `list_positions()` falls back to `ib.portfolio()` when `ib.positions()` is empty.

---

## 7. Known gotchas (bit us; keep in mind)

1. **IB option market‑order cap** → market orders on options leave BUY legs
   `Inactive` → naked. *Fix: marketable limits (§3).*
2. **Tick size** → option limits must be on the **0.0001** grid or IB rejects with
   *Warning 110* and the order hangs `submitted`.
3. **Preview premium ≠ IB market** (esp. OTM) → price limits off the **live quote**,
   not the theoretical premium.
4. **Mock vs live spot** — the client‑side preview greeks/IV/strikes use the *mock*
   surface (`DATA.SPOT = 1.0842`) while the market is ~1.14; the **backend** re‑prices
   off the live surface, so the *order* is correct even if the preview display is off.
5. **IB nets by contract** — two structures sharing a strike+expiry collapse to one
   IB position; per‑structure attribution is by `trade_id`, so the other structure's
   leg reads unlinked (correct, but a leg can look "missing").
6. **IB paper combos** go flat/net oddly → combos off by default.

---

## 8. Key files

| Concern | File |
|---|---|
| Product → legs (UI) | `frontend/src/voldesk/components/orderLegs.ts` |
| Order builder UI | `frontend/src/voldesk/components/OrderBuilder.tsx` |
| Preview / classify / pricing | `src/core/trade_preview.py` |
| Persist + order type | `src/api/routers/trade.py` |
| Live submit + re‑price | `src/engines/execution/live_submit.py` |
| Marketable limit / close / list_positions | `src/engines/execution/order_executor.py` |
| Fills → booking | `src/engines/execution/fills_handler.py` |
| IB → open_position + recon | `src/engines/execution/position_sync.py` |
| Stuck‑order reconcile | `src/engines/execution/order_reconciler.py` |
| Close endpoints | `src/api/routers/trades.py`, `src/api/routers/positions.py` |
| Positions API (mirror + structured) | `src/api/routers/positions.py` |
</content>
