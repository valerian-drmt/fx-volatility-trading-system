# Screenshots

Live captures of the desk (`valeriandarmente.dev/fx-volatility-trading-system`).
The **hero** + the **★ four** are embedded in the root `README.md` (top image
and the 2×2 grid); the rest are reference shots linked from there.

## Root README

| File | View | Shows |
|---|---|---|
| `dashboard.png` | **Dashboard** (hero) | the one-screen cockpit — vol surface, PCA signals, positions, greeks |
| ★ `vol-surface.png` | **Signals** | the SVI/SSVI vol surface + regime + PCA z-scores (the quant centrepiece) |
| ★ `risk.png` | **Risk** | portfolio greeks (Δ/Γ/V/Θ/Vanna/Volga) + Value-at-Risk + P&L distribution, two 3-column rows |
| ★ `positions.png` | **Trade → Open positions** | the structured book: straddle / strangle / butterfly / calendar / call spread + futures, greeks per leg |
| ★ `architecture.png` | **System** | the container topology — the 11 services healthy |

## Reference shots

| File | View | Shows |
|---|---|---|
| `pnl-attribution.png` | **Portfolio → P&L attribution by trade** | `½Γ·dS²` populated, futures footing, Total row reconciling |
| `marginal-var.png` | **Risk → Marginal contribution to VaR** | real structure names + caret left of name, capped at 6 rows w/ scroll |
| `expiries-rolloff.png` | **Risk → Expiries & roll-off** | structure names + DTE / pin P&L |
| `portfolio-equity.png` | **Portfolio** | the equity / net-liq curve |
| `portfolio-performance.png` | **Portfolio** | performance table + cash / margin |
| `trade-builder.png` | **Trade → order builder** | the structure factory (Δ pillar + tenor selectors) |
| `settings-volconfig.png` | **Settings** (auth) | the versioned VolConfig editor |
| `system-hardware.png` | **System → Hardware** | per-container CPU / RAM + host memory / swap |
| `grafana-ibgw-cpu.png` | **Grafana** (obs profile) | the `ib-gateway` CPU series with the ~2am nightly-restart spike |
| `db-schema.png` | **/dev console** (auth) | the ORM db-schema diagram / alembic migrations inspector |
