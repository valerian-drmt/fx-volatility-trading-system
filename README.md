# Trading Dashboard — Live IBKR Monitor
 
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-41CD52?logo=qt&logoColor=white)
![ib-insync](https://img.shields.io/badge/ib--insync-IBKR%20API-blue)
![CI](https://img.shields.io/github/actions/workflow/status/valerian-drmt/trading-ib/tests.yml?label=CI)
![License](https://img.shields.io/badge/license-MIT-green)
 
Lightweight desktop dashboard for **live FX monitoring and manual order execution** via IB Gateway/TWS.  
Multithreaded architecture separating market data streaming and order execution into independent QThread workers.
 
---
 
## Architecture
 
```
IB Gateway / TWS
       │
       ▼
  ib_client.py  (IBKR API wrapper)
       │
       ├──► MarketDataWorker (QThread) ──► ChartPanel  (live mid-price)
       │                                ──► LogsPanel  (tick stream)
       │
       └──► OrderWorker      (QThread) ──► OrderTicketPanel (LMT + TP/SL bracket)
                                        ──► OrdersPanel     (open orders / fills)
 
Controller ──► PortfolioPanel  (account snapshot)
           ──► StatusPanel     (connection state, latency, server time)
```
 
---
 
## Features
 
- Connect to IBKR API (host / port / client ID / read-only mode)
- Default startup in **read-only mode** for safe demo runs
- Live FX tick streaming for one symbol (e.g. `EURUSD`) with mid-price chart
- Manual order execution with optional **TP/SL bracket** for LMT orders
- Cancel all open orders from the Order Ticket panel
- Account / portfolio snapshot, open orders, and recent fills
- Tick log stream with filter controls
- Persisted settings via `config/status_panel_settings.json`
 
> This version intentionally excludes research notebooks, signal robots, database persistence, and Docker.
 
---
 
## Tech Stack
 
| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| GUI | PyQt5 |
| IBKR connectivity | ib-insync |
| Concurrency | QThread (market data + order workers) |
| Testing | pytest |
| Linting / CI | ruff · GitHub Actions |
 
---
 
## Project Structure
 
```
trading-ib/
├── app.py                          # Entrypoint
├── src/
│   ├── controller.py               # App lifecycle, settings, service orchestration
│   ├── services/
│   │   ├── ib_client.py            # IBKR API wrapper
│   │   ├── market_data_worker.py   # Periodic tick/snapshot worker (QThread)
│   │   └── order_worker.py         # Queued order execution worker (QThread)
│   └── ui/
│       ├── main_window.py          # Fixed 2×3 grid layout
│       └── panels/
│           ├── status_panel.py
│           ├── chart_panel.py
│           ├── order_ticket_panel.py
│           ├── portfolio_panel.py
│           ├── orders_panel.py
│           └── logs_panel.py
├── config/
│   └── status_panel_settings.json  # Persisted connection + runtime settings
└── tests/
```
 
---
 
## Quickstart
 
**Prerequisites:** Python 3.11 · IB Gateway or TWS running locally with API enabled (paper: port `4002`)
 
```bash
# Create virtual environment (no output on success)
python -m venv .venv
```

Activate it with the command for your shell:

```bash
source .venv/bin/activate          # macOS / Linux
```

```powershell
.\.venv\Scripts\Activate.ps1       # Windows PowerShell
# If blocked in current session:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

```bat
.\.venv\Scripts\activate.bat       # Windows CMD
```

```bash
pip install -r requirements.txt
python app.py
```
 
---
 
## Tests
 
Test coverage focused on production-risk areas:
 
- Settings validation and migration logic
- Order validation and execution-thread behavior
- Market-data worker payload construction and failure handling
- IB client fallback behavior and status snapshot formatting
- Critical status panel state transitions
 
```bash
# Full project test suite (pytest)
python -m pytest
```

To run the integration smoke test (instead of auto-skip), set `IB_RUN_INTEGRATION=1` and define `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` (typically from `config/status_panel_settings.json`), then run `python -m pytest -rs`.
 
**CI quality gates:** `compileall` import sanity · `ruff` linting · unit tests
 
---
 
## License
 
MIT — see [LICENSE](LICENSE)
