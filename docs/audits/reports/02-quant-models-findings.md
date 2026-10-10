# Audit 2 — Quant Model Correctness — Findings

- **Scope:** `src/core/` pure-math modules per [docs/audits/02-quant-models.md](../02-quant-models.md):
  `pricing/bs.py`, `risk/greeks.py`, all of `core/vol/`, plus the direct consumers that define the
  module-boundary conventions (`core/units.py`, `core/trade_preview.py`, `core/positions/position_pricing.py`)
  and, read-only, the engine call sites that feed them (delta encoding, SSVI theta keys, surface shape).
- **Method:** line-by-line review against Gatheral (SVI/SSVI) and standard Garman–Kohlhagen / Black-76,
  plus a numerical verification script (finite-difference greeks, analytic-vs-numeric butterfly g(k),
  randomized parameter search, NaN-leak probes) run with `PYTHONPATH=src` (Python 3.11.9,
  numpy 2.4.4, scipy 1.17.0, arch 8.0.0, sklearn 1.6.1). Script kept in the session scratchpad
  (`audit2_checks.py`); its outputs are quoted verbatim in the evidence sections.
- **No code was modified.** Findings only.

## Executive summary

The core math is largely sound. **All six analytic greeks (delta, gamma, vega, theta, vanna,
volga) match central finite differences of the pricer to ≤ 4.5e-5 across a 96-point grid**
(worst case is FD noise on gamma), put–call parity holds to machine precision, the SVI
butterfly `g(k)` analytic derivatives match numerical differentiation to 9e-9, and the
delta-pillar conventions (IB put delta → call-delta `1+δp` → pillar targets `25dp=0.75` →
SVI delta→k inversion) are consistent end-to-end. PCA is deterministic (SVD), GMM is seeded,
and no look-ahead was found in the core standardization paths.

The two High findings are convention/wiring bugs, not formula errors:

1. **Degenerate-input convention in `core/pricing/bs.py`**: every function returns `0.0`
   when `T <= 0` **or** `sigma <= 0`, so a deep-ITM option on its expiry day (or with a
   missing vol) prices at **zero with zero delta** instead of intrinsic/±1. `price_position`
   inherits this: a verified example marks a 20-big-figure ITM call at $0 on expiry morning.
2. **`interpolate_iv` expects a surface schema that no longer exists.** The live published
   surface is nested (`{tenor: {atm: {iv, strike}}}`); `interpolate_iv` looks for legacy flat
   keys (`sigma_ATM_pct`, `strike_25dp`, …), always returns `None`, and the position monitor's
   live re-pricing path is therefore silently dead (every leg counts as `n_surface_missing`,
   which disqualifies the live mark). This is a sibling of the exact bug family fixed in
   f301a560 / 0f4be379 (consumer assuming a stale surface schema).

No Blocker was found: both High items degrade to conservative fallbacks today (linearised
attribution path; preview-only pricing), so money loss requires an additional condition
(e.g. holding to expiry week while relying on the mark, or fixing QM-02 without QM-01).

| Severity | Count |
|---|---|
| Blocker | 0 |
| High | 2 |
| Medium | 8 |
| Low | 5 |

## Findings table

