# Configuration

Two JSON config files in `config/`. Editable via the Settings dialog (settings button in Runtime Status panel).

## config/status_panel_settings.json

Connection and runtime parameters.

```json
{
  "status": {
    "host": "127.0.0.1",
    "port": 4002,
    "client_id": 1,
    "client_roles": {"market_data": 1, "vol_engine": 2, "risk_engine": 3},
    "readonly": false,
    "market_symbol": "EURUSD"
  },
  "runtime": {
    "tick_interval_ms": 100,
    "snapshot_interval_ms": 2000
  }
}
```

### status section

| Parameter | Type | Default | Description |
|---|---|---|---|
| `host` | string | `"127.0.0.1"` | IB Gateway/TWS hostname |
| `port` | int | `4002` | IB Gateway port (4001=live, 4002=paper) |
| `client_id` | int | `1` | Primary IB client ID (Thread 1 + order execution) |
| `client_roles` | dict | `{1, 2, 3}` | IB client IDs per thread. Must be distinct. |
| `readonly` | bool | `false` | Reserved (not used) |
| `market_symbol` | string | `"EURUSD"` | FX pair for tick streaming and order ticket |

### runtime section

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tick_interval_ms` | int | `100` | Thread 1 poll interval in milliseconds |
| `snapshot_interval_ms` | int | `2000` | Account snapshot interval in milliseconds (within Thread 1) |

## config/vol_config.json

Vol engine parameters. Controls the IV scan (Step 1) and fair vol model (Step 2).

### step1 -- IV Surface Extraction

| Parameter | Type | Default | Impact | Settings Badge |
|---|---|---|---|---|
| `WAIT_GREEKS` | int | `8` | Seconds to wait for IB model greeks after `reqMktData`. Higher = more data, slower cycle. | MEDIUM |
| `LOOP_INTERVAL_S` | int | `180` | Seconds between vol scan cycles. Lower = more frequent, higher IB load. | MEDIUM |
| `n_side_short` | int | `20` | Strikes scanned around ATM for short tenors (DTE <= 45). | MEDIUM |
| `n_side_long` | int | `30` | Strikes scanned around ATM for long tenors (DTE > 45). | MEDIUM |
| `rr25_max_short` | float | `10.0` | Max abs(RR25) in % for short tenors. Tenors exceeding this are dropped. | LOW |
| `rr25_max_long` | float | `6.0` | Max abs(RR25) in % for long tenors. | LOW |
| `bf25_min_short` | float | `-6.0` | Min BF25 in % for short tenors. Tenors below this are dropped. | LOW |
| `bf25_min_long` | float | `-4.0` | Min BF25 in % for long tenors. | LOW |
| `IV_ARB_THRESHOLD` | float | `0.005` | Max C-P IV divergence (decimal). Flags put-call parity violations. | LOW |
| `TARGET_DTES` | list[int] | `[30,60,90,120,150,180]` | Target days-to-expiry for tenor selection. Maps to 1M-6M. | MEDIUM |

### step2 -- Fair Volatility Model

| Parameter | Type | Default | Impact | Settings Badge |
|---|---|---|---|---|
| `W1` | float | `0.65` | Weight on RV anchor (Yang-Zhang + risk premium). W2 = 1 - W1. | CRITICAL |
| `W2` | float | `0.35` | Weight on GARCH forward vol. Auto-computed from W1. | CRITICAL |
| `SIGNAL_THRESHOLD` | float | `0.20` | Signal threshold in vol % (20 bps). Lower = more signals, more noise. | CRITICAL |
| `ALPHA_BOOK` | float | `0.20` | Book adjustment strength. Higher = more aggressive position-aware quoting. | MEDIUM |
| `GARCH_DURATION` | string | `"1 Y"` | Historical data window for GARCH calibration. Options: "6 M", "1 Y", "2 Y". | MEDIUM |
| `RP_FLOOR` | float | `0.20` | Minimum risk premium in vol %. | MEDIUM |
| `VRP_SHIFT` | float | `0.50` | Additive shift on variance risk premium for dynamic RP. | MEDIUM |
| `W1_RATIO_THRESHOLD` | float | `1.15` | RV short/long ratio above which W1 is reduced (vol regime detection). | MEDIUM |
| `W1_RATIO_SENSITIVITY` | float | `0.10` | W1 reduction per unit of excess RV ratio. | MEDIUM |
| `W1_FLOOR` | float | `0.40` | Minimum W1 after conditional adjustment. | MEDIUM |
| `GARCH_EMPIRICAL_BLEND` | float | `0.50` | Blend weight between GARCH projection and empirical mean-reversion. | MEDIUM |
| `EMPIRICAL_KAPPA` | float | `2.0` | Mean-reversion speed for empirical blend. | MEDIUM |

### step2 -- Risk Premium per tenor

| Tenor | Default (vol %) | Description |
|---|---|---|
| 1M | 1.20 | Implied typically exceeds realized by this amount |
| 2M | 1.35 | Increases with tenor (more uncertainty) |
| 3M | 1.50 | |
| 4M | 1.55 | |
| 5M | 1.58 | |
| 6M | 1.60 | |

These are additive: `Anchor(T) = RV(T) + RP(T)`. Calibrated from historical EURUSD IV-RV spreads.

### step2 -- Vega Limits per tenor

| Tenor | Default (EUR/vol%) | Description |
|---|---|---|
| 1M | 150,000 | Max portfolio vega before book adjustment kicks in |
| 2M | 200,000 | |
| 3M | 300,000 | Increases with tenor (longer-dated vega is less volatile) |
| 4M | 350,000 | |
| 5M | 375,000 | |
| 6M | 400,000 | |

When net vega approaches the limit, `delta_book` shifts sigma_fair to discourage adding to the position.

## Settings Dialog

Accessible via the "Settings" button in the Runtime Status panel. Opens `settings_panel.py` (`SettingsPanel` QDialog).

Sections:
- **Connection**: Host, Port, Client ID (read-only)
- **Model**: W1/W2 slider, Signal Threshold, Alpha Book, Risk Premium per tenor
- **Scan**: Wait Greeks, Loop Interval, n_side, GARCH Duration, Target DTEs
- **Filters**: RR25/BF25 limits, IV Arb Threshold, Vega Limits per tenor

Each parameter has a colored badge:
- **CRITICAL** (red): directly impacts signal direction and fair vol level
- **MEDIUM** (orange): affects scan quality or model sensitivity
- **LOW** (green): fine-tuning, minimal impact on signals

Changes saved to both JSON files. Applied on the next vol engine scan cycle.
