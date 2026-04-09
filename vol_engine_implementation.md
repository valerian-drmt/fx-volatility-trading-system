# Volatility Engine — Implementation Guide

## Overview

Second thread of the application: a **real-time volatility analytics engine** that runs CPU-bound calculations (Black-Scholes inversion, GARCH calibration, Yang-Zhang realized vol) off the main asyncio+Qt thread, communicating via `queue.Queue`.

```
Thread 1 (main) — asyncio + Qt + IB Gateway
    |
    |  option chain prices, OHLC bars, portfolio greeks
    |  ──► queue.Queue ──►  Thread 2 — Vol Engine
    |                           |
    |  ◄── vol surface update   |  Every 30s:
    |  ◄── scanner signals      |   - BS inversion per strike x tenor
    |  ◄── greeks aggregation   |   - Yang-Zhang RV rolling
    |                           |   - GARCH forward vol
    |  update UI panels         |   - sigma_fair combination
    |                           |   - portfolio greeks aggregation
```

---

## Data Pipeline

### Step 1 — Market Implied Vol (`vol_mid_step1.py`)

Collects raw IV from IB Gateway and reconstructs the delta-pillar smile.

| Stage | IB API Call | Output |
|---|---|---|
| Spot price | `reqMktData` on EUR front-month future | `spot` (float) |
| Option chain | `reqSecDefOptParams` on EUR FOP CME | List of strikes per expiry |
| Implied vol per strike | `reqMktData` tick 100 on each FOP contract | `iv_raw`, `bid`, `ask`, `volume`, `delta` |

**Processing:**
1. **Filter geographic** — keep strikes within +/-8% of spot
2. **Filter liquidity** — min volume >= 5, bid-ask spread < 20%, IV non-NaN
3. **Compute IV mid** — average call/put IV at each strike (put-call parity)
4. **Reconstruct delta pillars** — BS delta inversion (Newton-Raphson) to map strikes to standard deltas (10Dp, 25Dp, ATM, 25Dc, 10Dc)
5. **Derive RR/BF** — Risk Reversal = IV(25Dc) - IV(25Dp), Butterfly = 0.5*(IV(25Dc) + IV(25Dp)) - IV(ATM)

**Output:** `vol_mid_output.csv` — one row per tenor with sigma_ATM, RR25, BF25, RR10, BF10, and pillar strikes.

**Libraries:** `numpy`, `scipy.stats.norm`, `scipy.optimize.brentq`/`newton`, `pandas`, `ib_insync`

### Step 2 — Fair Volatility (`vol_fair_step2.py`)

Combines three anchors to produce sigma_fair per tenor.

**Layer A — Realized Vol (Yang-Zhang estimator):**
- IB API: `reqHistoricalData` — 252 daily OHLC bars on EUR future
- Yang-Zhang estimator uses Open, High, Low, Close (more efficient than close-to-close)
- Rolling window adapted per tenor: max(21 days, T in business days)
- Anchor_1(T) = RV(T) + Risk_Premium(T)

**Layer B — GARCH(1,1) forward vol:**
- Calibrated on daily log-returns via `arch` library
- Forward vol curve: sigma^2(T) = sigma^2_LR + (sigma^2_current - sigma^2_LR) * exp(-kappa*T)
- Captures mean-reversion speed (kappa) and current vol regime

**Layer C — Book adjustment (delta_book):**
- IB API: `reqPositions` + `reqMktData` tick 100 for greeks
- Vega_net(T) = sum of position * vega * multiplier per tenor
- delta_book(T) = -alpha * Vega_net(T) / Vega_limit(T), clamped to +/-alpha
- Long vol book -> mark below mid (seller), short vol -> mark above (buyer)

**Combination:**
```
sigma_fair(T) = W1 * Anchor_1(T) + W2 * sigma_model(T) + delta_book(T)
```
Default weights: W1=0.65, W2=0.35

**Output:** `vol_fair_output.csv` — one row per tenor with RV, RP, Anchor, sigma_model, delta_book, sigma_fair, ecart, signal.

**Libraries:** `arch` (GARCH), `numpy`, `scipy`, `pandas`, `ib_insync`

---

## Dashboard Panels

### Panel 1 — Vol Scanner (central, full width)

Real-time table comparing market IV vs fair vol per strike and tenor.

| Column | Source | Computation |
|---|---|---|
| Tenor | Step 1 | IMM expiry label |
| Delta label | Step 1 | 10Dp, 25Dp, ATM, 25Dc, 10Dc |
| Strike | Step 1 | Pillar strike |
| IV market % | IB tick 100 | `ticker.impliedVolatility * 100` |
| sigma_fair % | Step 2 + Step 1 shape | `sigma_fair_ATM + RR/BF adjustment` |
| Ecart % | Computed | `IV_market - sigma_fair` |
| Signal | Threshold | CHEAP (<-0.20%) / EXPENSIVE (>+0.20%) / FAIR |