| ID | Severity | Location | One-line description |
|---|---|---|---|
| QM-01 | High | `src/core/pricing/bs.py:13-57`, `src/core/positions/position_pricing.py:86-89` | `T<=0` or `sigma<=0` returns price/greeks = 0, not intrinsic/limit — ITM positions mark $0 with zero delta on expiry day |
| QM-02 | High | `src/core/pricing/bs.py:116-144`, `src/api/orchestration/position_monitor.py:608-618` | `interpolate_iv` expects legacy flat pillar keys; live surface is nested → live re-pricing silently dead; also nearest-neighbour, not interpolation |
| QM-03 | Medium | `src/core/vol/svi.py:105-110` | Butterfly check only on k∈[−0.20, 0.20]; ~29% of random in-bounds SVI params are clean inside but violate outside |
| QM-04 | Medium | `src/core/vol/svi.py:78-79` | 5-parameter SVI fit accepted with as few as 3 observations (underdetermined) |
| QM-05 | Medium | `src/core/vol/ssvi.py:83-86,105-118` | Float-keyed ATM-θ lookup with silent per-obs-IV fallback (divergent fit demonstrated); "calendar_arb_free" condition non-standard; no butterfly conditions checked |
| QM-06 | Medium | `src/core/vol/yang_zhang.py:22-38` | NaN/±Inf leak on bad OHLC rows (verified `nan` and `inf` returns); no input validation; k-constant uses n instead of n−1 |
| QM-07 | Medium | `src/core/vol/har_rv.py:144-152,51-61` | Projection seeds from the *unfiltered* RV series → NaN output when a bad close is in the last 22 rows (verified); |r|-proxy and log-space biases documented but feed the last-resort fair source |
| QM-08 | Medium | `src/core/vol/garch.py:44-61` | No input sanitisation; `convergence_flag` never checked (arch returns params even at flag=9); projects instantaneous variance at T, not average variance over [0,T] |
| QM-09 | Medium | `src/core/pricing/bs.py` (module), `src/core/trade_preview.py:41-49` | Undiscounted premium (zero-rate approximation) — biases model premium/IV vs premium-style-margined CME FOPs; two parallel BS implementations disagree at T≤0 |
| QM-10 | Medium | `src/core/vol/gmm_regime.py:67-79` | `reg_covar=0.5` on unstandardized features swamps the vol_of_vol/term_slope covariances — components can only separate on vol_level |
| QM-11 | Low | `src/core/vol/fair_term.py:64-72` vs `src/core/vol/regime_engine.py:88-140` | Two different "regime" definitions: fair-vol VRP uses (RV_full, IV slope), gating uses (IV3M, vol-of-vol) — labels can diverge |
| QM-12 | Low | `src/core/vol/vrp.py:33-59`, `src/core/vol/fair_term.py:74` | VRP table lacks 9M/1Y; `build_fair_q` iterates those pillars → hardcoded 0.8 + WARNING every cycle |
| QM-13 | Low | `src/core/vol/tenors.py:45-52,104-112` | Anchors placed at nominal label DTE, not actual listed DTE; calendar-arb inputs (w_hi < w_lo) silently clamped |
| QM-14 | Low | `src/core/risk/greeks.py:28-36` | `bs_price_vec` lacks the scalar version's `F<=0` guard → NaN elements (verified; unreachable from the ±2% spot ladder) |
| QM-15 | Low | `src/core/vol/svi.py:23,140-146`, `src/core/vol/pchip_smile.py:7-9`, `src/core/vol/ssvi.py:121-139` | Docstring typo (10P listed twice); PCHIP "does not extrapolate" claim is scipy-default-inaccurate (behavior is guarded); sampled curves span only k∈±0.08 (misses 10Δ wings at longer tenors) |

---

## QM-01 (High) — degenerate-input convention returns 0 instead of intrinsic

**Files:** `src/core/pricing/bs.py:13-57` (all of `bs_price`/`bs_delta`/`bs_gamma`/`bs_vega`/`bs_theta`/`bs_vanna`/`bs_volga`), consumed by `src/core/positions/position_pricing.py:86-89` (`_years_to_expiry` floors T at whole days → 0 on expiry day) and `src/engines/risk/engine.py`, `src/api/routers/portfolio_panel.py`.

Every function short-circuits with `return 0.0` when `sigma <= 0 or T <= 0 or F <= 0 or K <= 0`.
The correct limits are: price → intrinsic `max(±(F−K), 0)`, delta → 0/±1 by moneyness. The
sibling implementation `core/trade_preview.py:41-49` gets the price limit right (returns
intrinsic), so the two BS implementations in the codebase disagree on the same input.

`position_pricing._years_to_expiry` computes `days = max(0, (expiry − now.date()).days)`,
so on the expiry date itself T = 0 **for the whole trading day** while the contract is
still live and possibly deep ITM.

**Evidence (verification run):**

```
bs_price(F=1.20, K=1.00, T=0,    sig=0.08, 'C') = 0.0   (intrinsic = 0.20)
bs_price(F=1.20, K=1.00, T=0.25, sig=0,    'C') = 0.0   (lower bound = 0.20)
bs_delta(F=1.20, K=1.00, T=0,    sig=0.08, 'C') = 0.0   (true limit = 1.0)
price_position ITM call ON expiry day: mark_usd=0.0, delta=0.0
  (expected ≈ 0.20 × 125 000 = $25 000, delta ≈ +1)
```

