# UI Architecture

## Window

Title: "FX Options Trading Dashboard"
Framework: PyQt5
Charts: pyqtgraph (GPU-accelerated, Qt-native)

## Layout — 3 Columns

```
Col 1 (365px fixed)          Col 2 (1500px fixed)                              Col 3 (450px fixed)
────────────────────         ──────────────────────────────────────────         ────────────────────────
Runtime Status               [Tick Chart] [Term Structure] [Smile] [Details]   Order Ticket
  connection/engine            (4 charts side by side, max 375px height)         Spot order
  Start/Stop buttons                                                             [Future] [Option]
  Settings button            Vol Scanner (table)                                 feedback label
                                                                               Greeks Summary
Account Summary              Open Positions (table)                              (5 lines, PnL bold)
  Net Liq, Cash, PnL                                                           PnL vs Spot
  Currencies (USD/EUR)                                                           (pyqtgraph chart)

Logs
  level/source filters
  searchable text area
```

## Panel Files — 11 panels + 1 dialog

All files follow the naming convention `<name>_panel.py` in `src/ui/panels/`.

### Col 1 — Left

| File | Class | Data Source | Refresh |
|---|---|---|---|
| `runtime_status_panel.py` | `StatusPanel` | Thread 1 + QTimer 1s | 1s |
| `account_summary_panel.py` | `PortfolioPanel` | Thread 1 (account snapshot) | 10s |
| `logs_panel.py` | `LogsPanel` | Thread 1 (messages) | 100ms |

### Col 2 — Center

| File | Class | Data Source | Refresh |
|---|---|---|---|
| `tick_chart_panel.py` | `ChartPanel` | Thread 1 (ticks) | 200ms bucket |
| `term_structure_panel.py` | `TermStructurePanel` | Thread 2 (vol engine) | ~180s |
| `smile_chart_panel.py` | `SmileChartPanel` | Thread 2 (vol engine) | ~180s |
| `smile_chart_panel.py` | `SmileDetailsPanel` | SmileChartPanel (tenor select) | on click |
| `vol_scanner_panel.py` | `VolScannerPanel` | Thread 2 (vol engine) | ~180s |
| `book_panel.py` | `OpenPositionsPanel` | Thread 3 (risk engine) | 2s |

### Col 3 — Right

| File | Class | Data Source | Refresh |
|---|---|---|---|
| `order_ticket_panel.py` | `OrderTicketPanel` | Thread 1 (bid/ask) | 2s throttle |
| `book_panel.py` | `BookPanel` | Thread 3 (risk engine) | 2s |
| `pnl_chart_panel.py` | `PnlSpotPanel` | Thread 3 (risk engine) | 2s |

### Dialog (on demand)

| File | Class | Trigger |
|---|---|---|
| `settings_panel.py` | `SettingsPanel` | "Settings" button in RuntimeStatusPanel |

## Panel Details

### RuntimeStatusPanel — Col 1

Two group boxes:
- **Runtime Status**: connection dot/label + Start/Stop, engine dot/label + Start/Stop, form rows (Mode, Env, Latency, Server time, ClientId, Account)
- **Connection Settings**: Host, Port, ClientId (read-only QLabel), Settings button opens dialog

### AccountSummaryPanel — Col 1

Form layout showing IB account data:
- Net Liq, Cash, Available, Buying Power, Unrealized PnL, Realized PnL, Gross Pos (all in k with currency suffix: `1,250.0 USD/k`)
- Open positions count
- Currencies sub-box: USD and EUR balances in k format

### LogsPanel — Col 1

- Filter controls: Level combo (ALL/INFO/WARN/ERROR), Source combo, text search
- QTextEdit with HTML-formatted entries, max 4000 entries, auto-scroll
- Parses `[LEVEL][source]` prefix pattern

### ChartPanel — Col 2

- pyqtgraph: bid (blue) and ask (red) curves
- Symbol combo box (12 FX pairs)
- 30s sliding window, 200ms bucket aggregation
- Circular numpy buffer (O(1) append)
- Internal QTimer 200ms for bucket flush

### TermStructurePanel — Col 2

- pyqtgraph: 3 curves — IV Market (blue), sigma Fair (green), RV (orange dashed)
- FillBetweenItem: red if IV > fair, green if IV < fair
- GroupBox title: "Term Structure"

### SmileChartPanel + SmileDetailsPanel — Col 2

- SmileChartPanel: tenor selector combo + pyqtgraph smile plot per tenor
- SmileDetailsPanel: QTableWidget with numerical smile details for selected tenor
- Linked via `set_details_panel()`

### VolScannerPanel — Col 2

