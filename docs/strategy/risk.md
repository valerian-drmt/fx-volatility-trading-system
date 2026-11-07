# Risk: greeks, limits, VaR, attribution, hedging

The risk framework turns a book of option legs into a small set of risk axes,
caps each axis against a stress-loss budget, decomposes realized P&L into greek
contributions, and keeps net delta inside a band. All the math is pure
(`core/` contract); the api/engine layers own the I/O.

## Greeks

Per-leg greeks come from Black-76 (`bs_greeks` in
[`core/structures.py`](../../src/core/structures.py)), aggregated signed
across legs. The book tracks six sensitivities:

| Greek | Unit | Meaning |
|---|---|---|
| Δ delta | USD cash-delta | spot exposure |
| Γ gamma | USD / pip | delta drift per 1-pip spot move |
| V vega | USD / vol point | exposure to a 1% IV change |
| Θ theta | USD / day | time decay |
| vanna | USD / vp·fig | dVega/dSpot — the skew cross-greek |
| volga | USD / vp | dVega/dVol — the convexity cross-greek |

Vanna and volga are the risk-reversal and butterfly exposures respectively; the
order builder surfaces all six as book *before → after* on every ticket.

## Computed greek limits

[`core/risk/greek_limits.py`](../../src/core/risk/greek_limits.py) does **not**
hardcode caps. One daily stress-loss appetite `L* = alpha × nav_base` is
projected onto each axis by inverting that axis' shock:

```
greek_cap = beta_axis * L* / shock_axis
```

`compute_caps(nav_base, spot, regime_mult, params)` returns `GreekCaps`:

- `delta_usd  = beta_delta * L* / shock_spot`
- `vega_usd   = beta_vega  * L* / shock_vol`
- `gamma_pip  = 2 * beta_gamma * L* / (shock_spot² · spot · 1e4)`
- `cross_usd  = beta_cross * L*` (vanna+volga, scenario-grid enforced)

Defaults: `alpha=0.05`, `beta = {delta .15, vega .50, gamma .25, cross .10}`,
`shock_spot=2.5%`, `shock_vol=4 vol pts`. Vega gets the largest share — it is the
*intended* risk of a vol book.

Two anti-procyclicality guards:

- **`nav_base`** is a slow anchor, `max(hwm × 0.9, ewma(nav, halflife=20d))`, not
  the live NAV — a drawdown does not instantly tighten every cap.
- **`regime_mult`** = `clamp(current_vol / calm_baseline, 1, 3)` scales shocks up
  (caps down) as vol rises.

All eight policy values are editable in the Risk settings panel
(`CONFIG_DEFAULTS` / `CONFIG_META`), overlaid via `params=`.

## Pre-trade gate (enforced, fail closed)

Since #315 every `POST /trade/submit` runs
[`core/risk/pretrade.py`](../../src/core/risk/pretrade.py) with inputs
collected by `api/orchestration/pretrade_gate.py`. The gate refuses with HTTP
400 `pretrade_gate_failed` and lists every failing reason; an input that
cannot be read is a failure, never a pass.

| Check | Failure code | Source |
|---|---|---|
| kill switch off | `trading_halted`, `halt_flag_unreadable` | `config_scalar` risk/`trading_halted` |
| leg symbols allowed | `symbol_not_allowed` | `ALLOWED_SYMBOLS` (default `EUR,M6E`) |
| IB session is the account type `TRADING_MODE` promises (live mode) | `account_type_mismatch` | `ib_connection_state.account_type` |
| risk engine heartbeat younger than 30 s (live mode) | `risk_engine_stale` | Redis `heartbeat:risk_engine` |
| surface age below `max_iv_data_age_seconds` (live mode) | `surface_stale` | preview `surface_age_seconds` |
| surface not flagged arbitrageable | `surface_arbitrage` | surface meta `butterfly_ok` (unknown passes) |
| every open position priced | `book_partially_priced` | `open_position` greeks |
| post-trade \|delta\| ≤ cap, \|vega\| ≤ cap, \|gamma\| ≤ cap | `cap_delta`, `cap_vega`, `cap_gamma` | book greeks + preview `greeks_net` vs `compute_caps` |
| caps established (NAV history + spot) | `caps_not_established`, `caps_unavailable` | `api.orchestration.greek_caps` |
| structure notional ≤ `max_notional_eur` (default 5 M EUR) | `cap_notional` | `config_scalar` risk namespace |

The caps are the same numbers the Risk settings panel shows
(`GET /portfolio/greek-limits` and the gate call one helper). Mock-mode
submits skip the three live-session checks and keep the rest.

In the execution engine, `live_submit` adds two checks before any leg is
placed: the symbol allow-list again, and a **price collar**: the live
marketable limit of every leg must sit within `LIVE_SUBMIT_COLLAR_PCT`
(default 50 %) of the preview premium, or the whole structure is refused and
the submit claim released. Not assessed, and recorded as a waiver: quoted
size / liquidity (the preview has no per-leg depth yet).

Between Arm and Submit, `core/execution/revalidation.py` re-runs the gates
that can change while the operator sits on a preview: not actioned, not
expired, signal still actionable, surface fresh, regime not `pre_event`.
The surface gate fails closed since #339 (audit EX-08): a preview priced on
a surface older than `max_iv_data_age_seconds` is refused with
`surface_stale`, and a preview with no recorded age with
`surface_age_unknown`. Like the pre-trade gate's `surface_stale`, it runs in
live mode; mock and paper submits pass `max_iv_age_seconds=None`.

## Kill switch