**Impact:** an ITM position's mark collapses to $0 and its delta to 0 on expiry day — the
delta hedger would see no exposure on a position that behaves like a full future, and any
mark-driven exit rule sees a fake 100% loss (long) or windfall (short). Today the blast
radius is limited because the live re-pricing path is dead (QM-02), but fixing QM-02 alone
would arm this bug.

**Recommended fix:** in `bs_price` return intrinsic (`max(F−K, 0)` / `max(K−F, 0)`) when
`T<=0` or `sigma<=0` with F, K valid; in `bs_delta` return the indicator limit
(C: `1.0 if F>K else 0.5 if F==K else 0.0`, P: that minus 1); keep second-order greeks at 0.
Mirror the same convention in `bs_price_vec`. Add a property test pinning
`bs_price(F,K,0,σ,·) == intrinsic` and `bs_price ≥ intrinsic` for all T.

## QM-02 (High) — `interpolate_iv` schema mismatch: live re-pricing silently dead

**Files:** `src/core/pricing/bs.py:116-144`; caller chain `src/core/positions/position_pricing.py:120` ← `src/api/orchestration/position_monitor.py:495,608-618`.

`interpolate_iv` reads legacy flat pillar keys (`sigma_ATM_pct`, `strike_atm`,
`iv_25dp_pct`, `strike_25dp`, …). The surface the position monitor actually passes comes
from Redis `latest_vol_surface:{symbol}` and is the engine's **nested** shape — the same
function file's own neighbours prove it: `position_monitor.py:615` reads
`surface["3M"]["atm"]["iv"]` (nested), and `engines/risk/engine.py:524-527` explicitly
reimplemented `_iv_for` "against the **new** surface shape `{tenor: {label: {iv, strike}}}`"
because the legacy shape is gone.

Consequences, in order:
1. `interpolate_iv` finds zero `(strike, iv)` pairs → returns `None` for every leg.
2. Every leg falls back to `preview_iv_pct` and increments `n_surface_missing`.
3. `position_monitor.py:208` requires `n_surface_missing == 0` to accept the live mark →
   the live re-pricing feature is **never used**; the monitor silently stays on the
   linearised entry-greeks attribution forever (stale IV, stale greeks).

This is precisely the failure family of the recent markets-closed fixes (f301a560,
0f4be379): a consumer still coded against a retired surface schema. The audit brief asked
to check for sibling bugs of those fixes — this is one.

Two secondary defects in the same function:
- The docstring says "interpolating" but the implementation is **nearest-strike lookup**
  (piecewise-constant smile). Verified: strikes 1.074→9.0% vs 1.076→8.0% (jump at the
  midpoint), and a far-OTM strike of 1.40 returns the 25dc pillar IV unchanged.
- Parameter `F` is accepted and never used.

**Recommended fix:** teach `interpolate_iv` the nested shape (or better: delete it and
route `position_pricing` through the same pillar-lookup used by `engines/risk/engine._iv_for`),
do genuine linear-in-strike (or in-delta) interpolation between the two bracketing pillars,
and surface `n_surface_missing > 0` as a monitored counter so a future schema drift is loud
instead of silent. Add a unit test that feeds the *real* published surface shape.

## QM-03 (Medium) — butterfly check grid too narrow for the wing fallback

**File:** `src/core/vol/svi.py:105-110` (`butterfly_g_min`, defaults `k ∈ [−0.20, 0.20]`).

The `g(k)` formula and its analytic derivatives are **correct** (verified against numerical
differentiation, max diff 9.1e-9 over the grid), but `g ≥ 0` is only certified on
±0.20 log-moneyness. The engine consumes the check as a gate for the SVI wing fallback
(`engines/vol/engine.py:146`), which then evaluates SVI at 10Δ pillars obtained by delta→k
inversion — for higher-vol/longer tenors 10Δ sits at |k| ≈ 0.1–0.2 and beyond, i.e. at or
past the certified edge, and `svi_curve` callers may sample further.