- QTableWidget: 1 row per tenor (6 tenors)
- Columns: Tenor, DTE, sigma Mid, sigma Fair, Ecart, Signal, RV, RR25, BF25
- Signal color-coded: CHEAP (green), EXPENSIVE (red), FAIR (grey)

### OpenPositionsPanel — Col 2

- QTableWidget: 1 row per open position
- Columns: Symbol, Side, Qty, Tenor, Strike, Right, Fill Price, IV Now %, Delta, Vega, Gamma, Theta, PnL
- When market closed: basic data shown, greeks/IV/PnL display "--"

### OrderTicketPanel — Col 3

Structure: 1 column, Spot on top, Future and Option side by side below.

```
Order Ticket
  Spot
    Symbol: EURUSD          Type: MKT
    Side: [BUY]             Qty: [25000]
            25,000 USD -> 25,000 EUR
               [Book (Preview)]
  [Future]              [Option]
    Symbol                Symbol
    Side / Qty            Side / Right
    Type / Contract       Expiry / Strike
    Notional / Delta      Qty / Delta hedge
    [Book (Preview)]      [Book (Preview)]
  [feedback message]
```

- Spot: MKT order on IDEALPRO via `Forex(symbol)`, notional shows `X EUR -> Y USD` (sell) or `Y USD -> X EUR` (buy)
- Future: MKT order on 6E CME (125k multiplier), shows notional + delta
- Option: MKT order on EUU FOP CME, optional delta hedge with auto-computed future qty
- All 3 Book buttons check market open (spot > 0), show "Market is closed." if not
- Preview dialog (`OrderConfirmDialog`) shows IB whatIf data, option greeks, net position for hedged orders

### BookPanel (Greeks Summary) — Col 3

5 QLabel lines:
- Delta Net, Vega Net (formatted +/-X.Xk)
- Gamma Net, Theta Net (formatted +/-X,XXX.XX)
- PnL Total (bold, larger font)
- All colored green (positive) / red (negative)

### PnlSpotPanel — Col 3

- pyqtgraph: PnL curve as function of spot (+/-3% range, 31 points)
- Green fill above zero, red fill below zero
- Red vertical line + dot at current spot with PnL annotation
- Data pre-computed by Thread 3 (RiskEngine), vectorized BS via numpy
- Panel only renders, no computation

### SettingsPanel — Dialog

- QDialog, scrollable form, 4 sections:
  - **Connection**: Host, Port, Client ID
  - **Model**: W1/W2 slider, Signal Threshold, Alpha Book, Risk Premium per tenor (1M-6M)
  - **Scan**: Wait Greeks, Loop Interval, n_side_short/long, GARCH Duration, Target DTEs
  - **Filters**: RR25/BF25 limits, IV Arb Threshold, Vega Limits per tenor
- Badges: CRITICAL (red), MEDIUM (orange), LOW (green)
- Buttons: Reset Defaults, Cancel, Save
- Saves to `config/status_panel_settings.json` + `config/vol_config.json`

## Signal Wiring

```
ChartPanel.market_symbol_input.currentTextChanged
    -> OrderTicketPanel.set_symbol()

ChartPanel -> OrderTicketPanel
    set_bid_offer_label()       (shared QLabel reference)
    set_on_price_update()       (bid/ask callback)

OrderTicketPanel.order_preview_requested
    -> controller._on_order_preview_requested()
    -> OrderExecutor.preview_*() / place_*()

StatusPanel buttons
    Start (connect)   -> controller._start_connect()
    Stop (disconnect)  -> controller._disconnect_client()
    Start (engine)     -> controller._start_engine_pool()
    Stop (engine)      -> controller._stop_engine_pool()
    Settings           -> controller._open_settings()

SettingsPanel.accepted
    -> controller._on_settings_saved()

MainWindow.window_closed
    -> controller._shutdown_services()
```

## Thread -> Panel Data Flow

```
Thread 1 — MarketDataEngine (100ms)
    chart_panel           ticks (bid/ask)
    order_ticket_panel    bid/ask quote (2s throttle)
    status_panel          connection state, latency, account
    portfolio_panel       account summary (10s)
    logs_panel            messages

Thread 2 — VolEngine (180s, skipped if market closed)
    vol_scanner_panel     scanner rows
    term_structure_panel  IV vs fair per tenor
    smile_chart_panel     smile surface per tenor

Thread 3 — RiskEngine (2s greeks, 10s positions fetch)
    open_positions_panel  per-position greeks + PnL
    book_panel            greeks summary (delta/vega/gamma/theta/PnL)
    pnl_chart_panel       pre-computed PnL vs Spot curve
```
