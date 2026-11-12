# Open positions (Trade view)

**Purpose of the panel:** show what the desk holds, grouped by the trade that
opened it, with a Close button per leg and per trade.

**What it does instead:** it shows the legs our *book* believes are open inside
the trades that still own something at the broker. Today that is 8 rows for 7
holdings: one row is a leg the broker no longer has.

Part of [the position-panels analysis](README.md).

## Pipeline

```mermaid
flowchart TD
    UI["Trade view: Place order"] -->|"POST /trade/submit"| API1["api · trade.py"]
    API1 -->|"INSERT"| TS[("trade_structure")]
    API1 -->|"INSERT, role = entry"| TO[("trade_order")]

    IB[("IB Gateway")] -->|"fill events"| FH["execution engine · fills_handler"]
    FH -->|"INSERT"| TF[("trade_fill")]
    FH -->|"rebuild_leg: sum of THIS order's fills"| LP[("leg_position")]
    FH -->|"all entry legs filled"| BP[("booked_position")]

    IB -->|"positions(), every 5 s"| PS["execution engine · position_sync"]
    PS -->|"UPSERT / DELETE by local symbol,<br/>sets trade_id = newest trade naming that contract"| OP[("open_position")]
    PS -->|"PUBLISH positions"| RD{{"Redis"}}
    RK["risk engine"] -->|"greeks, mark, P&L every 60 s"| OP

    TS --> EP
    TO --> EP
    LP --> EP
    OP --> EP["GET /positions/structured<br/>api · positions.py:367"]
    EP --> FE["structuredToRows<br/>TradeView.tsx:215"]
    RD -.->|"/ws/positions push, reload after 300 ms"| FE
    FE --> TB["OpenPositionsTable<br/>grouped by trade"]

    classDef tbl fill:#fff7e6,stroke:#c98a00,color:#111
    class TS,TO,TF,LP,BP,OP tbl
```

| Step | Where | Cadence |
|---|---|---|
| Trade and legs written | `src/api/routers/trade.py:985`, `:1086` | on submit |
| Fill recorded, leg quantity rebuilt | `src/engines/execution/fills_handler.py:224`, `src/persistence/projection.py:21` | per fill; full rebuild at engine start |
| Mirror of IB holdings | `src/engines/execution/position_sync.py:107` | every 5 s (`docker-compose.yml:443`) |
| Greeks, mark, P&L on the mirror | `src/engines/risk/engine.py:398` | every 60 s (30th cycle of a 2 s loop) |
| Endpoint | `src/api/routers/positions.py:367` | on request |
| Frontend poll | `TradeView.tsx:862` | 60 s, plus a reload on every positions push |

## How the endpoint picks its rows

```mermaid
flowchart TD
    A["trade_structure rows"] --> B{"state != 'closed'?"}
    B -->|"always true:<br/>nothing ever writes 'closed'"| C["newest 50 by created_at"]
    C --> D["all their trade_order legs<br/>(entry AND closing)"]
    M["every open_position row"] --> E{"row.trade_id is one of the 50?"}
    E -->|yes| F["attach to that trade"]
    E -->|no| G{"its symbol is a leg<br/>of one of the 50?"}
    G -->|yes| F
    G -->|no| U["unlinked[]"]
    D --> H{"trade owns at least<br/>one mirror row?"}
    F --> H
    H -->|no| X["trade dropped"]
    H -->|yes| I["structure returned<br/>with ALL its legs"]
    I --> J{"leg has a mirror row<br/>OR book open_qty != 0?"}
    J -->|yes| K["row on screen"]
    J -->|no| L["leg hidden"]
    U --> K
```

Three rules matter:

1. **Trade selection** (`positions.py:389-390`, `:519`): the newest 50 trades,
   then only those owning a mirror row. A trade outside the window is never
   shown; its holdings fall into `unlinked` if their symbol is not in the
   window either.
2. **Leg selection** (`positions.py:424-429`, `TradeView.tsx:232`): a leg is a
   row when the broker holds it **or the book says its quantity is not zero**.