**Evidence:** a 40 000-draw random search **inside the `fit_svi` bounds**
(`a∈[0,0.01], b∈[0,1], |ρ|≤0.999, |m|≤1, σ_p∈[1e-3,0.5]`) found **11 643 parameter sets
(29%) with `g_min(±0.2) ≥ 0` but `g_min(±1.0) < −0.01`**, e.g.
`SviParams(a=0.00028, b=0.868, ρ=−0.871, m=−0.181, σ=0.0120)` → `g_min(±0.2)=+0.30`,
`g_min(±1.0)=−6.09`. (Note: the bound `b ≤ 1` does keep the Lee wing slope
`b(1+|ρ|) ≤ 2` — good — so the violations are local, near the kink at `m`.)

**Recommended fix:** widen the default check grid to at least the range the fallback can be
asked to evaluate (e.g. `k ∈ ±max(0.5, 4·σ_ATM√T)`), or have `_build_svi_fallback` re-check
`g` on the actual k it lands on before trusting the extrapolated pillar.

## QM-04 (Medium) — SVI fit accepted with 3 points for 5 parameters

**File:** `src/core/vol/svi.py:78-79` (`if len(K) < 3: return None`).

Three observations cannot identify five parameters; `least_squares` (trf) still reports
success and returns one of infinitely many interpolating surfaces. Verified: a 3-point fit
returns `SviParams(a≈1.5e-05, b=0.0487, ρ=−0.585, m=−0.0266, σ=0.0401)` — plausible-looking
numbers with no data support for the wings, which then feed `butterfly_g_min` and the wing
fallback as if calibrated. **Recommended fix:** require ≥ 5 points (the standard pillar
count) for the full 5-parameter fit, or reduce to a constrained 3-parameter sub-model
(fix m=0, ρ prior) below that.

## QM-05 (Medium) — SSVI θ lookup fragility + mislabeled no-arb condition

**File:** `src/core/vol/ssvi.py:83-86` and `:105-118`; caller `src/engines/vol/engine.py:333-335`.

`theta = atm_iv_by_tenor_years.get(T, ivs[i])**2 * T` keys a dict by **float tenor-years**
and silently substitutes the *observation's own IV* as the ATM vol on any miss. Today the
caller builds both the observations and the dict from the same `tenor_years[t]` floats, so
lookups match — but any drift (rounding for JSON, recomputing T from DTE) silently changes
the model rather than failing. **Evidence:** refitting the same observations with keys
rounded to 4 dp produced `eta=0.734` vs `eta=9.557` with exact keys — a completely different
surface with a *better* RMSE, i.e. undetectable from the fit metric.

Secondary issues:
- Line 106 labels `2γ ≥ 1 − ρ²` as the calendar-arbitrage condition. For the power-law
  `φ(θ)=ηθ^{−γ}`, Gatheral–Jacquier's calendar condition holds for any `γ ≤ 1` regardless
  of ρ; the real static-arbitrage constraints not checked here are the butterfly ones,
  `θφ(θ)(1+|ρ|) ≤ 4` and `θφ(θ)²(1+|ρ|) ≤ 4` (γ ≥ 1/2 needed asymptotically for large θ).
  The stored `calendar_arb_free` flag therefore certifies neither calendar nor butterfly
  freedom. (SSVI is a smoother/diagnostic here — documented — so Medium, not High.)
- Violations only log a warning; the params are persisted with the misleading flag.

**Recommended fix:** key the ATM map by tenor **label** (str), make a missing key an error;
rename the flag and check the two Gatheral–Jacquier butterfly inequalities on the fitted
(η, γ, ρ) over the observed θ range.

## QM-06 (Medium) — Yang-Zhang NaN/Inf leak on bad OHLC

**File:** `src/core/vol/yang_zhang.py:22-38`.

No validation of the OHLC window: `np.log` of NaN/0/negative propagates, and the final
`max(s2_yz, 0)` does not catch NaN (Python `max` returns the NaN operand). The function's
contract is `float | None`, but:

```
clean OHLC  -> 9.5548 (%)
one NaN high -> nan     (returned as float('nan'), not None)
one zero low -> inf
```

Downstream, `rv_pct`/`_rv_full_pct` feed `build_fair_q` (σ_fair anchor), `detect_regime`
(`vol_level_pct > 10` is False for NaN → silently "calm") and the GARCH empirical leg — a
single corrupt bar poisons the fair-vol chain without any error. Minor additional bias: the
constant `k = 0.34/(1.34 + (n+1)/(n−1))` uses the row count n, while the variances are
computed on n−1 return observations (standard YZ uses the number of returns).

