# Risk: greeks, limits, VaR, attribution, hedging

The risk framework turns a book of option legs into a small set of risk axes,
caps each axis against a stress-loss budget, decomposes realized P&L into greek
contributions, and keeps net delta inside a band. All the math is pure
(`core/` contract); the api/engine layers own the I/O.

## Greeks

Per-leg greeks come from Black-76 (`bs_greeks` in
[`structures.py`](../../src/engines/execution/structures.py)), aggregated signed
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

## VaR

[`core/risk/marginal_var.py`](../../src/core/risk/marginal_var.py) computes
historical VaR as a positive USD loss quantile and decomposes it per position by
Euler allocation:

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
