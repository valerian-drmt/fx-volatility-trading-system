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

## Limits: the trader's, and only the trader's

Owner rule (2026-11-13): **the app is not the trader**. The code computes no
limit, no reference level, no suggested size, and asks for no confirmation.
An order is refused on a fact (the market, the account, the broker) or on a
limit the trader typed, and on nothing else.

The trader's limits are five keys of the risk configuration. None is set by
default, and a key that is not set does not exist for the gate:

| Key | Refuses when | Failure code |
|---|---|---|
| `hard_max_delta_usd` | post-trade book \|delta\| is above it (after the package's hedge) | `cap_delta` |
| `hard_max_vega_usd_per_volpt` | post-trade book \|vega\| is above it | `cap_vega` |
| `hard_max_gamma_usd_per_pip` | post-trade book \|gamma\| is above it | `cap_gamma` |
| `hard_max_notional_eur` | the order's notional is above it | `cap_notional` |
| `hard_max_order_contracts` | the order's contracts are above it | `cap_contracts` |

When one of them refuses, the preview shows the limit, what the book uses of
it before and after, and the largest size that fits. It shows nothing of the
kind otherwise.

[`core/risk/greek_limits.py`](../../src/core/risk/greek_limits.py) still
projects a stress-loss appetite onto each greek (`GET /portfolio/greek-limits`).
It is a calculator: no screen of the desk reads it and the gate does not
either. Between 2026-11-13 and the owner rule the gate enforced those numbers
and refused every standard trade; they were removed from the order path.

## Pre-trade gate (facts, fail closed)

Every `POST /trade/submit` runs
[`core/risk/pretrade.py`](../../src/core/risk/pretrade.py) with inputs
collected by `api/orchestration/pretrade_gate.py`. It refuses with HTTP 400
`pretrade_gate_failed` and lists every reason at once; an input that cannot
be read is a refusal, never a pass.

| Check | Failure code | Source |
|---|---|---|
| kill switch off | `trading_halted`, `halt_flag_unreadable` | `config_scalar` risk/`trading_halted` |
| leg symbols allowed | `symbol_not_allowed` | `ALLOWED_SYMBOLS` (default `EUR,M6E`) |
| IB session is the account type `TRADING_MODE` promises (live mode) | `account_type_mismatch` | `ib_connection_state.account_type` |
| risk engine heartbeat younger than 30 s (live mode) | `risk_engine_stale` | Redis `heartbeat:risk_engine` |
| surface age below `max_iv_data_age_seconds` (live mode) | `surface_stale` | the latest surface's age at submit |
| surface free of butterfly arbitrage | `surface_arbitrage` | the surface's SSVI fit, carried on the preview as `surface_meta.butterfly_ok` (not reported passes) |
| the book can be read and the trade's greeks exist | `book_unavailable`, `trade_greeks_unavailable` | `open_position` greeks, preview `greeks_net` |
| the premium to pay is not above the account's net liquidation | `insufficient_funds` | preview `costs.total_trade_cost_usd` (at the size), latest account snapshot |
| the trader's own limits, when set | `cap_*` | the table above |

Mock-mode submits skip the three live-session checks and keep the rest.

**The preview says what the submit will do.** The preview runs the same gate
and its `state` is `valid_for_submit` only when nothing would refuse:
`submit_blockers` lists the gate's refusals (plus `gate_unavailable` when it
could not be asked) and `blocking_reasons` the two checks the submit re-runs
(regime not `pre_event`, signal still actionable). The other pre-submit
checks inform and never block: `no_arb_violation_on_legs` reports both flags
of the surface's fit (the calendar flag is raised on about a third of prod
surfaces), and `minimum_liquidity` is reported as not evaluated, because no
quoted size reaches the preview. A check that cannot be evaluated has
`passed: null`.

**The preview's figures are those of the whole order.** Premium, maximum
loss, cost and greeks are one unit of the structure times the quantity
traded; `pricing.premium_per_unit_usd` is one unit. A leg is priced to the
expiry of the contract it trades: the listed monthly option of the tenor's
month (two Fridays before the third Wednesday), not the tenor's round day
count. Commission comes from one table,
`core.trade_preview.COMMISSION_USD` (option 2.00, 6E 2.40, M6E 0.60 USD per
contract and side), also used for the hedge.

In the execution engine, every order goes through one door
(`engines/execution/door.py`): the kill switch and the ready gate, whatever
path placed it. `live_submit` adds two checks before any leg is placed: the
symbol allow-list again, and a **price collar**: the live marketable limit
of every leg must sit within `LIVE_SUBMIT_COLLAR_PCT` (default 50 %) of the
preview price, or the whole structure is refused and the submit claim
released.

Between Arm and Submit, `core/execution/revalidation.py` re-runs the gates
that can change while the operator sits on a preview: not actioned, not
expired, signal still actionable, surface fresh, regime not `pre_event`.
The signal gate uses one bar at Arm and at Submit (`|z| >= z_threshold_min`,
`SIGNAL_ACTIONABLE_FACTOR = 1.0`). The surface gate fails closed: a preview
priced on a surface older than `max_iv_data_age_seconds` is refused with
`surface_stale`, and a preview with no recorded age with
`surface_age_unknown`.

## Kill switch

One durable flag (`config_scalar` namespace `risk`, name `trading_halted`),
set through `POST /api/v1/admin/halt {reason}` (trader cookie) and cleared
with `DELETE /api/v1/admin/halt {reason}`; `GET /api/v1/admin/halt` is public
so the desk can badge it. Setting it:

1. writes the flag and a `trading_halted` trade event (who, when, why);
2. mirrors it to Redis (`trading:halted`) and publishes on `system_alerts`;
3. calls the execution engine's `POST /internal/halt`, which cancels every
   working order of the IB session and issues IB's global cancel.

While halted, nothing leaves the execution engine: the door every order
passes (`engines/execution/door.py`) answers **423**, for the routes
(`/orders`, `/structure/submit`, `/structure/rollback`, the close) as for the
package hedge sent after a fill and its retry loop. The api's `/trade/submit`
fails the gate with `trading_halted`. A database that cannot be read counts
as halted (503). Resuming is a deliberate second call with its own reason;
nothing resumes on its own.

## After entry: the monitor alerts, the trader acts

The limits act once, at submit. After entry the position monitor (every
60 s) marks each booked position, attributes its P&L and evaluates the exit
rules. An `EXIT` decision is an `ExitAlert` row and a line on the screen.
Nothing is closed and nothing is hedged automatically: the automatic exit
and the monitor's delta hedges were removed (#493), the trader closes and
hedges.

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

A hedge is the trader's choice on the ticket. With "Delta hedge" ticked the
preview sizes the futures on the server's own delta
([`core/execution/package_hedge.py`](../../src/core/execution/package_hedge.py),
`plan_hedge`): whole 6E futures, so the residual sits within half a future
on either side of zero and changes sign as the size grows. The ticket shows
both numbers: "Delta" (the options' own) and "Delta after hedge".

The structure and its hedge are one package: the gate judges the residual,
and the engine sends the futures after each option fill, for the share
filled (`engines/execution/package_hedge.py`). Nothing is sent before a
fill. A hedge that cannot be placed is a rejected order with a critical
event, retried by the hedge loop. On a multi-leg structure filled unevenly
the share is counted in contracts, an approximation.

[`core/positions/delta_hedge.py`](../../src/core/positions/delta_hedge.py)
is the pure decision function of the former monitor hedge
(`check_delta_hedge_needed`). Nothing calls it on the order path.
