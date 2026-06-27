# Greek limit framework — implementation brief

Target: FX vol options risk dashboard (EUR/USD, delta-hedged book).
Goal: replace three independent ad-hoc greek caps with one stress-loss budget projected onto each axis, fix two structural bugs, add per-tenor vega and a scenario grid.

Reference state at time of writing: NAV ≈ $812k, net delta +$484.7k, net vega +$5.1k/vol, net gamma +$2.3k/pip.

---

## 0. Core principle

Do not cap greeks against NAV directly. A greek is a sensitivity; its risk is `sensitivity × shock`. Define one daily stress-loss budget `L* = alpha * NAV_base`, then project it onto each axis by inverting the shock. Caps are derived, not configured.

```
greek_cap = (beta_axis * L*) / shock_axis
```

---

## 1. Config constants

```python
ALPHA          = 0.05      # daily stress-loss appetite, fraction of capital base
BETA           = {         # allocation of L* across axes (standalone, sums to 1.0)
    "delta":  0.15,
    "vega":   0.50,        # largest: vega is the intended risk of a vol book
    "gamma":  0.25,
    "cross":  0.10,        # vanna + volga
}
S_REF          = 1.08      # current EUR/USD spot, pull live
# base shocks (calm regime); scaled by regime_multiplier at runtime
SHOCK_SPOT     = 0.025     # 2.5% ≈ 270 pips, 1-day stress
SHOCK_VOL      = 4.0       # vol points, 1-day stress
PIP            = 1e-4
```

---

## 2. Cap formulas

```python
def caps(nav_base, S, regime_mult=1.0):
    L      = ALPHA * nav_base
    s      = SHOCK_SPOT * regime_mult       # caps auto-tighten as regime_mult rises
    v      = SHOCK_VOL  * regime_mult

    delta_cap_usd   = BETA["delta"] * L / s
    vega_cap_total  = BETA["vega"]  * L / v
    gamma_cap_pip   = 2 * BETA["gamma"] * L / (s**2 * S * 1e4)
    cross_budget    = BETA["cross"] * L     # enforced via the scenario grid, not standalone
    return dict(delta=delta_cap_usd, vega=vega_cap_total,
                gamma=gamma_cap_pip, cross=cross_budget)
```

Sanity values at NAV=$812k, S=1.08, regime_mult=1.0: delta ≈ $243.6k, vega ≈ $5,075/vol, gamma ≈ $3,007/pip.

Gamma P&L convention used in the derivation (verify the live gamma feed matches): `gamma_pip` = change in delta-equivalent USD notional per 1-pip spot move. Stress P&L over `n` pips = `0.5 * gamma_pip * n^2 * (PIP / S)`.

---

## 3. Changes vs current implementation

| Item | Current (wrong) | Replace with |
|---|---|---|
| Delta cap | 10% NAV = $81k | `BETA.delta * L / s` ≈ $243.6k |
| Vega cap | hardcoded $5k | `BETA.vega * L / v` (≈ same; keep methodology, drop hardcode) |
| Gamma cap | delta_band / 100 pips = $812 | `2*BETA.gamma*L / (s^2 * S * 1e4)` ≈ $3,007/pip |
| Capital base | live NAV | smoothed base (see §6) |

The vega rule was already correct in spirit (`4-vol move ≤ 2% NAV` ≡ `alpha=0.05, beta_vega=0.5`). Generalize it to all three axes instead of keeping two ad-hoc rules.

Separate the delta band into two distinct objects — do not conflate:
- `delta_rehedge_trigger`: operational, set by transaction cost vs gamma. Tunable, smaller than the cap.
- `delta_cap_usd`: hard risk limit from the formula above. Constraint: `trigger < cap`.

Gamma is not a loss axis for a long-gamma book — its cost is theta. Track `theta ≈ -0.5 * gamma_r * sigma_daily^2` as the carry, but size the cap off the convexity formula above. Do not derive gamma from the delta band.

---

## 4. Per-tenor vega

Total book vega hides curve risk (long front / short back nets to ~0 but carries twist risk). Add a vega ladder:

```python
TENORS   = ["1W", "1M", "3M", "6M", "1Y", "2Y"]
V_SHOCK  = {"1W": 8.0, "1M": 6.0, "3M": 5.0, "6M": 4.0, "1Y": 3.0, "2Y": 2.5}  # front-end vol-of-vol >> back-end
BETA_V_T = {t: BETA["vega"]/len(TENORS) for t in TENORS}  # tune per desk view

def vega_cap_bucket(t, L):
    return BETA_V_T[t] * L / V_SHOCK[t]
```

