# Units and conventions

Every number that crosses a module boundary has one unit, written here. The
code's single source of truth is `src/core/units.py` (premium / greek / P&L
convention) and `src/core/risk/greek_scaling.py` (the per-position scaling
both the risk engine's persisted rows and its published aggregate use since
#313). This page is the table the audit asked for (02#7, 03#1), lifted from
the findings appendix and kept current.

## Greeks on an open position (`open_position`, `open_position_history`, `risk_update`)

`qty` is signed (long +, short −), `mult` is the contract notional in EUR
(125 000 for 6E and EUU options, 12 500 for M6E), `F` the published spot.

| Column / key | Formula | Unit | Reads as |
|---|---|---|---|
| `delta_eur` (payload only) | bs_delta × qty × mult | EUR | EUR-equivalent exposure; contract equivalents = ÷ 125 000 |
| `delta_usd` | delta_eur × F | USD | dollar exposure; what the "$ delta" cells show |
| `gamma_usd` | bs_gamma × qty × mult × 0.0001 | $ per pip | change of `delta_eur` for a 1-pip spot move |
| `vega_usd` | bs_vega × qty × mult × 0.01 | $ per vol point | P&L for +1 % IV |
| `theta_usd` | bs_theta × qty × mult | $ per day | `bs_theta` is already per calendar day |
| `vanna_usd` | bs_vanna × qty × mult × 0.01 | $ per vol point | change of `delta_eur` for +1 % IV |
| `volga_usd` | bs_volga × qty × mult × 0.01² | $ per vol point² | change of `vega_usd` for +1 % IV |
| `iv` | | fraction | 0.0605 = 6.05 % (the surface cells are fractions too) |
| `market_price` | | price points | IB's quote unit; USD = points × mult |
| futures | delta_eur = qty × mult, rest 0 | | |

**Cut-over 2026-11-06 (#313).** Before, `delta_usd` held `delta_eur` (no spot
factor, ~12 % low at 1.12) and the published aggregate carried no multiplier
at all. History rows written before the deploy keep the old value; the
24-hour P&L attribution reads `delta_usd / F` for the δ·dS term, so a window
straddling the cut-over understates the delta term by the spot factor for
one day.

## Booked positions (`booked_position_metric_history`, mock-mode book)

| Column | Unit |
|---|---|
| `current_delta_unhedged` | contract equivalents (1.0 = one 6E); `None` when the live delta could not be computed (#310) |
| `current_gamma_usd_per_pip2` | $ per pip²: gamma P&L = ½ × Γ × (ΔS in pips)² |
| `current_vega_usd_per_volpt` | $ per vol point |
| `current_theta_usd_per_day` | $ per day |
| `iv_avg_legs_pct` | percent |

## P&L attribution (`/portfolio/pnl-attribution`)

| Term | Formula | Inputs |
|---|---|---|
| δ | (delta_usd_t-1 ÷ F_t-1) × ΔF | ΔF in price units |
| ½Γ | ½ × (gamma_usd ÷ 0.0001) × ΔF² | IB rows; booked rows use gamma per pip² ÷ 0.0001² |
| V | vega_usd × Δσ | Δσ in vol points (IB `iv` fraction × 100) |
| Θ | theta_usd × Δt | Δt in days |
| residual | actual − (δ + ½Γ + V + Θ) | vanna, volga, higher order |

## Stress and ladders (`core.risk.stress.reval_book`)

`reval_book(..., output=...)` returns per-shock sums in the same units as the
table above: `delta` is EUR-equivalent (the ladder multiplies by the current
spot to print `delta_usd`), `gamma` $ per pip, `vega` $ per vol point,
`vanna` $ per vol point, `volga` $ per vol point², `theta` $ per day, `pnl`
in USD.

A vol shock that would push an option's IV through zero prices the option
at `IV_FLOOR` (0.1 vol pt) instead of skipping it (audit R3-19, #339): the
leg then contributes its near-intrinsic value and its delta to the most
violent vol-down scenarios rather than a silent zero.

## Preview → entry snapshot → monitor (`core.units`)

| Quantity | Formula | Unit |
|---|---|---|
| premium / mark | bs_price × 125 000 × qty, signed by side | USD |
| `vega_usd_per_volpt` | bs_vega × 0.01 × ±qty × 125 000 | $ per vol point |
| `gamma_usd_per_pip2` | bs_gamma × 0.0001² × ±qty × 125 000 | $ per pip² |
| `theta_usd_per_day` | per-day theta × ±qty × 125 000 | $ per day |
| `delta_usd` | bs_delta × spot × ±qty × 125 000 | USD exposure |
| IB prices (`lmtPrice`, fills, `preview_price`) | | price points; USD = points × 125 000 × qty |

## Vol surface and models

| Boundary | Quantity | Unit |
|---|---|---|
| IB chain → `chain_fetcher` | delta | call-delta in [0, 1]; puts mapped 1 + δ_put |
| surface grid cell `{tenor: {delta: {iv, strike}}}` | iv | fraction |
| surface meta `_rv_full_pct`, `_har.*_pct`, `_garch.*_pct`, `_fair_q.*_pct`, `rv_pct` | vol | percent, on the same payload as the fraction cells |
| `fit_svi` / `svi_iv` | k, w, T | ln(K/F), σ²T, calendar years |
| `svi_curve` / `ssvi_curve_for_tenor` | iv_pct | percent |
| `tenors` | time | integer calendar days; interpolation linear in w = iv² × dte |
| `yang_zhang_rv_pct`, `har_rv`, `garch` | RV, σ | annualised percent, √252 |
| `vrp` | VRP | additive vol points |
| `core.pricing.bs` | F, K, T, σ, outputs | price units, calendar years, fraction; price in points, theta per day, vega per 1.00 vol |
| `core.trade_preview.bs_greeks` | theta | per year, ÷ 365 at aggregation |
| `pca_engine` | features, z | IV vector in percent; z dimensionless |
| `regime_engine` | vol_level, vov, term_slope | percent points; z dimensionless |

## Frontend

`adaptPositions` shows `delta_usd`, `gamma_usd`, `vega_usd`, `theta_usd` as
dollars (auto-scaled) and `vanna_usd`, `volga_usd` in $k; `iv` × 100 to
percent. The order builder's preview delta is `delta % × qty × notional ×
spot`, the same USD exposure as `delta_usd`.

## Tests

- `tests/unit/core/risk/test_greek_scaling.py`: the formulas above, futures
  and micros, and that the published aggregate equals the sum of the
  persisted rows.
- `tests/unit/core/test_units_convention.py`: preview premium = position mark
  at entry, vega / gamma / theta conventions of `core.units`.
