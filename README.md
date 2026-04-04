# Trading Dashboard (IBKR)

Lightweight desktop dashboard focused on **live monitoring from IB Gateway/TWS**.

## Scope
- Connect to IBKR API (host/port/client ID/read-only mode).
- Default startup is read-only mode for safer demo runs.
- Start/stop live streaming for one FX symbol (ex: `EURUSD`).
- Plot live tick mid-price chart.
- Submit manual FX orders from an Order Ticket panel.
- Show live status (connection state, environment, account, latency, server time).
- Show account/portfolio snapshot.
- Show open orders and recent fills.
- Stream tick logs with filter controls.
- Run market data and order execution in separate worker threads.

This version intentionally excludes research notebooks, robots, persistence/database, and Docker.

## Tech Stack
- Python 3.11
- PyQt5
- ib-insync

## Project Structure
- `app.py`: entrypoint.
- `src/controller.py`: app lifecycle + settings + service orchestration.
- `src/services/ib_client.py`: IBKR API wrapper.
- `src/services/market_data_worker.py`: periodic tick/snapshot worker (QThread).
- `src/services/order_worker.py`: queued order execution worker (QThread).
- `src/ui/main_window.py`: fixed 2x3 grid main window.
- `src/ui/panels/`: `status_panel.py`, `chart_panel.py`, `order_ticket_panel.py`, `portfolio_panel.py`, `orders_panel.py`, `logs_panel.py`.
- `config/status_panel_settings.json`: persisted app settings.

## Quickstart
Prerequisites:
- Python 3.11
- IB Gateway or TWS running locally with API enabled (paper commonly on port `4002`).

PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
python app.py
```

bash:
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r ./requirements.txt
python app.py
```

## Tests
This project includes a pragmatic automated test baseline focused on production-risk areas:
- settings validation and migration logic;
- order validation and execution-thread behavior;
- market-data worker payload construction and failure handling;
- IB client fallback behavior and status snapshot formatting;
- critical status panel state transitions.

Run tests locally:

```bash
python -m pytest
```

Run only fast unit tests (same scope as CI pull requests):

```bash
python -m pytest -m "not integration"
```
