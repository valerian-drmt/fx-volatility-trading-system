# Order pipeline — full backend reference

> **Purpose.** A self-contained, code-grounded reference for the **entire backend
> order-send + position pipeline**: which files do what, a worked example of one
> order travelling end-to-end, exactly what is written to the DB at each step, and
> how IB data is cross-checked against the DB. Written so someone (or another model)
> can pick up the order/positions problem **without prior context**.

Branch: `sandbox/r11` (the free-legs Trade stack). Python 3.11, `PYTHONPATH=src`.

## Read in this order

| File | Answers |
|---|---|
| [`01-files.md`](01-files.md) | **Which files** are involved and what each one does |
| [`02-example.md`](02-example.md) | **A worked example** — one butterfly, click → live fill, every hop |
| [`03-db-writes.md`](03-db-writes.md) | **What is written to the DB** at each step (table + columns + state) |
| [`04-ib-db-sync.md`](04-ib-db-sync.md) | **IB ↔ DB cross-check** — fills matching, `position_sync`, reconciler |
| [`05-states-and-verify.md`](05-states-and-verify.md) | **State machines** + how to verify an order is submitted/filled |

Companion docs (higher-level): `../ORDERS_POSITIONS_AUDIT.md` (data-model audit),
`../ORDER_PIPELINE.md`, `../IB_ORDER_OPS.md` (operator commands), `../POSITIONS_TRUTH.md`.

## The big picture (one diagram)

```
                          ┌──────────────────────── API container (src/api) ────────────────────────┐
 UI Order builder         │                                                                          │
 (frontend, free legs) ──►│ POST /trade/preview  → core/trade_preview.py  (price + classify)         │
                          │        writes: trade_preview                                             │
                          │ POST /trade/submit   → routers/trade.py                                  │
                          │        writes: trade_structure(submitted), trade_order×N(pending),       │
                          │                trade_event(submission_attempt)                           │
                          │        dispatch ──HTTP──► exec-engine /internal/structure/submit          │
                          └───────────────────────────────┬──────────────────────────────────────────┘
                                                          │ (X-Trace-ID)
       ┌──────────────── execution-engine (src/engines/execution, clientID 5) ─────────┐
       │ main.py /internal/structure/submit → live_submit.py / order_executor.py        │
       │   per leg: qualifyContractsAsync → marketable LMT off live quote → ib.placeOrder│
       │   stamp trade_order.ib_order_id, state=submitted                                │
       │   attach fill handlers (fills_handler.py)                                       │
       └───────────────────────────────┬───────────────────────────────────────────────┘
                                        │  ib_insync
                              ┌─────────▼──────────┐
                              │  IB Gateway (paper) │  ── fills arrive ASYNC ──┐
                              └─────────┬──────────┘                           │
             reqPositions (~30s)        │  orderStatus / execDetails events    │
       ┌──────────────────┐             │                                      ▼
       │  position_sync.py │◄────────────┘                        fills_handler.py
       │  IB net positions │                                    trade_fill(+row),
       │  → open_position  │                                    trade_order.qty_filled/state,
       └─────────┬─────────┘                                    booked_position (on full fill)
                 │ (Postgres: only DB-writer / engines write)
                 ▼
      ┌────────────────────────── panels read back ──────────────────────────┐
      │ /positions/structured  (book identity + live marks)  → Open positions │
      │ /trade/submitted        (structure rows + state + Contract) → Blotter  │
      │ /positions/reconciliation / /positions/ledger (book vs broker, P&L)    │
      └───────────────────────────────────────────────────────────────────────┘
```

## The one thing to understand first
There are **two truths**, kept separate on purpose:
- **The book** = `trade_order` + `trade_fill` (what we *sent* and what *executed*) — ours, append-only.
- **The broker mirror** = `open_position` (what IB *says* we hold) — overwritten every ~30 s.

**IB nets by contract**, so `open_position` is a *net per contract*, not a per-structure
holding — this is the source of most confusion (orphans, wrong-looking sides, stuck
closes). See `04-ib-db-sync.md`.