Enforce per-bucket caps AND a total parallel-shift cap. Add explicit risk-reversal (vanna) and butterfly (volga) exposure limits — EUR/USD has strong spot-vol correlation, so smile risk is not captured by ATM vega alone.

---

## 5. Scenario grid (master limit)

Standalone greek caps are real-time guardrails. The binding limit is the worst cell of a full-reval spot × vol grid, because joint adverse scenarios add losses that no single standalone cap catches. For a long-vol book the worst cell is typically vol-down + spot-quiet (vega loss + theta, no gamma offset).

```python
SPOT_GRID = [-0.04, -0.025, -0.01, 0, +0.01, +0.025, +0.04]   # × regime_mult
VOL_GRID  = [-6, -4, -2, 0, +2, +4, +6]                       # × regime_mult

def scenario_pnl(book, dS, dVol):   # full reprice, not greek approximation
    ...
def worst_cell(book):
    return min(scenario_pnl(book, dS, dV) for dS in SPOT_GRID for dV in VOL_GRID)
# Hard limit: worst_cell(book) >= -L*
```

Reconcile so the worst grid cell ≈ -L*; if standalone caps are all at 100% but the grid says -1.5·L*, the grid binds first.

---

## 6. NAV anchor (procyclicality fix — high priority)

Scaling caps to live NAV is a positive feedback loop: a drawdown shrinks every cap simultaneously, forcing de-risking into the adverse move. Anchor `L*` to a slow-moving base:

```python
nav_base = max(high_water_mark * 0.9, ewma(nav_live, halflife="20d"))
```

Use `nav_base` for cap sizing; keep `nav_live` only for margin display. This decouples the limit denominator from the same shock it is meant to protect against.

---

## 7. Bugs to fix

- Dashboard cap column is mis-wired. Panel shows Δ cap $5.0k (the $5k vega number landed in the delta row; the "100%" is a clamped display of ~9,700%), vega cap $48k, gamma cap $20.4k — none match the intended config. Audit the cap-to-row assignment and the utilization clamp (show true %, flag >100% distinctly rather than clamping to 100).
- Utilization staleness: ensure the greek snapshot and the spot/NAV pull share a timestamp. Stale greeks vs live spot produce silently wrong utilization. Reject or flag any frame where `t(greeks) - t(spot) > threshold`.

---

## 8. Regime scaling

`regime_mult = f(prevailing_vol)` — e.g. `regime_mult = current_implied_vol / calm_baseline_vol`, clamped to [1, 3]. Feed it into §2 and §5 so shocks rise and caps fall as vol rises. Optionally maintain two discrete limit sets (normal / stressed) with a hard switch on a vol trigger, and cut `ALPHA` in the stressed set.

---

## 9. Acceptance criteria

- [ ] All three greek caps come from the formulas in §2; no hardcoded cap numbers remain.
- [ ] At NAV=$812k, S=1.08, regime_mult=1: delta cap ≈ $243.6k, vega ≈ $5,075, gamma ≈ $3,007/pip (±1%).
- [ ] Caps recompute on every NAV/spot/regime update; gamma cap scales with `1/s^2`, delta and vega with `1/s` and `1/v`.
- [ ] `delta_rehedge_trigger < delta_cap_usd` enforced.
- [ ] Vega shown per tenor bucket + total; vanna and volga limits present.
- [ ] Scenario grid computed by full reprice; `worst_cell >= -L*` is the master check.
- [ ] Cap sizing uses `nav_base` (smoothed), not `nav_live`.
- [ ] Dashboard cap-to-row mapping corrected; utilization >100% flagged, not clamped.
- [ ] Unit test: a long-1M / short-1Y vega book with ~0 total vega still trips a per-bucket cap.
- [ ] Unit test: simulated NAV drawdown does not tighten `nav_base` within the same session.

---

## 10. Priority order

1. NAV-anchor fix (§6) — largest structural risk.
2. Dashboard wiring + utilization clamp (§7).
3. Cap formulas (§2, §3).
4. Per-tenor vega (§4).
5. Scenario grid (§5).
6. Regime scaling (§8).