3. **Ownership** (`position_sync.py:409-447`): a held contract belongs to the
   newest trade that ever had an entry leg on it. Direction and whether that
   trade is still alive are not checked.

## Why trade 60 shows a leg nobody holds

```mermaid
sequenceDiagram
    participant U as Trader
    participant API as api
    participant X as execution engine
    participant IB as IB
    participant DB as Postgres

    Note over U,DB: 23 July: trade 60, long strangle
    U->>API: submit BUY 100 P1075 + BUY 100 C1210
    API->>DB: trade_structure 60, orders 75 and 76 (entry)
    IB-->>X: fills
    X->>DB: leg_position: order 75 = +100, order 76 = +100

    Note over U,DB: later: Close on the put row
    U->>API: POST /positions/{id}/close
    API->>DB: NEW trade_structure 85 "vanilla_put",<br/>order 106 (closing, closes_order_id = 75)
    IB-->>X: fill SELL 100 P1075
    X->>DB: leg_position: order 106 = -100
    Note over DB: order 75 stays at +100.<br/>The link is only used for reserved_qty.

    IB-->>X: positions(): P1075 gone, C1210 = 100
    X->>DB: open_position: delete P1075 row

    Note over U,DB: today
    API->>DB: GET /positions/structured
    Note over API: trade 60 owns C1210, so it is returned.<br/>Its put leg has open_qty = +100, so open = true,<br/>with no mirror row: linked = false.
    API-->>U: 2 legs: call (live values) + put (zeros, Close disabled)
```

Prod evidence (`trade_order` joined to `leg_position`):

| Trade | Order | Role | Closes | Contract | Side | Qty | Book open qty |
|---|---|---|---|---|---|---|---|
| 60 | 75 | entry | - | EUUZ6 P1075 | BUY | 100 | **+100** |
| 60 | 76 | entry | - | EUUZ6 C1210 | BUY | 100 | +100 |
| 85 | 106 | closing | 75 | EUUZ6 P1075 | SELL | 100 | **-100** |

The close is correctly linked (106 closes 75), yet order 75 still reads +100:
`rebuild_leg` sums only the fills of its own order
(`src/persistence/projection.py:23-29`).

## Other things this panel gets wrong

| Symptom | Cause | Where |
|---|---|---|
| Spot short shown as "Future - 6E", entry 0.00000887, no mark, no P&L | `EUR.USD` matches no contract pattern: multiplier defaults to 125,000 (`avgCost / 125000`), label falls to the future default, the risk engine skips it | `position_sync.py:156-163`, `src/core/products.py:66-75`, `risk/engine.py:272-274` |
| Expiry and days-to-expiry differ from the contract's | the leg carries the preview's calendar date (`today + tenor`); IB resolves to the listed expiry and nothing writes it back | `src/core/trade_preview.py:488`, `contract_builder.py:48-63` |
| Tenor is the contract's, expiry is the booked one, on the same row | `tenor` comes from the mirror, `expiry` from the leg | `positions.py:459`, `:481`; `TradeView.tsx:260-265` |
| A trade older than the 50 newest disappears from the panel while still held | `limit` defaults to 50 and the frontend sends none | `positions.py:367`, `frontend/src/api/endpoints.ts:334` |
| The "closed" filter does nothing | no code sets `trade_structure.state = 'closed'` | `positions.py:389` |

Booked versus real expiry, prod:

| Trade | Booked expiry (`trade_order`) | Contract expiry (`open_position`) |
|---|---|---|
| 60 call | 2026-12-20 | 2026-12-04 |
| 79 legs | 2027-02-02 | 2027-02-05 |
| 87 put | 2027-02-03 | 2027-02-05 |
| 88 future | 2027-02-03 | 2027-03-15 |

## What "same lines as the other panels" needs here

- The row set comes from the shared position set (one line per contract held),
  not from book legs. A leg the book holds and the broker does not becomes a
  **flagged** line that the other two panels show too.
- The 50-trade window stops deciding what is visible: visibility follows
  holdings, not creation date.
- Expiry shown is the contract's; the booked date is an entry detail.
