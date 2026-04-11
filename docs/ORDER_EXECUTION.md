# Order Execution

## Overview

Three instrument types share a single execution pipeline: **Spot**, **Future**, **Option**. All orders flow through the same UI signal, controller routing, and `OrderExecutor` class. Orders are executed synchronously on the main thread via the shared IB connection (client_id=1).

## Instruments

| Instrument | IB Contract | Exchange | Multiplier | Order Type |
|---|---|---|---|---|
| Spot FX | `Forex("EURUSD")` | IDEALPRO | 1 (cash) | MKT |
| Future | `Contract(secType="FUT", symbol="EUR")` | CME | 125,000 | MKT |
| Option | `Contract(secType="FOP", tradingClass="EUU")` | CME | 125,000 | MKT |

## Order Flow

```
User clicks "Book (Preview)"
    │
    ├── Market closed check (spot <= 0) → "Market is closed." error
    │
    ▼
OrderTicketPanel emits order_preview_requested(order_dict)
    │
    ▼
Controller._on_order_preview_requested(order)
    │
    ├── instrument == "Spot"   → OrderExecutor.preview_order()     [Forex]
    ├── instrument == "Future" → OrderExecutor.preview_future_order()  [6E CME]
    ├── instrument == "Option" → OrderExecutor.preview_option_order()  [EUU FOP]
    │
    ▼
IB whatIf response (margin, commission, greeks)
    │
    ▼
OrderConfirmDialog shown to user
    │
    ├── Cancel → nothing
    ├── Confirm → Controller._place_confirmed_order()
    │               │
    │               ├── Spot   → OrderExecutor.place_order()
    │               ├── Future → OrderExecutor.place_future_order()
    │               ├── Option → OrderExecutor.place_option_order()
    │               │
    │               ├── If option + delta_hedge checked:
    │               │   → auto-compute hedge future qty from option delta
    │               │   → OrderExecutor.place_future_order(hedge)
    │               │
    │               ▼
    │           Wait for fill/rejection (10s timeout, 200ms poll)
    │               │
    │               ▼
    │           Feedback message in Order Ticket panel
```

## Spot Order

- Contract: `Forex(symbol)` on IDEALPRO
- Quantity: base currency units (e.g., 25,000 EUR)
- Notional preview: `25,000 EUR -> 27,135 USD` (sell) or `27,135 USD -> 25,000 EUR` (buy)
- Validation: symbol length >= 6, side in BUY/SELL, quantity > 0

## Future Order

- Contract: front quarterly 6E future on CME, resolved via `reqContractDetails`
- Quantity: number of contracts (each = 125,000 EUR notional)
- Preview shows: notional (mid x qty x 125k), delta (mid x qty x 125k x sign)
- `_resolve_front_future()`: finds the nearest quarterly expiry (Mar/Jun/Sep/Dec) with DTE > 7

## Option Order

- Contract: EUU FOP on CME, resolved via `_resolve_fop_contract()`
- Fields: side, right (C/P), tenor, strike, quantity
- Tenor is resolved to expiry date from `config/fop_expiries.json`
- Preview includes: bid, ask, mid, IV, delta/gamma/vega/theta in USD, margin, commission

### Delta Hedge

When the "Delta hedge" checkbox is checked:

1. Option preview returns `delta_usd`
2. Hedge quantity = `round(|delta_usd| / (mid_spot x 125,000))`
3. Hedge side = opposite of option delta (SELL if delta > 0, BUY if delta < 0)
4. Both orders shown in a combined preview dialog with "Net Position" section
5. On confirm: option order placed first, then hedge future order

## Preview Dialog

`OrderConfirmDialog` shows IB whatIf data:

| Field | Source |
|---|---|
| Contract | IB localSymbol |
| Side / Quantity | User input |
| Bid / Ask / Mid | Live market data (options only) |
| IV | IB modelGreeks (options only) |
| Delta / Gamma / Vega / Theta (USD) | Computed: greek x qty x multiplier |
| Notional (USD) | mid x qty x multiplier |
| Init Margin / Maint Margin | IB whatIfOrder |
| Commission | IB whatIfOrder |
| Equity Change | IB whatIfOrder |

## Error Handling

- Market closed: blocked at UI level before signal emission
- IB not connected: checked in OrderExecutor before any API call
- Contract not found: `reqContractDetails` returns empty → error message
- Order rejected: IB status in {APICANCELLED, CANCELLED, INACTIVE} → error with IB reason
- whatIf failure: IB returns None → error with `get_last_error_text()`
- Fill timeout: 10s max wait, reports final status if not FILLED

## Files

| File | Role |
|---|---|
| `src/ui/panels/order_ticket_panel.py` | UI: Spot/Future/Option forms, preview trigger, market closed guard |
| `src/services/order_executor.py` | Backend: normalize, validate, preview, place, wait for fill |
| `src/controller.py` | Router: `_on_order_preview_requested`, `_place_confirmed_order`, delta hedge logic |