One durable flag (`config_scalar` namespace `risk`, name `trading_halted`),
set through `POST /api/v1/admin/halt {reason}` (trader cookie) and cleared
with `DELETE /api/v1/admin/halt {reason}`; `GET /api/v1/admin/halt` is public
so the desk can badge it. Setting it:

1. writes the flag and a `trading_halted` trade event (who, when, why);
2. mirrors it to Redis (`trading:halted`) and publishes on `system_alerts`;
3. calls the execution engine's `POST /internal/halt`, which cancels every
   working order of the IB session and issues IB's global cancel.

While halted, every order-creating route of the execution engine (`/orders`,
`/structure/submit`, `/structure/rollback`, both closes, `/hedge`) answers
**423**, the api's `/trade/submit` fails the gate with `trading_halted`, and
the position monitor's automatic hedge is refused by the engine. A database
that cannot be read counts as halted (503). Resuming is a deliberate second
call with its own reason; nothing resumes on its own.

## VaR

Methodology, as served by `GET /portfolio/var` and `GET /portfolio/marginal-var`
(#316, audit 03#3 / 03#4 / 03#6):

| | |
|---|---|
| Horizon | **1 trading day**. No horizon scaling anywhere: a full-revaluation VaR does not follow sqrt-t, so the desk shows the 1-day figures only (the old weekly / monthly / yearly rows were sqrt-t multiples and are gone). |
| Confidence | VaR 95 % and VaR 99 % = the 5 % / 1 % quantiles of the P&L series; ES 99 % = mean of the P&L at or beyond the 1 % quantile. |
| Percentile | linear interpolation between order statistics, on finite values only (`core/risk/var_stats.py`, the single implementation). |
| Preferred method | `historical-simulation`: ~1 year of daily EURUSD closes from the bar cache, each session replayed as one scenario (`Δspot` = close-to-close return in bp, `Δvol` = change of the 21-session realised vol in vol points, a proxy for implied) through **today's** book, full Black-Scholes revaluation per scenario (`core/risk/hist_var.py`). Needs at least 60 scenarios. |
| Fallback method | `account-history`: the empirical distribution of daily net-liq changes over the last `var_lookback_days` sessions (default 504), skipping any step longer than `var_max_gap_days` (default 3: a downtime or a capital move is not a daily loss). Needs 5 observations; fields are `null` before that. |
| Sign | `var_stats` computes **positive USD losses**, clamped (a tail that is still a gain reads 0, ES ≥ VaR 99 ≥ VaR 95). `/var` serves them as P&L quantiles (negative = loss, `convention: "pnl_quantile"`) because the desk plots them on the P&L axis; `/marginal-var` serves the positive losses (`convention: "loss"`). |
| What was not priced | every payload carries `dropped: [{id, reason}]` (`no_iv`, `no_expiry`, `unparseable_symbol`, `no_spot`, `non_finite_pnl`) and `fallback` (`{"spot": "strike_proxy"}` when no future mark gave a spot and an option strike stands in). `n_positions` counts the priced rows only. |
| Diversification | `1 − VaR_p / Σ standalone`, clamped to [0, 100] %. |

[`core/risk/marginal_var.py`](../../src/core/risk/marginal_var.py) decomposes
the 1-day 99 % VaR per position by Euler allocation:

```
comp_i = VaR_p · cov(pnl_i, pnl_p) / var(pnl_p)
```

which sums to `VaR_p`. Standalone VaR is each position's own loss quantile;
`diversification = 1 − VaR_p / Σ standalone`.

## P&L attribution (Taylor)

`GET /pnl-attribution` in
[`api/routers/portfolio_panel.py`](../../src/api/routers/portfolio_panel.py)
decomposes realized P&L over a lookback window per position, a Taylor expansion
**anchored on the window-start (t-1) greeks**:

```
actual_pnl = pnl_now − pnl_then
delta_pnl  = δ_t-1 × (spot_now − spot_then)
gamma_pnl  = 0.5 × Γ_t-1 × (spot_now − spot_then)²
vega_pnl   = V_t-1 × (iv_now − iv_then)      [vol points]
theta_pnl  = Θ_t-1 × Δt_days
residual   = actual_pnl − (delta + gamma + vega + theta)
```

Anchoring on the *entry* greek (not `δ_now`) is deliberate: over 24h an option's
delta drifts materially, so `δ_now·dS` would overshoot and inflate the residual.
With the t-1 anchor the residual holds only genuine higher-order (vanna/volga)
convexity. The endpoint also pivots the per-position terms by tenor/structure into
an attribution matrix; each row's residual foots to `actual − explained`.

## Delta hedging

[`core/positions/delta_hedge.py`](../../src/core/positions/delta_hedge.py) is a
pure decision function. `check_delta_hedge_needed(...)` fires a hedge when
`|delta_unhedged| > threshold` (default 0.05) **and** outside a cooldown
(default 300s). Hedge qty = `round(|delta|)`; if it rounds to 0 → skip; side is
opposite the imbalance (SELL futures when delta > 0). It returns a
`HedgeDecision` with the qty, side, and post-hedge residual, or a `skip_reason`
(`below_threshold` / `rounded_to_zero` / `cooldown`).

Hedge behaviour is configured by `DeltaHedgeConfig` in
[`vol_params.py`](../../src/core/config/vol_params.py): `mode`
(`static` / `threshold` / `scheduled`), `threshold_delta`,
`scheduled_interval_minutes`. In the order builder a delta hedge can be *bundled*:
a second linked `6E` futures order fires right after the structure to flatten its
net delta.
