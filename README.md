# Trading Project

Trading UI and research stack for IBKR workflows: live tick dashboard, service orchestration, and an offline ML pipeline for data preparation and model training.

## Highlights
- PyQt5 desktop UI with dockable panels (chart, performance, portfolio, orders, risk, logs, status).
- Live runtime orchestrated by `Controller` + services (`IBClient`, pipeline runner, persistence).
- Research pipeline for fetch/features/labels/preprocessing/training under `research/ml`.
- Draw.io architecture and dataflow diagrams under `Schemes/`.

## Project Structure
- `app.py`: main entrypoint. Adds `src` to `sys.path`, then launches `Controller`.
- `src/controller.py`: app bootstrap, UI wiring, service lifecycle.
- `src/ui/`: PyQt5 main window and panel widgets.
- `src/services/`: IB client and runtime services (`pipeline_runner`, persistence, performance, snapshot thread).
- `src/domain/` and `src/utils/`: shared types/events and utility helpers.
- `research/`: notebooks, datasets, and ML modules in `research/ml`.
- `tests/services/`: pytest coverage for core service behavior.
- `Schemes/`: architecture and runtime flow diagrams.

## Quickstart (UI)
Prereqs:
- Python 3.11
- IB Gateway or TWS running locally with API access enabled (default paper port: `4002`).

Setup (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Setup (bash):
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Docker
Build image:
```bash
docker build -t trading-system:latest .
```

Run container (headless check):
```bash
docker run --rm -e QT_QPA_PLATFORM=offscreen trading-system:latest
```

Run container with UI on Windows (PowerShell + VcXsrv):
```powershell
docker run --rm -it `
  -e DISPLAY=host.docker.internal:0.0 `
  -e QT_X11_NO_MITSHM=1 `
  trading-system:latest
```

## Data and ML Workflow (High Level)
1) Fetch market data in `research/ml/data/fetcher.py`.
2) Build features in `research/ml/data/features.py`.
3) Create labels in `research/ml/data/labels.py`.
4) Prepare tensors and loaders in `research/ml/data/preprocessing.py`.
5) Train models with `research/ml/models/lstm_model.py` and `research/ml/trainer/lstm_trainer.py`.

## Tests
```bash
python -m pytest -q
```

## Notes and Caveats
- Placeholder modules currently exist in `src/services/data_feed.py`, `src/services/robot_manager.py`, `research/ml/models/sac_model.py`, and `research/ml/trainer/sac_trainer.py`.
- Some research modules rely on optional packages not pinned in `requirements.txt` (for example `ccxt`, `requests`, `scikit-learn`, `statsmodels`, `psutil`).
- Live UI features require an active IBKR API session (IB Gateway/TWS) to stream data.
