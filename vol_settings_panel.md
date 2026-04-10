# Task — Vol scanner settings panel

## Context

The trading dashboard is a **PyQt5** app with this architecture:

```
controller.py          — app controller, wires services to UI panels
services/vol_engine.py — Thread 2: runs vol_mid_step1 pipeline (scan FOP IV, PCHIP, pillars)
services/vol_fair.py   — (future) Thread 3: runs vol_fair_step2 pipeline (RV, GARCH, δ_book, σ_fair)
ui/main_window.py      — assembles all panels
ui/panels/vol_scanner_panel.py — displays scanner rows
config/                — JSON config files (fop_expiries.json, fop_strikes.json, etc.)
```

**Current state**: all configurable parameters are **hardcoded at the top of `services/vol_engine.py`**:

```python
# In services/vol_engine.py (lines 30-40)
TARGET_DTES = [30, 60, 90, 120, 150, 180]
WAIT_GREEKS = 8
IV_ARB_THRESHOLD = 0.005
LOOP_INTERVAL_S = 180

PARAMS = {
    "short": {"n_side": 20, "rr25_max": 10.0, "bf25_min": -6.0, "min_strikes": 5},
    "long":  {"n_side": 30, "rr25_max": 6.0, "bf25_min": -4.0, "min_strikes": 7},
}
```

Step 2 parameters (W1, W2, RISK_PREMIUM, etc.) will live in `services/vol_fair.py` when implemented, but should already be configurable now so the UI is ready.

The goal is to:
1. Create a `config/vol_config.json` file
2. Make `vol_engine.py` (and future `vol_fair.py`) read from it at each scan cycle
3. Add a settings panel in the dashboard to edit and save these parameters

## What to build

### 1. Config file: `config/vol_config.json`

Create this file in the existing `config/` directory (alongside `fop_expiries.json`):

```json
{
  "step1": {
    "WAIT_GREEKS": 8,
    "LOOP_INTERVAL_S": 180,
    "n_side_short": 20,
    "n_side_long": 30,
    "rr25_max_short": 10.0,
    "rr25_max_long": 6.0,
    "bf25_min_short": -6.0,
    "bf25_min_long": -4.0,
    "IV_ARB_THRESHOLD": 0.005,
    "TARGET_DTES": [30, 60, 90, 120, 150, 180]
  },
  "step2": {
    "W1": 0.65,
    "W2": 0.35,
    "SIGNAL_THRESHOLD": 0.20,
    "ALPHA_BOOK": 0.20,
    "GARCH_DURATION": "1 Y",
    "RISK_PREMIUM": {
      "1M": 1.20, "2M": 1.35, "3M": 1.50,
      "4M": 1.55, "5M": 1.58, "6M": 1.60
    },
    "VEGA_LIMITS": {
      "1M": 150000, "2M": 200000, "3M": 300000,
      "4M": 350000, "5M": 375000, "6M": 400000
    }
  }
}
```

### 2. Modify `services/vol_engine.py`

Add a config loader that reads `config/vol_config.json` **at the start of each `_run_scan()` call** (not just at module import), so changes take effect on the next scan cycle without restarting the thread.

**Add this function** near the top of `vol_engine.py`:

```python
import json
from pathlib import Path

def _load_vol_config(section: str) -> dict:
    """Load config from config/vol_config.json, fallback to empty dict."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "vol_config.json"
    try:
        with open(config_path, "r") as f:
            cfg = json.load(f)
        return cfg.get(section, {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

**Replace the hardcoded module-level constants** with defaults, then at the top of `_run_scan()`, reload from config:

```python
# In VolEngine._run_scan(), before any IB calls:
cfg = _load_vol_config("step1")
wait_greeks = cfg.get("WAIT_GREEKS", 8)
iv_arb_threshold = cfg.get("IV_ARB_THRESHOLD", 0.005)
target_dtes = cfg.get("TARGET_DTES", [30, 60, 90, 120, 150, 180])
params = {
    "short": {
        "n_side": cfg.get("n_side_short", 20),
        "rr25_max": cfg.get("rr25_max_short", 10.0),
        "bf25_min": cfg.get("bf25_min_short", -6.0),
        "min_strikes": 5,
    },
    "long": {
        "n_side": cfg.get("n_side_long", 30),
        "rr25_max": cfg.get("rr25_max_long", 6.0),
        "bf25_min": cfg.get("bf25_min_long", -4.0),
        "min_strikes": 7,
    },
}
```

Then pass these local variables to the sub-methods (`_discover_chains`, `_qualify_contracts`, `_scan_iv`) instead of using the module globals. This means adding parameters to these methods.

**Also update `LOOP_INTERVAL_S`** in the `run()` loop — read it from config at each iteration:

```python
def run(self) -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    while not self._stop_event.is_set():
        cfg = _load_vol_config("step1")
        loop_interval = cfg.get("LOOP_INTERVAL_S", 180)
        try:
            result = self._run_scan()
            self._output_queue.put(result)
        except Exception as exc:
            ...
        if self._stop_event.wait(timeout=loop_interval):
            break
