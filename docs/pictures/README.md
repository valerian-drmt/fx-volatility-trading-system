# Screenshots — capture checklist

Drop the PNGs in this folder using the exact filenames below. The **★ four** are
wired into the root `README.md` 2×2 grid; the rest are reference shots.

Tips before shooting: open the live app
(`valeriandarmente.dev/fx-volatility-trading-system`), wait ~1–2 min after a
refresh so live panels leave the "accumulating…" state, and use a wide window so
tables don't horizontal-scroll.

## ★ Root README (2×2 grid)

| File | Where | Make sure it shows |
|---|---|---|
| `vol-surface.png` | **Signals** view | the SVI/SSVI vol surface + regime + PCA z-scores (the quant centrepiece) |
| `risk.png` | **Risk** view (top) | Portfolio greeks (Δ/Γ/V/Θ/Vanna/Volga) + the Value-at-Risk table + P&L distribution histogram in one frame |
| `positions.png` | **Trade → Open positions** | the structured book: straddle / strangle / butterfly / calendar / call spread + futures, greeks per leg, a couple rows expanded |
| `architecture.png` | **System** view | the container topology — all 11 services **healthy** (post IB recovery) |

## Reference shots (this folder only)

| File | Where | Make sure it shows |
|---|---|---|
| `pnl-attribution.png` | **Portfolio → P&L attribution by trade** | the fixed table: `½Γ·dS²` populated (not $0), futures footing, Total row reconciling |
| `marginal-var.png` | **Risk → Marginal contribution to VaR** | correct structure names (Straddle / Strangle 25Δ / Calendar / Butterfly), not all "Strangle 10Δ" |
| `expiries-rolloff.png` | **Risk → Expiries & roll-off** | correct structure names + DTE / pin P&L |
| `portfolio-equity.png` | **Portfolio** view | the equity / net-liq curve |
| `portfolio-performance.png` | **Portfolio** view | performance table + cash / margin |
| `trade-builder.png` | **Trade → order builder** | the structure factory (Δ pillar + tenor selectors) |
| `settings-volconfig.png` | **Settings** (auth) | the versioned VolConfig editor |
| `system-hardware.png` | **System → Hardware** | per-container CPU / RAM + host memory/swap |
| `grafana-ibgw-cpu.png` | **Grafana** (obs profile) | the `ib-gateway` CPU series with the ~2am nightly-restart spike (24h window) |
| `dev-console.png` | **/dev console** (auth) | db-schema diagram or the alembic migrations inspector |
