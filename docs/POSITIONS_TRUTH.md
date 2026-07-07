# Positions: book vs broker (system of record)

How the desk answers two different questions about "what do we hold?" — and why it
keeps them **separate** instead of trusting one blindly.

## Two truths, by design

| Source | Question it answers | Table | Nature |
|---|---|---|---|
| **The book** | "What do our own executions add up to?" | `trade_fill` (append-only) | reproducible from events, never overwritten |
| **The broker mirror** | "What does IB say we hold right now?" | `open_position` | overwritten every sync (~30 s) |

The **book is the system of record**: the immutable `trade_fill` log is the audit-grade
source. The **IB mirror is a reconciliation feed** — useful for "what the street thinks
we hold" and for live marks, but never the authority on our own P&L or identity.

> **Principle: own your truth, reconcile everything external.** A broker feed is a feed,
> not a source of truth. We *reconcile against* IB; we don't *read our P&L from* it.

## The two endpoints

### `GET /api/v1/positions/ledger`
Positions + realised / unrealised P&L **folded from the `trade_fill` event log**
(average-cost), independent of the mutable mirror.

- Fills are folded **in execution order** (by fill timestamp).
- Per contract: **net qty**, **average cost**, **realised P&L** (net of commissions),
  and **unrealised MTM** (using the mirror's `market_price` as the mark on the open qty).
- Reproducible from events: re-running the fold gives the same numbers — that's what an
  auditor (or a P&L sign-off) trusts. Pure fold logic lives in `core/ledger.py`.

Response shape:
```json
{ "as_of": "…",
  "positions": [ { "contract": "EUUV6 C1130", "net_qty": 0.0, "avg_cost": 0.02,
                   "realized_pnl": 12490.0, "unrealized_pnl": 0.0,
                   "commission": 10.0, "multiplier": 125000.0 } ],
  "totals": { "realized_pnl": …, "unrealized_pnl": …, "commission": … } }
```

### `GET /api/v1/positions/reconciliation`
The **break view** — where the book disagrees with the broker.

- Nets the **book** (filled `trade_order` qty, entries minus closes) vs the **broker**
  (`open_position`), **per contract** (IB nets by contract), and attributes each break to
  a structure.
- Every break is classified:
  - `missing_at_ib` — book holds it, IB is flat (fill not reflected / recon lag)
  - `unbooked_at_ib` — IB holds it, the book has no record (manual / orphan)
  - `direction` — signs disagree (we think long, IB is short)
  - `quantity` — both hold it, sizes differ
- Uses `qty_filled` (real executions), so a cancelled-but-not-flipped order row doesn't
  distort the net. Pure diff logic in `_compute_breaks`.

Response shape:
```json
{ "as_of": "…", "n_contracts": 5, "n_breaks": 1,
  "breaks": [ { "contract": "EUUV6 P1090", "expected_net": 10.0, "actual_net": 6.0,
                "break": 4.0, "kind": "quantity", "structure_id": 42 } ] }
```

## How they fit together
- The ledger's **net qty per contract is exactly the `expected` side** of reconciliation.
- Together: *here's our fill-derived book, here's what the broker holds, here's any break,
  and here's the money* — all derived, all reproducible, neither side trusted blindly.

Both are **read-only diagnostics**. They don't replace the operational mirror; they make
the desk's own record first-class and make disagreements with the broker **visible**
instead of silently swallowed.