```

### 3. Create `ui/panels/vol_settings_panel.py`

A new PyQt5 panel (QDialog or QWidget) with the same visual style as the existing panels in the dashboard.

**Structure**: 3 collapsible sections (use `QGroupBox` with `setCheckable(True)` or a custom collapsible widget matching the dashboard's existing style). Each section contains a form/table of parameters.

#### Section 1 — Model (step 2 params, critical)

Default: **expanded**.

| Parameter | Widget | Range | Default | Impact | Description |
|---|---|---|---|---|---|
| `W1` / `W2` | `QSlider` + 2 labels | 0.00–1.00 (step 0.05) | 0.65 / 0.35 | CRITICAL | Weight RV anchor vs GARCH in σ_fair. W1+W2=1 always. W2 auto-updates. |
| `SIGNAL_THRESHOLD` | `QDoubleSpinBox` | 0.05–1.00% (step 0.05) | 0.20% | CRITICAL | Seuil CHEAP/EXPENSIVE. Plus bas = plus de signaux, plus de faux positifs. |
| `RISK_PREMIUM 1M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.20% | CRITICAL | Prime de risque additive pour le tenor 1M. Shift direct de l'anchor. |
| `RISK_PREMIUM 2M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.35% | CRITICAL | Idem 2M. |
| `RISK_PREMIUM 3M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.50% | CRITICAL | Idem 3M. |
| `RISK_PREMIUM 4M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.55% | CRITICAL | Idem 4M. |
| `RISK_PREMIUM 5M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.58% | CRITICAL | Idem 5M. |
| `RISK_PREMIUM 6M` | `QDoubleSpinBox` | 0.00–5.00% (step 0.05) | 1.60% | CRITICAL | Idem 6M. |
| `ALPHA_BOOK` | `QSlider` + label | 0.00–1.00 (step 0.05) | 0.20 | MEDIUM | Intensité de l'ajustement book. 0 = pas d'ajustement. |

#### Section 2 — Scan (step 1 + step 2 scan params)

Default: **collapsed**.

| Parameter | Widget | Range | Default | Impact | Description |
|---|---|---|---|---|---|
| `WAIT_GREEKS` | `QSlider` + label | 3–15s (step 1) | 8s | MEDIUM | Temps d'attente greeks IB par tenor. Trop bas = IV manquantes. |
| `LOOP_INTERVAL_S` | `QSpinBox` | 30–600s (step 30) | 180s | MEDIUM | Intervalle entre deux scans complets. |
| `n_side_short` | `QSlider` + label | 8–30 (step 1) | 20 | MEDIUM | Strikes scannés par côté pour DTE ≤ 45j. |
| `n_side_long` | `QSlider` + label | 12–40 (step 1) | 30 | MEDIUM | Idem pour DTE > 45j. |
| `GARCH_DURATION` | `QComboBox` | ["6 M", "1 Y", "2 Y"] | "1 Y" | MEDIUM | Fenêtre historique calibration GARCH. |
| `TARGET_DTES` | `QLineEdit` (comma-separated) | integers 7–365 | "30,60,90,120,150,180" | LOW | Tenors cibles en jours. |

#### Section 3 — Filters (validation gates)

Default: **collapsed**.

