# Audit 2 — Quant Model Correctness

## Why

Every signal, price, and greek downstream depends on `src/core/`. A sign error in
Black-Scholes, a mis-parameterized SVI slice, or a mishandled day-count doesn't crash —
it silently produces wrong trades. Pure-math bugs are exactly the ones tests miss when
tests were written by the same author as the code, so this audit re-derives and
cross-checks against independent references.

## Scope

`src/core/` only (pure Python, no I/O):

- `pricing/bs.py` — Black-Scholes for FX (Garman–Kohlhagen conventions).
- `risk/greeks.py` — Δ/Γ/V analytics.
- `vol/svi.py`, `ssvi.py`, `pchip_smile.py` — smile calibration & interpolation.
- `vol/garch.py`, `har_rv.py`, `yang_zhang.py`, `vrp.py` — realized/forecast vol.
- `vol/fair_term.py`, `surface_z.py`, `tenors.py` — term structure & surface assembly.
- `vol/gmm_regime.py`, `regime_engine.py`, `pca_engine.py`, `feature_enrichment.py` —
  regime & factor models.

## What to check

### Pricing & greeks
- [ ] FX conventions: domestic vs foreign rate placement, premium currency, delta
      convention (spot/forward, premium-adjusted?) consistent with how IB quotes are used.
- [ ] Greeks match finite-difference bumps of the pricer within tolerance (property test).
- [ ] Edge cases: T→0, deep ITM/OTM, zero/negative rates, vol→0 — no NaN/Inf leaking out.

### Vol surface
- [ ] SVI/SSVI calibration: parameter bounds enforced; **static arbitrage checks**
      (butterfly: g(k) ≥ 0; calendar: total variance non-decreasing in T).
- [ ] PCHIP smile: monotonicity/extrapolation behavior at wings — what happens beyond
      the last quoted strike?
- [ ] Tenor interpolation (`fair_term`, `tenors`): day-count and calendar handling around
      weekends/holidays; the "markets closed → serve last full grid" path returns a
      *consistent* surface (recent fixes f301a560, 0f4be379 — verify no sibling bugs).
- [ ] Units audited end-to-end: variance vs vol, annualized vs per-period, log-moneyness
      vs delta — one conversion table produced and checked at every module boundary.

### Time-series models
- [ ] GARCH/HAR-RV: input return construction (log vs simple, gap handling), forecast
      horizon alignment with option tenors, fallback behavior when fit fails to converge.
- [ ] Yang-Zhang: OHLC edge cases (limit days, missing bars), window handling.
- [ ] VRP: consistent variance units between implied and realized legs.

### Regime / PCA
- [ ] Standardization fit on training window only (no look-ahead in feature enrichment).
- [ ] GMM/PCA: determinism (seeds), stability of component signs across refits, behavior
      with degenerate input (constant features, short history).
- [ ] Signal thresholds: units and directions match what the trade router expects.

## Method

Independent re-derivation against references (Gatheral for SVI/SSVI no-arbitrage
conditions, standard Garman–Kohlhagen formulas), plus targeted property-based tests
(hypothesis) written *during* the audit as evidence. Cross-check a sample of outputs
against a reference implementation (e.g. hand-computed BS values, `scipy` where relevant).

## Deliverable & exit criteria

- Findings report + a units/conventions table committed to `docs/vol-modeling/`.
- New property tests kept in `tests/unit/core/` as regression armor.
- **Exit:** all greeks pass finite-difference checks; surface passes static-arbitrage
  spot checks on real calibrated params; zero Blocker/High open.