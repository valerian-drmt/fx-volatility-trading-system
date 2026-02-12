# Trading Project

End-to-end trading research and UI prototype: data fetch, feature engineering, labeling, model training, and a live tick dashboard for IBKR (via ib_insync).

## Highlights
- PyQt5 desktop UI with dockable panels for charts, performance, portfolio, orders, risk, logs, and connection status.
- Data pipeline to fetch OHLCV and DOM data (Binance via ccxt, Bybit public data), save/load CSV, and resample.
- Feature engineering (VWAP, session flags, pivot points, volume pivots, CVD, OBI, and more).
- Labeling utilities for pivot-based classification targets.
- LSTM model + trainer with early stopping and metrics.
- Research notebooks in `Research/` for experimentation (fetching, features/labels, analysis, LSTM, SAC, backtesting).

## Project Structure
- `src/app.py`: UI entrypoint (starts the LiveTickWindow).
- `src/ui/`: PyQt5 app and panel widgets (chart, performance, portfolio, risk, orders, logs, status).
- `src/data/`: fetchers, preprocessing, features, labels, and analysis helpers.
- `src/ml/`: LSTM model and trainer (SAC stubs present).
- `src/services/`: placeholders for IB client, data feed, robot manager.
- `Research/`: notebooks and sample datasets.
- `tests/`: pytest tests (currently a basic import test).

## Quickstart (UI)
Prereqs:
- Python 3.11
- IB Gateway or TWS running locally with API access enabled (default: paper port 4002).

Setup (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH="src"
python src\app.py
```

Setup (bash):
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
python src/app.py
```

## Data and ML Workflow (High Level)
1) Fetch market data (Binance OHLCV or Bybit DOM) in `src/data/fetcher.py`.
2) Build features in `src/data/features.py`.
3) Create labels in `src/data/labels.py`.
4) Prepare tensors and loaders in `src/data/preprocessing.py`.
5) Train the LSTM in `src/ml/models/lstm_model.py` and `src/ml/trainer/lstm_trainer.py`.

## Tests
```bash
python -m pytest -q
```

## Notes and Caveats
- Some modules are placeholders (`src/services/ib_client.py`, `src/services/data_feed.py`, `src/services/robot_manager.py`, `src/ml/models/sac_model.py`, `src/ml/trainer/sac_trainer.py`).
- Several data/ML modules require extra packages not listed in `requirements.txt` (for example: `ccxt`, `requests`, `scikit-learn`, `statsmodels`, `psutil`). Install as needed for those workflows.
- The UI expects a live IBKR API connection to stream ticks; run IB Gateway/TWS first.