| Parameter | Widget | Range | Default | Impact | Description |
|---|---|---|---|---|---|
| `rr25_max_short` | `QDoubleSpinBox` | 5.0–20.0% | 10.0% | LOW | Max \|RR25\| tenors courts. Rejette skew aberrant. |
| `rr25_max_long` | `QDoubleSpinBox` | 3.0–15.0% | 6.0% | LOW | Max \|RR25\| tenors longs. |
| `bf25_min_short` | `QDoubleSpinBox` | -10.0–0.0% | -6.0% | LOW | Min BF25 tenors courts. Rejette smile inversé. |
| `bf25_min_long` | `QDoubleSpinBox` | -10.0–0.0% | -4.0% | LOW | Min BF25 tenors longs. |
| `IV_ARB_THRESHOLD` | `QDoubleSpinBox` | 0.1–2.0% | 0.5% | LOW | Max \|iv_call - iv_put\| avant flag arb. |
| `VEGA_LIMITS 1M–6M` | 6× `QSpinBox` | 50K–1M (step 50K) | 150K–400K | MEDIUM | Budget vega par tenor en EUR/vol%. |

#### Impact badges

Use colored `QLabel` with fixed-size background:
- **CRITICAL** — red background (`#FCEBEB` text `#A32D2D`, dark mode: `#791F1F` / `#F7C1C1`)
- **MEDIUM** — amber background (`#FAEEDA` / `#854F0B`, dark mode: `#633806` / `#FAC775`)
- **LOW** — green background (`#E1F5EE` / `#0F6E56`, dark mode: `#085041` / `#9FE1CB`)

### 4. Wire into `controller.py`

#### Opening the panel

Add a method to controller:

```python
def _open_vol_settings(self) -> None:
    """Open the vol scanner settings dialog."""
    from ui.panels.vol_settings_panel import VolSettingsPanel
    config_path = self._project_root / "config" / "vol_config.json"
    dialog = VolSettingsPanel(config_path=config_path, parent=self.window)
    if dialog.exec_() == QDialog.Accepted:
        self._log("[INFO][settings] Vol config saved — changes apply on next scan cycle")
```

#### Triggering it

Option A — Add a gear icon button in `vol_scanner_panel.py` header, connect to `controller._open_vol_settings`.

Option B — Add a "Vol Settings" button in the status panel alongside Start/Stop Engine.

Wire in `_setup_services()`:

```python
# In controller._setup_services():
settings_btn = getattr(self.window.vol_scanner_panel, "settings_button", None)
if settings_btn is not None:
    settings_btn.clicked.connect(self._open_vol_settings)
```

### 5. `VolSettingsPanel` internal behavior

```python
class VolSettingsPanel(QDialog):
    def __init__(self, config_path: Path, parent=None):
        super().__init__(parent)
        self._config_path = config_path
        self._defaults = { ... }  # hardcoded defaults dict
        self._build_ui()
        self._load_config()  # populate widgets from vol_config.json

    def _load_config(self):
        """Read vol_config.json and populate all widgets. Fallback to defaults."""
        ...

    def _save_config(self):
        """Read all widget values, validate, write to vol_config.json, accept dialog."""
        # Validation: W1+W2==1, TARGET_DTES has ≥1 value, all ranges respected
        ...
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.accept()

    def _reset_defaults(self):
        """Reset all widgets to default values (don't save yet)."""
        ...
```

**Buttons at bottom of dialog**:
- **Save** → calls `_save_config()`, closes dialog
- **Reset defaults** → calls `_reset_defaults()`, keeps dialog open
- **Cancel** → `self.reject()`, closes without saving

## Summary of files to create/modify

| File | Action |
|---|---|
| `config/vol_config.json` | **Create** — default config file |
| `services/vol_engine.py` | **Modify** — add `_load_vol_config()`, replace hardcoded globals with config reads in `_run_scan()` and `run()` |
| `ui/panels/vol_settings_panel.py` | **Create** — PyQt5 QDialog with 3 collapsible sections |
| `ui/panels/vol_scanner_panel.py` | **Modify** — add a gear icon button in the panel header |
| `controller.py` | **Modify** — add `_open_vol_settings()` method, wire button in `_setup_services()` |

## Design constraints

- **PyQt5 only** — no React, no web. Match the existing panel style (dark background, monospace labels, consistent spacing).
- Config is read **at each scan cycle** (every `LOOP_INTERVAL_S` seconds), not just at startup. This means changes take effect without restarting the app.
- The `vol_config.json` path uses the project root's `config/` directory, resolved via `Path(__file__).resolve().parents[1] / "config"` (same pattern as `fop_expiries.json`).
- No external dependencies beyond what the project already uses (PyQt5, json, pathlib).
