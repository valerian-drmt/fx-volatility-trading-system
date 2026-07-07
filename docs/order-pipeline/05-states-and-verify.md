# 05 — State machines + how to verify an order

## Order state machine (`trade_order.state`)

IB reports an `orderStatus.status`; `fills_handler._map_status` maps it onto our state.

```
            place order
 pending ──────────────► submitted ──(fills accumulate)──► partially_filled ──► filled
    │                        │                                   │
    │                        ├──► rejected      (IB rejected / risk)
    │                        ├──► cancelled     (we DELETE, or IB pulls)
    │                        └──► (Inactive)    IB price-cap parked it → STAYS 'submitted' (never fills)
```

| IB status | our `state` | fills? | meaning |
|---|---|---|---|
| `PendingSubmit` | `pending` | not yet | sent, not acknowledged |
| **`Submitted`** | `submitted` | **working** | live at IB; fills only when opposing size exists |
| `Filled` | `filled` | ✅ | fully executed |
| `Inactive` | *(stays `submitted`)* | ❌ | IB **price-cap** parked it (option MKT) |
| `Cancelled` / `ApiCancelled` | `cancelled` | ❌ | pulled |

> **`Submitted` ≠ filled.** It's *working*. On IB **paper**, deep-OTM/thin strikes may
> have no opposing size → the order rests `submitted` **forever** (the blotter shows a
> ⏱ age). There is **no terminalizer** for a never-filling close, so its row lingers.

## Structure state machine (`trade_structure.state`)

```
 submitted ──(entry legs fill)──► partial_fill ──► fully_filled ──► closed
     │                                                   │
     └──► partial_fail / fully_failed  (dispatch/placement failed)
```

## How to verify an order actually went through

### The three sources of truth

1. **Frontend — Orders blotter** (`GET /trade/submitted`): `Time · Trade · Contract ·
   Product · Type · State` (+ ⏱ age when working > 10 min). Open positions panel
   (`GET /positions/structured`) = what IB actually holds, grouped by trade.

2. **The DB** — ground truth:
   ```sql
   -- one structure's legs + their lifecycle
   SELECT id, leg_idx, side, qty, qty_filled, order_type, limit_price, state,
          ib_order_id, ib_local_symbol, submitted_at
   FROM trade_order WHERE structure_id = <N> ORDER BY leg_idx;

   -- the fills behind a leg
   SELECT qty_filled, fill_price, commission_usd, timestamp
   FROM trade_fill WHERE order_id = <O> ORDER BY timestamp;

   -- what IB says we hold (the mirror)
   SELECT id, trade_id, contract_id, structure, side, quantity, market_price
   FROM open_position ORDER BY id;

   -- the audit trail for a structure
   SELECT event_type, severity, description, created_at
   FROM trade_event WHERE structure_id = <N> ORDER BY created_at;
   ```

3. **IB itself** (via execution-engine, internal Docker network only):
   ```powershell
   # orders actually resting at IB right now
   docker compose exec -T execution-engine python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:8001/internal/orders')); print('count',d['count']); [print(o['order_id'],o['status']) for o in d['orders']]"
   ```

### Decision tree — "did my order go through?"

```
blotter shows the row?
 ├─ no  → submit FAILED. check trade_event (submission_attempt/…) + the API response.
 │        nothing was persisted OR the exec-engine dispatch 5xx'd.
 └─ yes → look at trade_order.state:
      ├─ pending            → not yet acknowledged (seconds) — wait
      ├─ submitted          → WORKING. is it really at IB?  (source #3)
      │     ├─ in /internal/orders → genuinely resting; fills on liquidity (paper = slow)
      │     └─ NOT at IB          → GHOST row (cancelled/dead, never terminalized)
      │                             → cancel/reconcile the stale row
      ├─ partially_filled   → some size done; the rest is working (or a ghost)
      ├─ filled             → DONE ✅  → open_position should mirror it within ~30 s
      ├─ rejected           → read trade_order.rejection_text
      └─ cancelled          → pulled
```

### Cross-check book vs broker
- `GET /positions/reconciliation` — nets filled `trade_order` vs `open_position` per
  contract, classifies any gap (`missing_at_ib` / `unbooked_at_ib` / `direction` /
  `quantity`).
- `GET /positions/ledger` — realised/unrealised P&L folded from `trade_fill` (the
  reproducible book, independent of the mirror).

### The classic confusions (all in `04-ib-db-sync.md`)
- **`#389` (trade) vs `#131` (position)** — `trade_structure.id` vs `open_position.id`.
- **Leg side ≠ its structure** — the mirror is a *net per contract* across all structures.
- **`— —` position** — orphan (`open_position.trade_id` NULL), IB holds it, no booking claims it.
- **`submitted ⏱ 91h`** — a dead close order never terminalized.