**Recommended fix:** drop rows with non-finite or non-positive O/H/L/C (and `high < low`)
before the log, return `None` when < 3 valid rows remain or the result is non-finite; use
the return-count in the k constant. Property test: any single corrupt row → `None` or a
finite float, never NaN/Inf.

## QM-07 (Medium) — HAR projection seeds from unfiltered series; NaN leak

**File:** `src/core/vol/har_rv.py:144-152` (`fit_and_project_har`), `:51-61`.

`fit_har_rv` filters `closes` to finite/positive values (line 82), but the projection seed
is rebuilt from the **raw** array: `rv = _daily_rv_percent_from_closes(np.asarray(arr))`
(line 147). A NaN/zero close inside the last `MONTHLY_LAG=22` rows contaminates the seed
windows (`np.maximum(nan, 1e-4)` propagates NaN; `math.log(max(nan, …)) = nan`):

```
NaN close at index 150 (outside seed window) -> {'1M': {'sigma_har_pct': 4.7323}}   (silently differs from clean 4.7078)
NaN close at index -3                        -> {'1M': {'sigma_har_pct': nan}}
zero close at index -3                       -> {'1M': {'sigma_har_pct': nan}}
```

The NaN then rides into the surface `_har` bucket and — when RV is absent — into
`pick_sigma_fair_p` → `σ_fair^Q = nan + VRP = nan` (fair_term has no finite-check on the
estimator path, `fair_term.py:83-86`).

Known-and-documented biases (noting for completeness, no action forced): the daily RV proxy
`|r|·√252` underestimates σ by `√(2/π) ≈ 0.80` for Gaussian returns, and the log-space
projection re-exponentiates without a `+σ²_resid/2` correction — both acknowledged in
`fair_term.build_fair_q`'s docstring, which is why fair anchors to Yang-Zhang RV and HAR is
only a last-resort source. Also harmless-but-odd: `project_horizon:118`'s
`rv_series[-max(MONTHLY_LAG, len(rv_series)):]` always takes the whole series (`max` was
presumably meant to be `min`).

**Recommended fix:** reuse the filtered array for the projection seed (filter once in
`fit_and_project_har`, pass it to both), and have `fit_and_project_har` drop non-finite
projections; `fair_term.pick_sigma_fair_p` should also reject non-finite values.

## QM-08 (Medium) — GARCH: unsanitised input, unchecked convergence, point-vs-average variance

**File:** `src/core/vol/garch.py:44-61,66`.

1. **Input:** `np.diff(np.log(closes)) * 100` with no finite/positive filter (contrast
   `har_rv.py:82`). In practice arch raised on the NaN test case and the except-clause
   returned `{}` (the documented failure mode) — acceptable, but zero/negative closes
   produce ±Inf returns whose behaviour inside arch is undefined; filter for parity with HAR.
2. **Convergence:** `arch`'s `.fit()` returns a result object even when the optimizer fails;
   only exceptions are caught. Verified: `convergence_flag: 9` ("Iteration limit reached")
   with plausible-looking params returned. `fit.convergence_flag != 0` should be treated as
   fit failure (return `{}`).
3. **Horizon quantity:** `var_T = var_lr + (var_c − var_lr)·e^{−κT}` is the *instantaneous*
   variance forecast at horizon T, not the *average* expected variance over [0, T] — the
   quantity an option fair vol needs (the average adds a `(1 − e^{−κT})/(κT)` weighting).
   HAR uses the mean-over-horizon convention (har_rv.py:109-128), so the two estimators
   answer different questions while being displayed/blended as the same `sigma_model_pct`.
   Since fair vol anchors to RV (fair_term docstring) and GARCH is a diagnostic, Medium.
4. Annualisation math (`(√(σ²_daily·252)/100)²` with returns ×100) and the capped-persistence
   long-run variance were checked and are internally consistent (the κ→0 blow-up in
   `ω/(1−p)` cancels against `e^{−κT}` to first order — verified numerically: 1M 8.6604% vs
   6M 8.6610%, no explosion).

**Recommended fix:** filter closes like HAR does; check `convergence_flag == 0`; integrate
the variance path over [0, T] (closed form exists) so GARCH and HAR share the
"average σ over the option's life" convention.

## QM-09 (Medium) — undiscounted premium convention + duplicate BS implementations

