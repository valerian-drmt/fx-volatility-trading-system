# Orders & positions — data-model + pipeline audit

What tables hold orders/positions, **who writes each**, the core problem, how an
order travels from a click to a live IB fill (with algorithmic schemas), what is
sent/received at every hop, and **how to verify** an order is really
submitted / filled.

Companions: `ORDER_PIPELINE.md` (execution code path), `IB_ORDER_OPS.md`
(operator commands), `POSITIONS_TRUTH.md` (book-vs-broker principle).

---

## 1. The tables (grain · owner · role)

| Table | Grain | Written by | Holds |
|---|---|---|---|
| `trade_preview` | 1 / preview | API (`/trade/preview`) | priced legs + greeks *before* submit (discardable) |
| **`trade_structure`** | 1 / **Submit** | API (`/trade/submit`) | the **trade** : product name, tenor, state, `trace_id`. **System of record for identity.** |
| **`trade_order`** | 1 / **leg** | API (persist) + fills_handler (state) | one order line per leg : side, qty, `qty_filled`, `order_type`, `state`, `ib_order_id`, `ib_local_symbol` |
| **`trade_fill`** | 1 / **execution** | fills_handler | append-only fill events : `qty_filled`, `fill_price`, `commission_usd` |
| `booked_position` | 1 / structure | fills_handler (on full fill) | the desk's booking once every entry leg filled |
| **`open_position`** | 1 / **netted IB contract** | position_sync (~30 s) | the **IB mirror** : `trade_id`→structure, `contract_id`, side, `quantity`, marks/greeks |
| `trade_event` | 1 / event | API + engines | audit log (submission_attempt, order_rejected, position_close_initiated…) |
| `hedge_order` | 1 / hedge | execution-engine | the bundled delta-hedge futures order |

**Two id systems that confuse people** (your `#389` vs `#131`):
- `trade_structure.id` = **#389** — the *trade* (group header in Open positions).
- `open_position.id` = **#131** — the *IB-mirror row* for one *contract*. You **close a contract**, so the backend names it by `open_position.id`.

**The mirror is netted.** IB nets by contract, so one `open_position` row can be the
sum of *many* structures trading the same contract — `position_sync` attributes that
net to one `trade_id`, which is why a leg's side can look "wrong" vs its structure.

---

## 2. The core problem (why it gets messy)

Two truths, on purpose:
- **The book** = `trade_order` + `trade_fill` (what we *sent* and what *executed*) — append-only, ours.
- **The broker mirror** = `open_position` (what IB *says* we hold) — overwritten every sync.

The friction comes from three facts:
1. **IB nets by contract** → a contract shared across structures collapses to one net
   position ; per-leg attribution is best-effort (`trade_id`), so a leg can read
   *unlinked* (`— —`, `trade_id` NULL = **orphan**) or attributed to the wrong structure.
2. **Fills are async + partial** → an option order on paper dribbles ; a leg fills
   while its sibling doesn't → **naked residual** (`1/3 legs ⚠ naked`).
3. **No order-lifecycle terminalizer** → a close that never fully fills (or is
   cancelled at IB) is **never flipped terminal** in `trade_order` → it lingers
   `submitted` forever (your `#388`, `⏱ 91h`), and its qty keeps blocking new closes
   via the stacking guard.

`GET /positions/reconciliation` exists precisely to make (1) visible as *breaks*.

---

## 3. How an order is sent — the pipeline

```
 UI Order builder
   │  builderToLegs()  → free legs {contract_type, side, tenor, delta_pillar|strike}
   ▼
 POST /trade/preview ───────────────► build_from_legs → resolve strikes off LIVE surface,
   │                                   price (BS), greeks, classify_legs → name
   │  (trade_preview row, discardable)
   ▼
 POST /trade/submit ──────────────────────────────────────────────────────────────┐
   │  persist:  trade_structure(state=submitted)                                    │
   │            trade_order × N   (state=pending ; option→LMT, future→MKT)          │
   │            trade_event(submission_attempt)                                     │
   │  dispatch: POST exec-engine /internal/structure/submit                         │
   ▼                                                                                │
 execution-engine  (clientID 5)                                                     │
   │  per leg: qualify contract → re-price marketable LMT off live quote            │
   │           ib.placeOrder → attach fill handlers → returns ib_order_id/perm_id   │
   │  stamp trade_order.ib_order_id, state=submitted                                │
   ▼                                                                                │
 IB Gateway (paper)  → fills arrive ASYNC ──────────────────────────────────────►  │
   ├─ fills_handler:  trade_fill(+row) ; trade_order.qty_filled/state ;             │
   │                  on all-entry-filled → booked_position                         │
   └─ position_sync (~30 s): IB positions → open_position (the panel's mirror)      │
   ▼                                                                                │
 Open positions panel ◄── /positions/structured (book identity + live marks) ◄──────┘
 Orders blotter       ◄── /trade/submitted (structure rows + state + Contract)
```

### Submit — pseudocode
```
on Submit(legs):
    preview = revalidate(previewId)            # strikes/greeks off the live surface
    structure = insert trade_structure(state=submitted, trace_id=req.trace_id)
    for i, leg in legs:
        order_type = MKT if leg.is_future or no_premium else marketable_LMT(preview_price)
        insert trade_order(structure.id, leg, state=pending, order_type)
    ee = POST exec-engine /internal/structure/submit {structure.id, legs}
    for order, ib in zip(orders, ee.orders):
        order.ib_order_id = ib.order_id; order.state = submitted
    return {structure_id, trace_id, state:"submitted"}
```

