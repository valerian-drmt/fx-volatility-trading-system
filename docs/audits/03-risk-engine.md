# Audit 3 — Risk Engine & Limits

## Why

The risk engine is the safety net between signals and the market. If greeks aggregate
wrong, if VaR understates tail exposure, or if a limit breach fails to block a trade,
the system can build positions far beyond intent — and nobody notices until the P&L does.
Unlike a pricing bug, a risk bug only shows up when it's expensive.

## Scope

- `src/engines/risk/` — engine + main loop.
- `src/core/risk/greeks.py` — consumed aggregation primitives (correctness audited in
  Audit 2; *usage* audited here).
- `docs/strategy/` greek-limits spec — the audit checks **implementation vs spec**.
- Risk-related persistence (risk snapshots tables) and Redis keys/channels.
- The risk view consumers: `/ws/risk`, risk router, frontend Risk view (display-level
  checks stay in Audit 9; contract-level checks here).

## What to check

### Aggregation correctness
- [ ] Portfolio greeks: correct signs for short positions, correct contract multipliers,
      correct currency conversion into the base currency before summing.
- [ ] Position source of truth: does the engine aggregate from IB positions, internal
      state, or DB — and what happens when they disagree?
- [ ] Marginal VaR: methodology consistent with total VaR; components sum sanely.

### VaR methodology
- [ ] Horizon, confidence level, and return-window choices documented and consistent
      between code and `docs/strategy/`.
- [ ] Missing/stale market data handling: does VaR silently shrink when inputs are
      missing (fewer risk factors = fake diversification)?
- [ ] Revaluation approach (full reval vs delta-gamma) matches what the docs claim.

### Limits & enforcement
- [ ] Every limit in the greek-limits spec is actually enforced in code — build a
      spec-vs-code matrix.
- [ ] Limit breach behavior: block new trades only, or also alert/flatten? Is a breach
      *fail-closed* (reject when risk engine is down/stale)?
- [ ] Staleness: if the risk engine stops publishing, do execution paths keep trading
      on stale risk numbers? What is the max tolerated staleness?

### Failure modes
- [ ] Engine restart: state rebuild correctness (positions re-synced before first
      risk publish?).
- [ ] IB disconnect mid-computation: partial position snapshots must not publish.
- [ ] NaN/Inf propagation: one bad option quote must not poison portfolio VaR.

## Method

Spec-vs-code matrix first (from `docs/strategy/`), then line review of the aggregation
path, then scenario walkthroughs (short straddle, mixed-currency book, stale feed) with
hand-computed expected values.

## Deliverable & exit criteria

- Findings report + the spec-vs-code limits matrix committed alongside the spec.
- **Exit:** every spec'd limit maps to an enforcement point; fail-closed behavior
  verified on stale/absent risk data; zero Blocker/High open.