**Coloring:** Red background (#FCEBEB) for EXPENSIVE, green (#E1F5EE) for CHEAP.
**Interaction:** Click row -> pre-fill order ticket with strike, tenor, right (Call/Put), suggested notional. Supports direct Buy/Sell of vanilla Call and Put options on EUR CME futures options (FOP) via IB Gateway.
**Sort:** By |ecart%| descending (best opportunities first).
**Update frequency:** Every 30 seconds.

### Panel 2 — Term Structure Chart (top right)

pyqtgraph chart with three curves:
- Blue: IV market ATM (from Step 1 sigma_ATM per tenor)
- Green: sigma_fair ATM (from Step 2)
- Orange dashed: Realized Vol (from Step 2 RV)
- Fill zone: red where IV > fair, green where IV < fair

X-axis: tenors (1W to 2Y). Y-axis: vol %.
Annotation: "Max opportunity: 6M (+0.74%)" on the tenor with largest |ecart|.
**Update frequency:** Every 60 seconds.

### Panel 3 — Smile Chart (middle center)

pyqtgraph chart with two smile curves for a selected tenor:
- Blue: market smile (Step 1 pillar IVs)
- Green: fair smile (sigma_fair_ATM + market RR/BF shape)

X-axis: delta pillars (10Dp, 25Dp, ATM, 25Dc, 10Dc). Y-axis: vol %.
Tenor selector: QComboBox [1M, 3M, 6M, 1Y].
**Update frequency:** On demand (tenor selection change) or every 5 minutes.

### Panel 4 — Greeks Portfolio (middle left)

Aggregated greeks from all open FOP positions:
```
Delta net:  -35,420 EUR
Vega net:   +12,300 EUR/vol%
Gamma net:  +1,840 EUR
Theta net:  -280 EUR/day
```
Plus delta hedge suggestion: "Buy 0.28 EUR future contracts" with action button.
**Update frequency:** Every 60 seconds (reqPositions + reqMktData tick 100).

### Panel 5 — P&L Decomposition (in positions table)

Additional columns per open position:
| P&L total | P&L delta | P&L vega | P&L theta | IV entry | IV current |

- P&L delta = delta_entry * (spot_now - spot_entry) * position * multiplier
- P&L theta = theta_daily * days_held * position * multiplier
- P&L vega = P&L total - P&L delta - P&L theta (residual)

**Update frequency:** Every 60 seconds.

### Panel 6 — RV vs IV Historical (bottom, toggleable)

pyqtgraph chart:
- Blue: IV ATM historical (daily snapshots)
- Orange: RV 21-day Yang-Zhang rolling
- Green fill: risk premium zone (IV - RV)

X-axis: dates (60 trading days). Y-axis: vol %.
**Update frequency:** Once per day.

### Panel 7 — Vol P&L Post-Trade (separate tab)

Analysis table for closed trades:
| Date in | Date out | Tenor | Strike | IV bought | RV realized | Vol edge | P&L vol | Verdict |

Vol edge = RV realized - IV bought. Positive = correct vol call.
**Update frequency:** On demand.

---

## Threading Architecture

```python
# Thread 1 (main): asyncio + Qt + IB
#   - IB streaming (ticks, positions, greeks)
#   - Qt UI rendering
#   - Sends raw data to Thread 2 via queue

# Thread 2 (vol engine): threading.Thread
#   - Receives option chain data from queue
#   - Runs CPU-bound calculations:
#     * BS inversion (scipy.optimize) per strike x tenor
#     * Yang-Zhang RV (numpy)
#     * GARCH calibration (arch library)
#     * sigma_fair combination
#     * Portfolio greeks aggregation
#   - Sends results back via queue
#   - Main thread polls result queue via QTimer
```

**Why a real thread:** The vol calculations are CPU-bound (100+ BS inversions, GARCH MLE optimization). Running them on the main asyncio thread would freeze the UI and delay tick processing.

---

## IB API Calls Summary

| Call | Data | Used by |
|---|---|---|
| `reqMktData(FUT)` | Spot EUR/USD | Step 1 |
| `reqSecDefOptParams` | Option chain strikes/expiries | Step 1 |
| `reqMktData(FOP, "100")` | IV, greeks per option | Step 1, Panel 1, Panel 4 |
| `reqHistoricalData(FUT, "1 day")` | OHLC for RV + GARCH | Step 2 |
| `reqPositions` | Open FOP positions | Step 2 (Layer C), Panel 4 |

---

## File Structure

```
src/
  services/
    vol_engine.py          — Thread 2: queue consumer, orchestrates calculations
  analytics/
    vol_mid.py             — Step 1: IV collection + delta pillar reconstruction
    vol_fair.py            — Step 2: RV + GARCH + book -> sigma_fair
    yang_zhang.py          — Yang-Zhang realized vol estimator
    garch.py               — GARCH(1,1) calibration + forward vol
    greeks.py              — Portfolio greeks aggregation
  ui/panels/
    vol_scanner_panel.py   — Panel 1: scanner table
    term_structure_panel.py — Panel 2: term structure chart
    smile_panel.py         — Panel 3: smile chart
    greeks_panel.py        — Panel 4: portfolio greeks
data/
  vol_mid_output.csv       — Step 1 output (refreshed every 30s)
  vol_fair_output.csv      — Step 2 output (refreshed daily)
  rv_history.csv           — Daily RV series
```

---

## Dependencies

| Library | Purpose |
|---|---|
| `ib_insync` | IB Gateway connectivity |
| `numpy` | Numerical computation |
| `scipy` | BS inversion (brentq/newton), optimization |
| `pandas` | Data manipulation |
| `arch` | GARCH(1,1) calibration |
| `QuantLib` | Advanced option pricing (future enhancement) |
| `pyqtgraph` | Real-time charts (term structure, smile) |

---

## Update Frequencies

| Frequency | Components |
|---|---|
| Real-time (<1s) | Spot tick chart, P&L total |
| Every 30s | Vol scanner (IV market per strike) |
| Every 60s | Term structure, greeks portfolio, P&L decomposition |
| Every 5min | Smile chart |
| Once/day | RV historical, GARCH recalibration (Step 2) |