### Fill cascade — pseudocode (async, per IB event)
```
on IB fill(order_id, exec):
    if exec.id already in trade_fill: return              # idempotent
    insert trade_fill(order_id, qty, price, commission)
    order = load trade_order(order_id)
    order.ib_local_symbol ||= exec.contract.localSymbol   # first fill stamps it
    order.qty_filled = Σ fills ; order.avg_fill_price = vwap
    order.state = filled if qty_filled==qty else partially_filled
    if all entry legs of structure filled: create booked_position

every 30s  position_sync:
    for p in ib.positions():                              # NET per contract
        upsert open_position(localSymbol=p, qty=p.position, trade_id=resolve(p))
    delete open_position rows IB no longer holds
```

---

## 4. What is sent / received at each hop

| Hop | Sent | Received |
|---|---|---|
| UI → `/trade/preview` | free legs (type/side/tenor/pillar\|strike) | priced legs, greeks, structure name |
| UI → `/trade/submit` | previewId, legs, mode | `{structure_id, trace_id, state:submitted}` |
| API → exec-engine `/structure/submit` | `{structure_id, legs}` (+ `X-Trace-ID`) | `{orders:[{order_id, perm_id, status}]}` |
| exec-engine → IB (`placeOrder`) | qualified contract + marketable LMT/MKT | `Trade` obj → `orderStatus` events (async) |
| IB → fills_handler (event) | fill exec (qty, price, comm, localSymbol) | persisted `trade_fill` + updated `trade_order` |
| IB → position_sync (`reqPositions`) | — | net positions → `open_position` upserts |

---

## 5. How to verify an order is submitted / filled

### 5.1 Order states (IB status → our `trade_order.state`)
| IB status | our state | fills? |
|---|---|---|
| PendingSubmit | `pending` | not yet |
| **Submitted** | `submitted` | **working** — fills only when there's opposing size |
| Filled | `filled` | ✅ |
| Inactive | (stays `submitted`) | ❌ IB price-cap parked it |
| Cancelled / ApiCancelled | `cancelled` | ❌ |

> **`Submitted` ≠ filled.** It's *working*. On IB paper, deep-OTM / thin strikes may
> never fill → the row rests `submitted` (see the ⏱ stale flag in the blotter).

### 5.2 The three places to look
1. **Frontend** — Orders blotter (`/trade/submitted`) : Time · Trade · **Contract** ·
   Product · Type · State (+ ⏱ age when working > 10 min). Open positions
   (`/positions/structured`) shows what IB actually holds.
2. **DB** — the ground truth :
   ```sql
   -- one order's lifecycle
   SELECT id, side, qty, qty_filled, order_type, state, ib_order_id, ib_local_symbol
   FROM trade_order WHERE structure_id = <N> ORDER BY leg_idx;
   -- its fills
   SELECT qty_filled, fill_price, commission_usd, timestamp
   FROM trade_fill WHERE order_id = <O> ORDER BY timestamp;
   -- what IB holds (mirror)
   SELECT id, trade_id, structure, side, quantity FROM open_position ORDER BY id;
   ```
3. **IB (via exec-engine)** — is it *actually* resting at IB?
   ```powershell
   docker compose exec -T execution-engine python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:8001/internal/orders')); print(d['count']); [print(o['order_id'],o['status']) for o in d['orders']]"
   ```

### 5.3 Decision tree — "did my order go through?"
```
blotter shows the row?
 ├─ no  → submit failed (check trade_event / API 4xx-5xx ; nothing persisted)
 └─ yes → state?
      ├─ pending            → not yet acknowledged by IB (seconds) — wait
      ├─ submitted          → working. is it AT ib?  (§5.2.3)
      │     ├─ in /internal/orders  → genuinely resting; fills on liquidity (paper = slow)
      │     └─ NOT at IB           → GHOST row (cancelled/dead, never terminalized) → reconcile/cancel
      ├─ partially_filled   → some size done; rest working (or stuck → ghost)
      ├─ filled             → done ✅  (open_position should mirror it within ~30 s)
      ├─ rejected           → see trade_order.rejection_text
      └─ cancelled          → pulled
```

### 5.4 Reconcile book vs broker
`GET /positions/reconciliation` nets filled `trade_order` vs `open_position` per
contract and classifies any gap (`missing_at_ib` / `unbooked_at_ib` / `direction` /
`quantity`). `GET /positions/ledger` gives realised/unrealised P&L folded from
`trade_fill` (the reproducible book).

---

## 6. Known failure modes → what to check

| Symptom | Cause | Check / fix |
|---|---|---|
| `submitted ⏱ 91h` for a dead order | never terminalized (no cancel→DB sync) | is it at IB (§5.2.3)? if not → cancel/reconcile the stale row |
| `1/3 legs ⚠ naked` | deep-OTM wing didn't fill (thin paper liquidity) | wait / cancel the wing order / use 25Δ not 10Δ |
| position `— —` (no trade/contract) | orphan : `trade_id` NULL (netting/attribution) | `/positions/reconciliation` → `unbooked_at_ib` |
| close blocked "already N closing" | a prior close still in-flight/ghost | cancel the resting close (`IB_ORDER_OPS §4`) |
| leg side ≠ its structure's | IB netted the contract across structures | expected — the mirror is a net, not a per-structure holding |
