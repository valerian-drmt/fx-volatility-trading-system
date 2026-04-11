# FX Options Trading Dashboard

**Real-time FX volatility analytics and order execution platform** built on Interactive Brokers API.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-41CD52?logo=qt&logoColor=white)
![ib-insync](https://img.shields.io/badge/ib--insync-IBKR%20API-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Single-screen dashboard for FX options traders: **implied vol vs fair vol signals**, **real-time portfolio greeks**, **PnL simulation**, and **multi-instrument order execution** (Spot, Future, Option) with live IB Gateway connectivity.

---

## Features

### Market Data & Execution
- Real-time FX spot tick chart with bid/ask (pyqtgraph, 200ms buckets)
- **3 order types** in a single ticket: Spot (IDEALPRO), Future (6E CME), Option (EUU FOP CME)
- Order preview with IB whatIf (margin, commission, greeks) before submission
- Option delta-hedge: auto-computed future quantity for delta-neutral entry
- Account summary with Net Liq, Cash, Unrealized PnL, currency balances

### Volatility Analytics
- **Vol Scanner** -- market IV vs model fair vol per tenor, CHEAP/EXPENSIVE/FAIR signals
- **Term Structure** -- IV market vs sigma fair vs Realized Vol across 6 tenors (1M-6M)
- **Smile Chart** -- delta-space smile (10Dp, 25Dp, ATM, 25Dc, 10Dc) per tenor
- **Greeks Summary** -- aggregated Delta, Vega, Gamma, Theta, PnL across all open positions
- **PnL vs Spot** -- simulated portfolio PnL curve over +/-3% spot range (vectorized BS)

### Quantitative Models
- **IV Surface (Step 1)** -- FOP chain scan, BS IV extraction via IB model greeks, PCHIP delta-space interpolation, RR/BF derivation, validation gates per tenor
- **Fair Vol (Step 2)** -- Yang-Zhang realized vol + dynamic risk premium, GARCH(1,1) forward projection with mean-reversion blend, conditional W1 weighting, portfolio book adjustment
- **Signal** -- `sigma_fair(T) - sigma_mid(T)` thresholded at +/-20bps

---

## Architecture

### Threading Model -- Main + 3 Worker Threads

```
Main Thread (Qt)               Thread 1                Thread 2              Thread 3
UI rendering                   MarketDataEngine        VolEngine             RiskEngine
Order execution                IB ticks (100ms)        IV scan (180s)        Positions (10s)
QTimer 50ms + 1s               Account (10s)           GARCH + RV            BS greeks (2s)
                               Status snapshot         PCHIP interpolation   PnL chart (2s)

IB client_id=1 (shared)        IB client_id=1          IB client_id=2        IB client_id=3
```

- **Thread 1** polls IB ticks and pushes to UI via queue. Writes spot to Thread 3.
- **Thread 2** runs the full vol pipeline every 180s. Writes IV surface to Thread 3. Skipped if market is closed.
- **Thread 3** fetches positions from IB, computes BS greeks and PnL chart on its own thread (no main thread bottleneck).
- **Main Thread** only renders UI and handles order clicks. Zero scipy, zero BS computation.

Communication: `queue.Queue` (thread-safe) + atomic attribute writes (CPython GIL). No locks.

### Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| GUI | PyQt5 + pyqtgraph |
| IB connectivity | ib_insync |
| Concurrency | threading.Thread (3 workers) + QTimer (2 pollers) |
| Vol models | scipy (PCHIP, norm.cdf), arch (GARCH), numpy |
| Option pricing | Black-Scholes (custom bs_pricer.py) |
| Testing | pytest (unit + integration) |
| Linting | ruff |

---

## Quickstart

**Prerequisites:** Python 3.11, IB Gateway or TWS with API enabled (paper: port `4002`)

```bash
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .\.venv\Scripts\Activate.ps1     # Windows PowerShell

pip install -r requirements.txt
python app.py
```

1. Click **Start** to connect to IB Gateway
2. Click **Start Engine** to launch all 3 threads (ticks, vol scan, risk)
3. Use the **Order Ticket** to trade Spot, Future, or Option

---

## Testing

```bash
python -m pytest                                  # full suite
python -m pytest tests/test_controller_settings.py -v  # single file
python -m ruff check src tests app.py             # lint
python -m compileall -q src app.py                # import check
```

Integration tests (requires live IB Gateway):
```bash
IB_RUN_INTEGRATION=1 python -m pytest -m integration -rs
```

---

## Project Structure

```
trading-ib/
├── app.py                              # Entry point (logging + asyncio/Qt setup)
├── src/
│   ├── controller.py                   # App lifecycle, engine pool, signal wiring
│   ├── services/
│   │   ├── market_data_engine.py       # Thread 1: tick polling + account snapshots
│   │   ├── vol_engine.py              # Thread 2: IV scan + GARCH + fair vol
│   │   ├── risk_engine.py            # Thread 3: positions + greeks + PnL chart
│   │   ├── order_executor.py          # Order preview + placement (Spot/Future/Option)
│   │   ├── ib_client.py              # IB API wrapper
│   │   └── bs_pricer.py              # Black-Scholes functions + IV interpolation
│   └── ui/
│       ├── main_window.py             # 3-column layout
│       └── panels/
│           ├── runtime_status_panel.py     # Connection + engine status
│           ├── account_summary_panel.py    # Net Liq, Cash, currencies
│           ├── logs_panel.py              # Filtered log viewer
│           ├── tick_chart_panel.py         # Live bid/ask chart
│           ├── term_structure_panel.py     # IV term structure
│           ├── smile_chart_panel.py        # Smile per tenor
│           ├── vol_scanner_panel.py        # Vol scanner table
│           ├── book_panel.py              # Greeks summary + open positions
│           ├── order_ticket_panel.py       # Spot/Future/Option order entry
│           ├── pnl_chart_panel.py         # PnL vs Spot simulation
│           └── settings_panel.py          # Config dialog (vol params)
├── config/
│   ├── status_panel_settings.json      # Connection + runtime settings
│   ├── vol_config.json                # Vol engine parameters
│   ├── fop_expiries.json             # Cached FOP expiry data
│   └── fop_strikes.json              # Cached FOP strike data
├── scripts/                            # Jupyter notebooks for standalone exploration
│   ├── vol_mid.ipynb                  # IV surface extraction
│   ├── vol_fair.ipynb                 # Fair vol model
│   ├── option_booking.ipynb           # FOP order test
│   ├── future_booking.ipynb           # Future order test
│   ├── list_fop_strikes.ipynb         # Strike discovery
│   └── list_fop_expiries.ipynb        # Expiry discovery
├── docs/
│   ├── THREADS.md                     # Threading architecture
│   ├── UI.md                          # UI layout and panel specs
│   ├── VOL_MODEL.md                   # Vol model math documentation
│   ├── ORDER_EXECUTION.md             # Order flow and execution pipeline
│   └── CONFIG.md                      # Configuration parameters reference
└── tests/
```

---

## Documentation

| Document | Content |
|---|---|
| [docs/THREADS.md](docs/THREADS.md) | Threading architecture: 3 workers, engine pool, communication, lifecycle |
| [docs/UI.md](docs/UI.md) | UI layout: 3-column grid, 11 panels, signal wiring, data flow |
| [docs/VOL_MODEL.md](docs/VOL_MODEL.md) | Volatility model: PCHIP interpolation, Yang-Zhang RV, GARCH(1,1), signal generation, Greeks/PnL decomposition |
| [docs/ORDER_EXECUTION.md](docs/ORDER_EXECUTION.md) | Order flow: Spot/Future/Option pipeline, preview, delta hedge, error handling |
| [docs/CONFIG.md](docs/CONFIG.md) | Configuration parameters: vol engine, connection, risk limits, Settings dialog |

---

## License

MIT