**Files:** `src/core/pricing/bs.py` (module docstring: "zero rates approximation"),
`src/core/trade_preview.py:41-71`, `src/engines/execution/structures.py` (same Black-76 copy).

All pricing is undiscounted Black-76 on the forward/future. That is internally consistent,
and for **futures-style-margined** options it is exactly right. CME EUR/USD FOPs (EUU),
however, are premium-paid-upfront, so the traded premium ≈ `e^{−r_USD·T}` × Black-76 value:
at 4–5% USD rates the model overprices a 3M option by ~1.1–1.3% and a 6M by ~2.2–2.5% of
premium, and `bs_implied_vol` run against *market* premiums inherits the opposite bias.
Effects mostly cancel in richness/cheapness comparisons (both legs share the convention) but
not at the boundary where model premium meets IB fill prices (`preview_price` vs
`avg_fill_price`). Also: three near-identical BS implementations exist
(`core/pricing/bs.py`, `core/trade_preview.py`, `engines/execution/structures.py`) with
diverging degenerate-input conventions (see QM-01) — a drift hazard by itself.

**Recommended fix:** either document the accepted bias with a magnitude bound in
`core/units.py` (where the price-points ↔ USD boundary is specified), or thread a single
`df = e^{−rT}` through premium (greeks may stay forward-measure). Consolidate to one BS
implementation with one edge-case convention.

## QM-10 (Medium) — GMM regularizer swamps two of three features

**File:** `src/core/vol/gmm_regime.py:67-79` (`reg_covar=0.5`).

Features are fed unstandardized: `vol_level` ~5–15 (%), `vol_of_vol` ~0–2 (pp),
`term_slope` ~±2 (pp). `reg_covar=0.5` adds 0.5 to every covariance diagonal — comparable
to or larger than the *total* variance of vol_of_vol and term_slope, so those dimensions'
fitted covariances are dominated by the regularizer and the components can effectively
only separate along vol_level. The mapping `_map_components_to_labels` sorts by mean
vol_level anyway, but the *probabilities* (`p_calm/p_stressed/p_pre_event`) are persisted
to `regime_snapshots` for the J+30 backtest comparison — that comparison will evaluate a
nearly-1-D model and may wrongly conclude GMM ≈ threshold heuristic. The comment
acknowledges the collapse motivation but not this side effect. Shadow-only today
(labels stay threshold-driven), hence Medium.

**Recommended fix:** standardize X (per-feature z) before the fit — then a small
`reg_covar` (1e-4…1e-2) suffices — and store the scaler with the serialized model
(`serialize_gmm`) so `infer_proba` projects in the same space.

## QM-11 (Low) — two inconsistent regime definitions

`fair_term.build_fair_q` (fair_term.py:64-72) classifies regime from
`(rv_full_pct, ATM 6M−1M slope)` with vol_of_vol=None, while the gating snapshot
(`regime_engine.compute_regime_snapshot:88-140`) classifies from
`(IV_3M, 30d std of IV_3M)`. Same `detect_regime` function, different features → the VRP
addend used for σ_fair^Q can be computed under a different label than the regime shown/gated
on. Recommend feeding both from one feature set (or persisting which features produced the
fair-vol regime).

## QM-12 (Low) — VRP table missing 9M/1Y pillars

`VRP_DEFAULTS_VOL_PTS` covers 1M…6M only (vrp.py:33-37) but `build_fair_q` iterates every
surface tenor including the 9M/1Y anchors (fair_term.py:74; `DEFAULT_TENOR_T` includes
them). Result: `predict_vrp` WARNING + hardcoded 0.8 every cycle for those tenors —
log noise and an uncalibrated VRP for the long end. Either add 9M/1Y rows or skip
non-display tenors in `build_fair_q`.

## QM-13 (Low) — tenor anchors at nominal DTE; inverted-term inputs clamped silently

`to_display_surface`/`interpolate_pillar` place anchors at `LABEL_DTE` nominal days
(tenors.py:104-107) rather than each listed expiry's actual DTE (which the engine knows) —
interpolation weights are off by up to the bucketing error (±15d). `_interp_iv`
(tenors.py:45-52) linearly interpolates total variance and clamps `max(w, 0)` — verified
that a decreasing-total-variance input (calendar arbitrage in the anchors, e.g. IV 20%@30d
vs 2%@90d) interpolates silently instead of flagging. The flat-hold extrapolation past the
last anchor (≤45d, marked "interp") is variance-increasing and safe. Total-variance
monotonicity for normal inputs verified (w30 ≤ w60 ≤ w90). Recommend passing actual DTEs
for anchors and logging when `w_hi < w_lo`.

