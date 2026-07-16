# Spot EUR/USD trade panel — plan

Goal: a small dedicated "Spot EUR/USD" panel on the Trade tab, directly above
the "Close position" panel, to BUY/SELL EUR.USD spot. Fills must flow into the
Cash holdings display (EUR / USD settled balances).

## How the pieces already connect (audit)

**Order path (exists, options/futures only today):**

```
TradeView → POST /api/v1/orders (require_write, pure proxy, src/api/routers/orders.py)
         → POST /internal/orders on execution-engine (src/engines/execution/main.py:281)
         → OrderExecutor.place_order (src/engines/execution/order_executor.py:267)
         → ib_insync Contract + LimitOrder → IB Gateway
```

- `PlaceOrderBody` (`engines/execution/main.py:250`) restricts `sec_type` to
  `"FUT" | "FOP"` and **requires** `limit_price > 0`, `qty ≤ 1000`.
- `OrderRequest` (`order_executor.py:60`) mirrors that; `place_order` builds a
  generic `ib_insync.Contract`, qualifies it, always places a `LimitOrder`.

**Cash holdings path (exists, needs NO change):**

```
IB account values (CashBalance per currency)
  → OrderExecutor.account_summary().by_currency   (order_executor.py:171)
  → position_sync loop → account_history.currencies JSONB
  → GET /api/v1/portfolio/cash                    (api/routers/portfolio_panel.py:1655)
  → CashHoldings component                        (frontend PositionsTable.tsx:440)
```

A real spot fill changes IB's per-currency `CashBalance` (buy EUR.USD:
EUR += qty, USD -= qty × price). The next account snapshot picks it up and the
panel updates by itself. The position mirror also already maps IB secType
`CASH` → `SPOT` (`core/payloads.py:171`), so a net spot position shows up in
the IB mirror without schema work.

**Spot contract:** IB models EUR/USD spot as `Contract(symbol="EUR",
secType="CASH", exchange="IDEALPRO", currency="USD")` (= `Forex("EURUSD")`,
already used read-only by the market-data engine). Quantity is EUR notional
units (e.g. 100 000 = 100k EUR); IDEALPRO's practical minimum is ~25 000.

## Changes to apply

### Backend — execution engine

1. `src/engines/execution/order_executor.py`
   - `OrderRequest.sec_type`: allow `"CASH"`; `limit_price: float | None`
     (None ⇒ market order).
   - `place_order`: place `MarketOrder` when `limit_price is None`,
     `LimitOrder` otherwise (both already imported patterns in this file).
2. `src/engines/execution/main.py` — `PlaceOrderBody`:
   - `sec_type: Literal["FUT", "FOP", "CASH"]`.
   - `limit_price: float | None = Field(None, gt=0)` — market when omitted.
   - qty bound: keep `≤ 1000` for FUT/FOP but allow spot notionals
     (validator: CASH ⇒ `qty ≤ 5_000_000`, others ⇒ `qty ≤ 1000`;
     non-CASH still requires `limit_price`).

### Backend — API tier

3. `src/api/routers/orders.py` — **no change** (pure dict proxy, already
   auth-gated with `require_write`).

### Frontend

4. `frontend/src/api/endpoints.ts` — add `postSpotOrder(body)` posting to
   `/api/v1/orders` (same `apiPost` pattern as the other write helpers).
5. `frontend/src/voldesk/views/TradeView.tsx` — new small `SpotPanel`:
   - live bid/ask (reuses the `spotBid`/`spotAsk` already computed in the view);
   - qty input in EUR (default 100 000);
   - BUY / SELL buttons → market order `{symbol:"EUR", sec_type:"CASH", side,
     qty, exchange:"IDEALPRO", currency:"USD"}`;
   - write-gated like the rest of the desk (login required), errors surfaced
     in the panel + a blotter line via the existing `addOrder`;
   - rendered in the third `.trade-top` column, stacked ABOVE the
     "Close position" panel (flex column wrapper — no CSS file change).

### Tests

6. Unit test: `PlaceOrderBody` accepts CASH+no-limit / rejects FOP without
   limit; `OrderRequest`→contract/order building for CASH (market order).

## Non-goals (MVP)

- No persistence into `trade_structure`/`trade_order` (a raw spot order is not
  a vol structure); it is audit-logged via the existing `order_events` SUBMIT
  path and visible in the Orders tab via IB open-orders until filled.
- No limit-order UI for spot (market only — the panel is for cash management,
  not price improvement); the API accepts an optional limit for operators.
- EUR/USD only.

## Verification

- `python -m pytest tests/unit/engines/execution` + ruff + import-linter.
- Frontend: lint + typecheck + vitest + build.
- Manual (paper): buy 100k EUR.USD from the panel → order fills at IB →
  within one account-snapshot cycle the Cash holdings EUR row increases by
  100k and USD decreases by ~100k × spot; the IB mirror shows a SPOT line.
