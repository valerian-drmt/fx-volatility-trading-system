# FX Options Trading Dashboard

**Real-time FX volatility analytics and order execution platform** built on Interactive Brokers API.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-41CD52?logo=qt&logoColor=white)
![ib-insync](https://img.shields.io/badge/ib--insync-IBKR%20API-blue)
![CI](https://img.shields.io/github/actions/workflow/status/valerian-drmt/trading-ib/tests.yml?label=CI)
![License](https://img.shields.io/badge/license-MIT-green)

Designed for FX options traders who need a single-screen view of **implied vol vs fair vol**, **portfolio greeks**, and **order execution** with real-time IB Gateway connectivity.

---

## Key Features

### Live Market Data & Execution
- Real-time FX spot tick streaming with bid/ask chart (pyqtgraph)
- **FX spot orders:** Market, Limit, and LMT bracket orders with TP/SL
- **FX options orders:** Buy/Sell vanilla Call and Put on EUR CME futures options (FOP)
- One-click cancel per order, portfolio snapshot, open orders & recent fills
- Dynamic symbol switching with automatic stream restart

### Volatility Analytics Engine (dedicated compute thread)
- **Vol Scanner** — real-time table comparing market IV vs model fair vol per strike and tenor, with CHEAP/EXPENSIVE/FAIR signals and color-coded opportunities
- **Term Structure** — IV market vs sigma_fair vs Realized Vol curves across tenors (1W to 2Y), with opportunity zone highlighting
- **Smile Chart** — market smile vs fair smile by delta pillar (10Dp to 10Dc) per selected tenor
- **Portfolio Greeks** — aggregated Delta, Vega, Gamma, Theta across all open FOP positions with delta hedge suggestion

### Quantitative Models
- **Implied Vol (Step 1)** — BS inversion on EUR CME futures options via IB tick 100, liquidity filtering, put-call parity mid IV, delta-pillar reconstruction (10D/25D/ATM), RR and BF derivation
- **Fair Vol (Step 2)** — three-layer model combining Yang-Zhang realized vol + historical risk premium, GARCH(1,1) forward vol with mean-reversion, and portfolio-aware book adjustment (delta_book)
- **Signal generation** — `sigma_fair(T) = W1*(RV+RP) + W2*sigma_GARCH + delta_book` vs market IV, thresholded at +/-20bps

---

## Architecture

```
IB Gateway / TWS
       |
       v
  asyncio + Qt event loop (Thread 1)
       |
       +---> ib_client.py          IB API wrapper (single connection)
       |        |
       |        +---> MarketDataWorker    Tick polling (QTimer, 100ms)
       |        +---> OrderWorker         Order execution (direct calls)
       |        +---> Chart / Orders / Portfolio / Status panels
       |
       +---> queue.Queue ----------> Vol Engine (Thread 2)
                                        |
                                        +---> vol_mid.py      Step 1: IV collection + delta pillars
                                        +---> vol_fair.py     Step 2: RV + GARCH + book = sigma_fair
                                        +---> yang_zhang.py   Realized vol estimator
                                        +---> garch.py        GARCH(1,1) calibration
                                        +---> greeks.py       Portfolio greeks aggregation
                                        |
                                        +---> Vol Scanner / Term Structure / Smile / Greeks panels
```

**Thread 1 (main):** asyncio event loop integrated with Qt via `ib_insync.util.useQt()`. Handles all IB network I/O, UI rendering, and tick streaming. Single IB connection, zero locks.

**Thread 2 (vol engine):** Dedicated `threading.Thread` for CPU-bound volatility calculations (Black-Scholes inversion, GARCH MLE, Yang-Zhang estimator). Communicates with Thread 1 via producer-consumer `queue.Queue`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| GUI | PyQt5 + pyqtgraph |
| IB connectivity | ib_insync (asyncio, single-threaded) |
| Concurrency | asyncio (I/O) + threading.Thread (CPU-bound vol engine) |
| Quant models | scipy (BS inversion), arch (GARCH), numpy |
| Option pricing | QuantLib (available) |
| Testing | pytest |
| Linting / CI | ruff, GitHub Actions |

---

## Quickstart

**Prerequisites:** Python 3.11, IB Gateway or TWS with API enabled (paper: port `4002`)

```bash
python -m venv .venv
```

Activate:
```bash
source .venv/bin/activate          # macOS / Linux
```
```powershell
.\.venv\Scripts\Activate.ps1       # Windows PowerShell
```

Install and run:
```bash
pip install -r requirements.txt
python app.py
```

---

## Tests

```bash
# Full test suite
python -m pytest

# Single test file
python -m pytest tests/test_order_worker.py -v

# Lint
python -m ruff check src tests app.py
```

Integration tests (requires live IB Gateway):
```bash
IB_RUN_INTEGRATION=1 IB_HOST=127.0.0.1 IB_PORT=4002 IB_CLIENT_ID=3 python -m pytest -rs
```

**CI gates:** `compileall` import check, `ruff` linting, unit tests.

---

## Project Structure

```
trading-ib/
+-- app.py                          # Entry point (asyncio + Qt event loop)
+-- src/
|   +-- controller.py               # App lifecycle, service orchestration
|   +-- services/
|   |   +-- ib_client.py            # IB API wrapper
|   |   +-- market_data_worker.py   # Tick polling (called by QTimer)
|   |   +-- order_worker.py         # Order validation + execution
|   |   +-- vol_engine.py           # Thread 2: vol calculation orchestrator
|   +-- analytics/
|   |   +-- vol_mid.py              # Step 1: IV collection + delta pillars
|   |   +-- vol_fair.py             # Step 2: RV + GARCH + book -> sigma_fair
|   |   +-- yang_zhang.py           # Yang-Zhang realized vol
|   |   +-- garch.py                # GARCH(1,1) calibration
|   |   +-- greeks.py               # Portfolio greeks aggregation
|   +-- ui/
|       +-- main_window.py          # Grid layout
|       +-- panels/
|           +-- status_panel.py     # Connection state, latency
|           +-- chart_panel.py      # Live tick chart + symbol selector
|           +-- order_ticket_panel.py
|           +-- orders_panel.py     # Open orders + fills with cancel
|           +-- portfolio_panel.py
|           +-- logs_panel.py
|           +-- vol_scanner_panel.py
|           +-- term_structure_panel.py
|           +-- smile_panel.py
|           +-- greeks_panel.py
+-- config/
|   +-- status_panel_settings.json
+-- docs/
|   +-- vol_engine_implementation.md  # Detailed vol engine specs
+-- tests/
+-- vol_mid_step1.py                # Standalone Step 1 script
+-- vol_fair_step2.py               # Standalone Step 2 script
```

---

## Volatility Model Documentation

See [`docs/vol_engine_implementation.md`](vol_engine_implementation.md) for the complete implementation guide covering:
- Step 1 & Step 2 data pipelines
- IB API calls and data flow
- Panel specifications and update frequencies
- Threading architecture rationale

---

## License

MIT — see [LICENSE](LICENSE)