## QM-14 (Low) — `bs_price_vec` missing F guard

`src/core/risk/greeks.py:28-36` guards `sigma/T/K ≤ 0` but not the forward array:
`bs_price_vec([1.1, 0.0, −0.5], …) → [0.0176, 0.0, nan]` with RuntimeWarnings. The PnL-curve
caller builds F from a ±2% spot ladder so F>0 in practice; still, mirror the scalar guard
(`np.where(F_arr > 0, …, 0.0)` — or intrinsic per QM-01's convention).

## QM-15 (Low) — documentation/sampling nits

- `svi.py:23`: pillar list reads "10P/25P/ATM/25C/**10P**" — last should be 10C.
- `pchip_smile.py:7-9`: "PCHIP itself does not extrapolate: … returns None from the native
  spline" — scipy's `PchipInterpolator` extrapolates by default; the code is safe only
  because `_at` range-guards before calling. Docstring should credit the guard, not scipy.
- `svi_curve` / `ssvi_curve_for_tenor` default `k ∈ ±0.08`: at 6M with σ≈8%, 10Δ sits at
  |k| ≈ 0.09–0.12, so the rendered smile curves stop short of the 10Δ pillars they are
  plotted against. Display-only.

---

## Verified clean (evidence of absence)

- **Greeks vs finite differences — PASS.** Central FD of `bs_price` at
  (F=1.10, K=1.12, T=0.25, σ=0.08), both rights: delta diff ≤ 1.2e-9, gamma ≤ 4.0e-6,
  vega ≤ 2.9e-10, theta ≤ 2.0e-13, vanna ≤ 2.5e-8, volga ≤ 1.4e-8. Grid sweep over
  96 (F, K, T, σ, right) combos: max|Δ−fd| = 2.8e-9, max|Γ−fd| = 4.5e-5 (FD truncation at
  T=0.02, σ=0.03), max|V−fd| = 8.8e-9. Undiscounted put–call parity `C−P−(F−K)` = −5.6e-17.
- **`bs_implied_vol`**: round-trips (0.08 → 0.07999998); returns `None` below intrinsic and
  outside the [lo, hi] price envelope, as documented.
- **SVI butterfly formula**: analytic `g(k)` (w′, w″ closed forms) matches numerical
  differentiation to 9.1e-9 on ±0.2; the fitted 5-pillar EURUSD-like smile reproduces
  observations to < 5e-4 IV and is butterfly-clean on ±1.0.
- **Delta conventions consistent end-to-end**: IB put deltas negative →
  `chain_fetcher.py:324` maps `call_delta = 1 + put_delta`; pillar targets
  (`pchip_smile.DELTA_LABELS`: atm 0.50, 25dp 0.75, 10dp 0.90) match; SVI fallback inversion
  `k = σ²T/2 − σ√T·Φ⁻¹(δ)` (engine.py:170) is the correct forward-delta relation.
- **Tenor/day-count units**: T = calendar days / 365 everywhere on the option side
  (`position_pricing`, `trade_preview`, `DEFAULT_TENOR_T` = months/12); RV side uses
  trading days with √252 annualisation and HAR converts calendar→trading days via 5/7
  (har_rv.py:150) — conventions differ by construction but are matched at the boundary
  (horizon-matched RV per pillar) rather than mixed.
- **PCA**: deterministic (numpy SVD, no randomness); standardization means/stds are frozen
  in the persisted model and the live observation is projected against them — the z-score
  history query (`engine.py:1356-1362`) excludes the current cycle → no look-ahead found.
  `sign_correct_loadings` cosine-flip is sound; `pc3_sub_metrics` indexing matches the
  declared (tenor-outer, delta-inner) layout.
- **GMM determinism**: `random_state=42`, label mapping sorted by mean vol_level each fit
  (stable across refits); `serialize_gmm`/`deserialize_gmm` restore `precisions_cholesky_`
  verbatim → bit-identical `predict_proba`.
- **feature_enrichment**: `bucket`/`pct`/`signal` operate on caller-supplied *history*
  windows (90d past series per `stamp_enrichment`) — no in-sample standardization; the
  normal-fallback and quantile cutoffs match the documented spec.
- **`core.units` USD convention**: applied identically in `trade_preview._leg_greeks`
  (vega×VOLPT, gamma×PIP², theta/365 for the per-year variant) and
  `position_pricing.price_position` (theta already per-day, scaled by notional only —
  no double /365). Cross-checked against `core/units.py` docstring.
- **Markets-closed serving fixes (f301a560, 0f4be379)**: the API-side grid-preference query
  and `_fair_q` persistence look correct; `to_display_surface` is idempotent (interp cells
  never downgraded to "listed"). The one sibling bug found in that family is QM-02.

## Appendix — units & conventions table (module boundary → quantity → unit)

| Boundary (producer → consumer) | Quantity | Unit / convention |
|---|---|---|
| IB chain → `chain_fetcher` triples | delta | call-delta convention in [0,1]; puts mapped `1 + δ_put` (IB put δ < 0) |
| `chain_fetcher` → `pchip_smile` / `fit_svi` | iv | decimal fraction (0.08 = 8%) |
| `pchip_smile.DELTA_LABELS` | pillar targets | atm 0.50, 25dc 0.25, 25dp 0.75, 10dc 0.10, 10dp 0.90 (call-delta space) |
| surface grid cell `{tenor:{delta:{iv,strike}}}` | iv | **decimal fraction**; strike in price units |
| surface meta `_rv_full_pct`, `_har.sigma_har_pct`, `_garch.sigma_model_pct`, `_fair_q.*_pct`, `rv_pct` | vol | **percent** (8.0 = 8%) — ⚠ grid cells are fractions, meta are percent, on the same payload; converters: `regime_engine._atm_pct` (×100), `pca_engine.feature_vector_from_surface` (×100) |
| `fit_svi` / `svi_iv` | k | log-moneyness ln(K/F); w = σ²T total variance, T in calendar years (months/12) |
| `svi_curve` / `ssvi_curve_for_tenor` output | iv_pct | percent (×100 at the boundary) |
| `fit_ssvi` | θ | ATM total variance σ²_ATM·T (decimal² · years), keyed by float tenor-years (see QM-05) |
| `tenors` (`TenorAnchor.dte`, `_interp_iv`) | time | integer **calendar** days; interpolation linear in w = iv²·dte |
| `yang_zhang_rv_pct` | RV | annualised percent, √252 (trading-day) annualisation |
| `har_rv` | RV proxy | \|log-return\|·√252·100 (percent); horizons converted calendar→trading days ×5/7 |
| `garch` | σ_model | annualised percent, √252, from ×100-scaled log-returns |
| `vrp` | VRP | additive **vol points** (0.6 = +0.6 IV pct-pts) on σ_P percent |
| `core.pricing.bs` inputs | F, K | price units (fwd/future); T = calendar days/365 years; σ decimal |
| `core.pricing.bs` outputs | price | price points (premium per unit notional), undiscounted; theta **per day**; vega per 1.00 vol |
| `core.trade_preview.bs_greeks` | theta | **per year** (divided by 365 at aggregation — `_leg_greeks`) |
| `core.units` boundary | vega_usd | bs_vega × 0.01 (VOLPT) × ±qty × 125 000 → $/vol-pt |
| | gamma_usd | bs_gamma × (1e-4)² (PIP²) × ±qty × 125 000 → $/pip²; PnL = ½γ(ΔS_pips)² |
| | theta_usd | per-day theta × ±qty × 125 000 → $/day |
| | delta_usd | bs_delta × spot × ±qty × 125 000 ($ notional); contract-equivalent delta = bs_delta × qty_signed (unscaled — hedger input) |
| IB order prices | lmtPrice | raw price points; USD = points × 125 000 × qty |
| `pca_engine` | features | 30-dim IV vector in percent; z-scores dimensionless vs frozen model μ/σ and vs 90d raw-score history |
| `regime_engine` / `feature_enrichment` | vol_level, vov, term_slope | percent points (IV3M; 30d std of IV3M; IV6M−IV1M); z dimensionless; delta_z_1h in z-points/hour |
