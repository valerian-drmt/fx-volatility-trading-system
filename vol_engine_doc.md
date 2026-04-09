# Volatility Engine — Mathematical Documentation

**Pipeline: σ\_mid → σ\_fair → Signal → Trade Decision**

EUR/USD CME Futures Options (FOP) · Interactive Brokers API · Buy-side systematic vol trading

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Step 1 — σ\_mid: Implied Volatility Surface Extraction](#2-step-1--σ_mid-implied-volatility-surface-extraction)
3. [Step 2 — σ\_fair: Fair Volatility Model](#3-step-2--σ_fair-fair-volatility-model)
4. [Signal Generation](#4-signal-generation)
5. [Trade Decision Framework — Greeks & P\&L Decomposition](#5-trade-decision-framework--greeks--pl-decomposition)
6. [Sensitivity & Assumptions](#6-sensitivity--assumptions)
7. [Appendix A — Day-Weight Framework: Why Not Used, When Needed](#7-appendix-a--day-weight-framework-why-not-used-when-needed)
8. [Appendix B — W₁/W₂ Sensitivity Analysis](#8-appendix-b--w₁w₂-sensitivity-analysis)

---

## 1. System Overview

### Objective Function

Identify mispricings in the implied volatility term structure of EUR/USD CME futures options, where:

$$
\text{signal}(T) = \sigma_{\text{fair}}(T) - \sigma_{\text{mid}}(T)
$$

- **signal > +threshold** → market IV is cheap relative to model → **buy vol** (buy options)
- **signal < −threshold** → market IV is expensive relative to model → **sell vol** (sell options)
- **|signal| ≤ threshold** → fairly priced → no trade

### Pipeline

```
IB Gateway
    │
    ▼
Step 1: vol_mid_step1.py
    │  FOP chain scan → BS IV extraction → delta-space interpolation
    │  Output: σ_ATM, RR25, BF25, smile pillars per tenor
    │
    ▼
Step 2: vol_fair_step2.py
    │  Layer A: Yang-Zhang Realized Vol
    │  Layer B: GARCH(1,1) forward vol
    │  Layer C: Portfolio book adjustment (δ_book)
    │  Combination: σ_fair = W₁·(RV + RP) + W₂·σ_GARCH + δ_book
    │
    ▼
Signal: CHEAP / EXPENSIVE / FAIR per tenor
    │
    ▼
Trade Decision: buy/sell vol with known Greeks exposure
```

---

## 2. Step 1 — σ\_mid: Implied Volatility Surface Extraction

> **Design Choices**
>
> | Choice | Selected | Alternatives Considered |
> |--------|----------|------------------------|
> | Interpolation method | PCHIP (monotone cubic Hermite) | Natural cubic spline, linear, SVI parametric |
> | Interpolation domain | Delta-space (Δ → σ) | Strike-space (K → σ), log-moneyness (ln K/F → σ) |
> | Strike window | ±6 short / ±10 long | Fixed ±8 all tenors, adaptive by liquidity |
> | IV source | IB model greeks (tick 100) | Own BS inversion on mid price, Vanna-Volga |
> | Validation thresholds | RR₂₅\_max, BF₂₅\_min per tenor bucket | Global thresholds, no filtering |

### Purpose

Extract the market's current implied volatility surface across 6 tenors (1M–6M) from live IB option chain data. This is the **observed variable** — what the market prices.

### Pipeline

#### 2.1 — Forward Price F

Fetch the front CME EUR future (6E), compute mid:

$$
F = \frac{\text{bid}_F + \text{ask}_F}{2}
$$

F is used as the underlying reference for moneyness computation across all tenors. Using the future (not spot) avoids carry/forward-point adjustments.

#### 2.2 — Option Chain Selection

For each target DTE ∈ {30, 60, 90, 120, 150, 180}:

1. Query all EUU (EUR CME FOP) expirations via `reqSecDefOptParams`
2. Select the expiration closest to target DTE
3. Filter strikes in a window around ATM:
   - Short tenors (DTE ≤ 45): ±6 strikes around ATM
   - Long tenors (DTE > 45): ±10 strikes around ATM

**Rationale for asymmetric windows:** Short-dated options have steeper smiles and less liquid wings — scanning fewer strikes reduces noise. Long-dated options have flatter smiles and more liquid wings — wider scan captures the curvature.

#### 2.3 — IV Extraction per Strike

For each qualified strike K, request IB model greeks (tick type 100):

$$
\sigma_{\text{impl}}(K) = \text{BS}^{-1}\big(C_{\text{market}}(K), F, K, T, r\big)
$$

IB returns the IV directly from its own BS inversion engine. Each strike also yields model delta Δ(K).

**Filtering:** Strikes with no IV or IV = 0 are discarded. A minimum number of valid strikes is required (5 for short tenors, 7 for long tenors).

#### 2.4 — Delta-Space Interpolation

Raw IV-by-strike data is noisy and irregularly spaced. Convert to delta-space for canonical pillar extraction:

1. Sort (Δ, σ, K) triples by delta
2. Fit PCHIP monotonic interpolator on Δ → σ and Δ → K
3. Extract standard FX vol pillars:

| Pillar | Delta |
|--------|-------|
| 10Δ Put | −0.10 |
| 25Δ Put | −0.25 |
| ATM | +0.50 |
| 25Δ Call | +0.25 |
| 10Δ Call | +0.10 |

**Why PCHIP over cubic spline — mathematical comparison:**

Both methods interpolate through the same data points {(Δ\_i, σ\_i)} for i = 1, ..., n. The difference is in how they compute the derivative (slope) at each knot.

**Natural Cubic Spline:**

On each interval [Δ\_i, Δ\_{i+1}], fit a cubic polynomial S\_i(Δ) such that S, S', and S'' are continuous at every interior knot. The second derivatives {m\_i} are determined by solving a global tridiagonal system:

$$
h_{i-1} m_{i-1} + 2(h_{i-1} + h_i) m_i + h_i m_{i+1} = 6\left(\frac{\sigma_{i+1} - \sigma_i}{h_i} - \frac{\sigma_i - \sigma_{i-1}}{h_{i-1}}\right)
$$

where h\_i = Δ\_{i+1} − Δ\_i, with boundary conditions m\_0 = m\_n = 0 (natural). The interpolant on [Δ\_i, Δ\_{i+1}] is:

$$
S_i(\Delta) = \frac{m_i}{6h_i}(\Delta_{i+1}-\Delta)^3 + \frac{m_{i+1}}{6h_i}(\Delta-\Delta_i)^3 + \left(\frac{\sigma_i}{h_i} - \frac{m_i h_i}{6}\right)(\Delta_{i+1}-\Delta) + \left(\frac{\sigma_{i+1}}{h_i} - \frac{m_{i+1} h_i}{6}\right)(\Delta-\Delta_i)
$$

**Problem:** The C² continuity constraint is *global* — perturbing one data point at the wing changes the interpolant everywhere, including at ATM. With sparse/noisy wing data (common on short-dated FOP chains), this produces oscillations that can make the interpolated smile non-convex, generating **negative butterfly spreads** (BF₂₅ < 0 when the raw data implies BF₂₅ ≥ 0). This is the Runge phenomenon applied to smile interpolation.

**PCHIP (Piecewise Cubic Hermite Interpolating Polynomial):**

On each interval [Δ\_i, Δ\_{i+1}], fit a cubic Hermite basis using function values *and* first derivatives at the endpoints:

$$
P_i(\Delta) = \sigma_i \cdot h_{00}(t) + d_i \cdot h_i \cdot h_{10}(t) + \sigma_{i+1} \cdot h_{01}(t) + d_{i+1} \cdot h_i \cdot h_{11}(t)
$$

where t = (Δ − Δ\_i) / h\_i and the Hermite basis functions are:

$$
h_{00}(t) = 2t^3 - 3t^2 + 1, \quad h_{10}(t) = t^3 - 2t^2 + t
$$

$$
h_{01}(t) = -2t^3 + 3t^2, \quad h_{11}(t) = t^3 - t^2
$$

The slopes d\_i at each knot are computed *locally* using the Fritsch-Carlson algorithm:

1. Compute secants: δ\_i = (σ\_{i+1} − σ\_i) / (Δ\_{i+1} − Δ\_i)
2. If δ\_{i-1} and δ\_i have opposite signs or either is zero: d\_i = 0 (enforces local extremum)
3. Otherwise: d\_i is the harmonic mean of adjacent secants, bounded to preserve monotonicity:

$$
d_i = \frac{3(\alpha_i + \beta_i)}{\frac{\alpha_i}{\delta_{i-1}} + \frac{\beta_i}{\delta_i} + \frac{1}{\delta_{i-1}\delta_i}(\alpha_i \delta_i + \beta_i \delta_{i-1})}
$$

where α\_i = (1 + h\_i / (h\_{i-1} + h\_i)) / 3 and β\_i = 1 − α\_i.

**Key property:** The slopes are determined *locally* — each d\_i depends only on the neighboring data points. This makes PCHIP immune to oscillations from distant noisy points, and it guarantees monotonicity between consecutive knots when the data is monotone. The tradeoff is C¹ continuity only (S'' may be discontinuous at knots), but this is irrelevant for our use case — we only need smooth σ(Δ) values at 5 fixed pillars, not smooth curvature.

#### 2.5 — FX Vol Conventions

From the interpolated pillars, derive standard FX vol metrics:

**Risk Reversal (skew measure):**

$$
\text{RR}_{25} = \sigma_{25\Delta C} - \sigma_{25\Delta P}
$$

Positive RR₂₅ → calls trade at higher IV than puts → market skew favors upside.

**Butterfly (convexity/smile measure):**

$$
\text{BF}_{25} = \frac{\sigma_{25\Delta C} + \sigma_{25\Delta P}}{2} - \sigma_{\text{ATM}}
$$

Positive BF₂₅ → wings trade above ATM → convex smile. Negative BF₂₅ is typical for FX (wings below ATM on liquid pairs).

#### 2.6 — Validation Gates

Each tenor passes adaptive filters before inclusion:

| Parameter | Short (DTE ≤ 45) | Long (DTE > 45) |
|-----------|-------------------|------------------|
| \|RR₂₅\| max | 10.0% | 6.0% |
| BF₂₅ min | −6.0% | −4.0% |
| Min strikes | 5 | 7 |

Tenors failing validation are dropped — no imputation. Better to have 4 clean tenors than 6 noisy ones.

#### 2.7 — Output

Per tenor: `{tenor_label, expiry, dte, F, σ_ATM, RR25, BF25, iv_10dp, iv_25dp, iv_25dc, iv_10dc, strike_pillars}` → `vol_mid_output.csv`

---

## 3. Step 2 — σ\_fair: Fair Volatility Model

> **Design Choices**
>
> | Choice | Selected | Alternatives Considered |
> |--------|----------|------------------------|
> | Combination weights | W₁ = 0.65 (RV), W₂ = 0.35 (GARCH) | Equal weights (0.50/0.50), Bayesian model averaging, regime-switching weights |
> | RV estimator | Yang-Zhang (OHLC) | Close-to-close, Parkinson (HL), Garman-Klass (OHLC), Rogers-Satchell |
> | Forward vol model | GARCH(1,1) Normal | EGARCH (leverage), GJR-GARCH (asymmetric), GARCH-t (fat tails), HAR-RV, SV (stochastic vol) |
> | GARCH innovation dist. | Normal | Student-t, Skewed-t, GED |
> | Risk premium | Constant per tenor (calibrated) | Time-varying RP, regime-conditional RP, VRP regression |
> | Rolling window | max(21, ⌊T×252⌋) days | Fixed 63d all tenors, exponentially weighted |
> | Book adjustment | Linear ratio × α\_book | Quadratic penalty, Kelly-based sizing, no adjustment |

### Purpose

Construct an independent estimate of what volatility *should* be, using realized data, a parametric model, and portfolio state. This is the **model variable** — what we believe vol is worth.

### Architecture

$$
\sigma_{\text{fair}}(T) = W_1 \cdot \underbrace{\big(\text{RV}(T) + \text{RP}(T)\big)}_{\text{Layer A: anchored realized}} + W_2 \cdot \underbrace{\sigma_{\text{GARCH}}(T)}_{\text{Layer B: model forward}} + \underbrace{\delta_{\text{book}}(T)}_{\text{Layer C: portfolio bias}}
$$

Default weights: W₁ = 0.65, W₂ = 0.35. Constraint: W₁ + W₂ = 1.

**Rationale for the weighted combination:** Pure realized vol is backward-looking and misses regime shifts. Pure GARCH is model-dependent and can overfit recent moves. The blend diversifies estimation error across two independent information sources. W₁ > W₂ because realized vol is more robust on liquid FX pairs where vol is mean-reverting and jumps are rare. The 65/35 split reflects a prior that on EURUSD, the empirical anchor (what *did* happen) is a stronger predictor than the parametric extrapolation (what the model *thinks* will happen). These weights are fixed — a natural extension would be regime-conditional weights where W₂ increases during high-persistence regimes (α + β > 0.99) when GARCH's forward projection carries more information.

---

### Layer A — Yang-Zhang Realized Volatility

> **Design Choices**
>
> | Choice | Selected | Why Not Alternatives |
> |--------|----------|---------------------|
> | Estimator | Yang-Zhang | Close-to-close: ~7× higher variance. Parkinson: assumes zero drift. Garman-Klass: sensitive to overnight jumps. Rogers-Satchell: no overnight component. Yang-Zhang combines all three information sources (overnight, drift, range) optimally. |
> | Window per tenor | max(21, ⌊T×252⌋) | Fixed window ignores tenor structure. Exponential weighting adds a decay parameter to calibrate. Rolling rectangular window is simplest and robust. |
> | Annualization | ×252 (trading days) | ×365 overcounts non-trading days where vol = 0 |

#### Why Yang-Zhang over Close-to-Close

Close-to-close vol:

$$
\hat{\sigma}_{\text{CC}} = \sqrt{\frac{252}{n-1} \sum_{i=1}^{n} (r_i - \bar{r})^2}, \quad r_i = \ln\frac{C_i}{C_{i-1}}
$$

This estimator is **inefficient** — it uses only 1 data point per day (close) and ignores intraday range. Yang-Zhang (2000) uses the full OHLC vector, achieving ~7× lower variance for the same sample size.

#### Yang-Zhang Estimator

Decompose daily variance into three components:

**Overnight variance:**

$$
\sigma^2_{\text{overnight}} = \text{Var}\big(\ln O_i - \ln C_{i-1}\big)
$$

**Open-to-close variance:**

$$
\sigma^2_{\text{OC}} = \text{Var}\big(\ln C_i - \ln O_i\big)
$$

**Rogers-Satchell variance (range-based, drift-independent):**

$$
\sigma^2_{\text{RS}} = \frac{1}{n}\sum_{i=1}^{n} \Big[(\ln H_i - \ln C_i)(\ln H_i - \ln O_i) + (\ln L_i - \ln C_i)(\ln L_i - \ln O_i)\Big]
$$

**Combined Yang-Zhang:**

$$
\sigma^2_{\text{YZ}} = \sigma^2_{\text{overnight}} + k \cdot \sigma^2_{\text{OC}} + (1-k) \cdot \sigma^2_{\text{RS}}
$$

where:

$$
k = \frac{0.34}{1.34 + \frac{n+1}{n-1}}
$$

**Annualized realized vol:**

$$
\text{RV}(T) = \sqrt{\sigma^2_{\text{YZ}} \times 252} \times 100 \quad (\text{in \%})
$$

**Rolling window:** max(21, ⌊T × 252⌋) days, capped at available data. Short tenors use 21 days minimum to avoid single-week noise.

#### Risk Premium

A constant additive premium RP(T) is applied per tenor:

| Tenor | RP (%) |
|-------|--------|
| 1M | 1.20 |
| 2M | 1.35 |
| 3M | 1.50 |
| 4M | 1.55 |
| 5M | 1.58 |
| 6M | 1.60 |

**Rationale:** Implied vol systematically exceeds realized vol on average (the Variance Risk Premium). Buyers of options pay a premium for gamma/convexity insurance. The premium increases with tenor because longer-dated options carry more uncertainty about future realized paths. These values are calibrated from historical EURUSD IV-RV spreads.

**Anchored estimate:**

$$
\text{Anchor}(T) = \text{RV}(T) + \text{RP}(T)
$$

---

### Layer B — GARCH(1,1) Forward Volatility

> **Design Choices**
>
> | Choice | Selected | Why Not Alternatives |
> |--------|----------|---------------------|
> | Model | GARCH(1,1) | Simplest vol clustering model with only 3 parameters (ω, α, β). EGARCH adds leverage effect — irrelevant for EURUSD which has near-symmetric vol response to up/down moves (unlike equity indices). GJR-GARCH: same argument. GARCH(2,1) or (1,2): marginal fit improvement, overfitting risk on 252 obs. HAR-RV: requires intraday data not available from IB OHLC. Stochastic vol (Heston): requires option price calibration, circular dependency with Step 1. |
> | Innovation distribution | Normal | Student-t captures fat tails but adds 1 parameter (df) — with 252 daily obs, MLE struggles to separate α from df. Normal is adequate for forward vol projection (we use the conditional variance path, not the tail quantiles). |
> | Estimation data | 1 year daily (252 obs) | Shorter: insufficient for stable MLE. Longer: includes stale regimes that dilute the current vol structure. 1Y is the standard for FX GARCH calibration. |
> | Mean model | Constant (μ) | Zero mean, AR(1). On daily FX returns, μ ≈ 0 and AR(1) coefficient is statistically insignificant. Constant mean avoids zero-mean bias without adding complexity. |

#### Model Specification

Daily log returns (× 100 for numerical stability):

$$
r_t = \mu + \varepsilon_t, \quad \varepsilon_t = \sigma_t z_t, \quad z_t \sim \mathcal{N}(0,1)
$$

Conditional variance dynamics:

$$
\sigma^2_t = \omega + \alpha \varepsilon^2_{t-1} + \beta \sigma^2_{t-1}
$$

**Parameter interpretation:**

- **ω (omega):** Baseline variance floor. Determines the unconditional (long-run) variance level. ω > 0 ensures the variance process doesn't collapse to zero. Economically: even in the absence of recent shocks, there is a minimum level of uncertainty in EURUSD driven by structural macro factors.

- **α (alpha):** Shock sensitivity. Weight on yesterday's squared innovation ε²\_{t-1}. Higher α → vol reacts more aggressively to new information. Typical EURUSD values: 0.03–0.08.

- **β (beta):** Persistence/memory. Weight on yesterday's conditional variance σ²\_{t-1}. Higher β → vol decays slowly after a shock. Typical EURUSD values: 0.90–0.96.

- **α + β (persistence):** Total persistence of the variance process. Must be < 1 for stationarity. The half-life of a vol shock is:

$$
t_{1/2} = \frac{\ln 2}{\kappa} = \frac{-\ln 2}{\ln(\alpha + \beta)}
$$

For persistence = 0.98 → half-life ≈ 34 days. For persistence = 0.995 → half-life ≈ 138 days.

**Stationarity constraint:** α + β < 1, enforced by clipping persistence to 0.9999 in the code.

Parameters estimated by MLE (Maximum Likelihood Estimation) on 1 year of daily close-to-close returns from the EUR continuous future.

#### MLE Estimation

The log-likelihood for GARCH(1,1) with Normal innovations:

$$
\mathcal{L}(\omega, \alpha, \beta, \mu \mid r_{1:T}) = -\frac{1}{2}\sum_{t=1}^{T}\left[\ln(2\pi) + \ln(\sigma^2_t) + \frac{(r_t - \mu)^2}{\sigma^2_t}\right]
$$

where σ²\_t is the conditional variance at time t, recursively computed from the GARCH equation above. The optimizer maximizes L over (ω, α, β, μ) subject to ω > 0, α ≥ 0, β ≥ 0, α + β < 1. The `arch` Python library uses L-BFGS-B with analytical gradients.

#### Forward Variance Projection

GARCH(1,1) mean-reverts to the unconditional (long-term) variance:

$$
\sigma^2_{\infty} = \frac{\omega}{1 - \alpha - \beta}
$$

The forward T-horizon average variance is:

$$
\bar{\sigma}^2(T) = \sigma^2_{\infty} + \big(\sigma^2_{\text{current}} - \sigma^2_{\infty}\big) \cdot e^{-\kappa T}
$$

where:

$$
\kappa = -\ln(\alpha + \beta) \quad \text{(mean-reversion speed)}
$$

**Annualized model vol:**

$$
\sigma_{\text{GARCH}}(T) = \sqrt{\bar{\sigma}^2(T)} \times 100 \quad (\text{in \%})
$$

**Interpretation:** If current conditional vol is above long-run, GARCH projects convergence downward over time (and vice versa). Short tenors inherit more of the current regime; long tenors converge toward the unconditional mean. This produces a natural term structure shape from a single model.

**Persistence = α + β.** Typical values for EURUSD: 0.97–0.99. Higher persistence → slower mean-reversion → flatter projected term structure.

---

### Layer C — Portfolio Book Adjustment (δ\_book)

> **Design Choices**
>
> | Choice | Selected | Alternatives Considered |
> |--------|----------|------------------------|
> | Adjustment function | Linear: −α\_book × ratio | Quadratic (softer near zero, harder near limits), sigmoid, Kelly-optimal sizing |
> | α\_book | 0.20 | Higher (0.40): more aggressive de-risking. Lower (0.10): allows larger concentration before adjustment kicks in. |
> | Vega limits | Static per tenor (150K–400K) | Dynamic: scale with portfolio NAV, VIX regime, or recent P&L |
> | Clipping | ratio ∈ [−1, 1] | No clipping (allows extrapolation beyond limits), soft clipping (tanh) |

#### Purpose

Shift σ\_fair based on current portfolio vega exposure. This is **not** an alpha signal — it's a risk management overlay that adjusts quoting behavior to reduce concentration risk.

#### Mechanism

For each tenor, compute the vega loading ratio:

$$
\text{ratio}(T) = \text{clip}\left(\frac{\text{Vega}_{\text{net}}(T)}{\text{Vega}_{\text{limit}}(T)},\ -1,\ 1\right)
$$

where Vega\_net is the aggregate portfolio vega in €/vol% from all open FOP positions for that tenor, and Vega\_limit is a per-tenor risk budget.

**Book adjustment:**

$$
\delta_{\text{book}}(T) = -\alpha_{\text{book}} \times \text{ratio}(T)
$$

with α\_book = 0.20.

**Logic:**
- If Vega\_net > 0 (long vol) → ratio > 0 → δ\_book < 0 → σ\_fair decreases → less likely to generate a BUY signal → system avoids adding to an already long position
- If Vega\_net < 0 (short vol) → ratio < 0 → δ\_book > 0 → σ\_fair increases → less likely to generate a SELL signal

**Vega limits per tenor:**

| Tenor | Limit (€/vol%) |
|-------|----------------|
| 1M | 150,000 |
| 2M | 200,000 |
| 3M | 300,000 |
| 4M | 350,000 |
| 5M | 375,000 |
| 6M | 400,000 |

Limits increase with tenor because longer-dated vega is less volatile (slower theta decay, lower gamma).

---

## 4. Signal Generation

> **Design Choices**
>
> | Choice | Selected | Alternatives Considered |
> |--------|----------|------------------------|
> | Threshold | 0.20% (20 bps) symmetric | Asymmetric (tighter for sell, wider for buy to account for VRP), adaptive threshold based on recent σ\_fair − σ\_mid distribution |
> | Signal type | Ternary (CHEAP / FAIR / EXPENSIVE) | Continuous z-score, probabilistic (P(mispriced) from model uncertainty) |
> | Comparison metric | σ\_fair − σ\_mid (absolute bps) | Ratio σ\_fair / σ\_mid, z-score normalized by historical spread std |

### Threshold

$$
\text{threshold} = 0.20\% \quad (20 \text{ bps})
$$

### Signal Logic

$$
\text{signal}(T) =
\begin{cases}
\textbf{CHEAP} & \text{if } \sigma_{\text{fair}}(T) - \sigma_{\text{mid}}(T) > +0.20\% \\
\textbf{EXPENSIVE} & \text{if } \sigma_{\text{fair}}(T) - \sigma_{\text{mid}}(T) < -0.20\% \\
\textbf{FAIR} & \text{otherwise}
\end{cases}
$$

- **CHEAP** → market IV is below model → buy vol (buy options)
- **EXPENSIVE** → market IV is above model → sell vol (sell options)

### Current Snapshot (2026-04-09)

| Tenor | σ\_mid | σ\_fair | Écart | Signal |
|-------|--------|---------|-------|--------|
| 1M | 6.70% | 8.33% | +1.64% | CHEAP → Buy Vol |
| 3M | 6.50% | 8.55% | +2.04% | CHEAP → Buy Vol |
| 4M | 6.52% | 8.08% | +1.57% | CHEAP → Buy Vol |
| 6M | 6.55% | 7.61% | +1.06% | CHEAP → Buy Vol |

All tenors show σ\_fair >> σ\_mid — the model estimates that market IV is significantly underpriced relative to realized vol + GARCH projection.

---

## 5. Trade Decision Framework — Greeks & P\&L Decomposition

> **Design Choices**
>
> | Choice | Selected | Alternatives Considered |
> |--------|----------|------------------------|
> | Hedge instrument | EUR CME future (delta-hedge) | Spot FX (basis risk), no hedge (directional), delta+vega hedge (variance swap overlay) |
> | Hedge frequency | At entry (static) | Continuous (daily rebalance), gamma-threshold triggered |
> | Instrument | Vanilla FOP (calls/puts) | Straddles/strangles (pure vol), variance swaps (pure RV exposure), calendar spreads (term structure) |

Once a signal fires, the trade creates a specific Greeks exposure. Understanding this decomposition is essential for managing the position.

### 5.1 — Greeks Exposure by Trade Direction

When the signal says **CHEAP** (buy vol = buy options, delta-hedged):

| Greek | Exposure | Mechanism |
|-------|----------|-----------|
| **Δ (Delta)** | Hedged to ~0 | Delta-hedged with EUR future at entry |
| **Γ (Gamma)** | **Long** | Gains from spot moves in either direction. P&L = ½·Γ·S²·(Δspot)² |
| **Θ (Theta)** | **Short** | Pays time decay daily. Cost of carrying the gamma position |
| **ν (Vega)** | **Long** | Gains if IV increases post-trade. P&L = ν·Δσ\_impl |

When the signal says **EXPENSIVE** (sell vol = sell options, delta-hedged):

| Greek | Exposure | Mechanism |
|-------|----------|-----------|
| **Δ (Delta)** | Hedged to ~0 | Delta-hedged with EUR future at entry |
| **Γ (Gamma)** | **Short** | Loses from large spot moves. P&L = −½·Γ·S²·(Δspot)² |
| **Θ (Theta)** | **Long** | Collects time decay daily. Revenue from gamma exposure |
| **ν (Vega)** | **Short** | Gains if IV decreases post-trade. P&L = −ν·Δσ\_impl |

### 5.2 — P\&L Decomposition

Total P&L of a delta-hedged option position decomposes into three independent sources:

#### Source 1: Gamma vs Theta (Realized Vol P\&L)

$$
\text{P\&L}_{\Gamma/\Theta} \approx \frac{1}{2} \Gamma S^2 \big(\sigma^2_{\text{realized}} - \sigma^2_{\text{implied}}\big) \cdot T
$$

- **Long vol wins** if realized vol > implied vol at entry (spot moves more than priced)
- **Short vol wins** if realized vol < implied vol at entry (spot moves less than priced)

This is the fundamental bet: will the actual path variance exceed the variance the market priced into the option?

#### Source 2: Vega P\&L (IV Movement)

$$
\text{P\&L}_{\nu} = \nu \times \big(\sigma_{\text{impl},t+1} - \sigma_{\text{impl},t}\big)
$$

- **Long vol wins** if market IV rises after entry
- **Short vol wins** if market IV falls after entry

This is a mark-to-market effect. Even if the realized vol bet is correct, adverse IV moves can create drawdowns.

#### Source 3: Alpha P\&L (Model Edge)

$$
\text{P\&L}_{\alpha} \propto \sigma_{\text{fair}} - \sigma_{\text{mid}}
$$

If the model's σ\_fair is correct and the market eventually reprices toward fair value, this captures the mispricing. This is the systematic edge the model is designed to exploit.

### 5.3 — Scenario Analysis (Buy-Side Perspective)

#### Scenario A — CHEAP signal → Buy Vol → Market Was Indeed Cheap

**Setup:** σ\_mid = 6.50%, σ\_fair = 8.55%

| P\&L Source | Outcome | Condition |
|-------------|---------|-----------|
| Gamma/Theta | **Positive** | RV > σ\_mid (spot moves justify the premium paid) |
| Vega | **Positive** | σ\_mid rises toward σ\_fair (market reprices) |
| Alpha | **Positive** | Model was correct — mispricing closes |

**This is the best-case scenario.** All three P&L sources align.

#### Scenario B — CHEAP signal → Buy Vol → Vol Collapses Further

**Setup:** σ\_mid = 6.50%, σ\_fair = 8.55%, then σ\_mid drops to 4.50%

| P\&L Source | Outcome | Condition |
|-------------|---------|-----------|
| Gamma/Theta | **Negative** | RV << σ bought (spot is calm, theta eats the position) |
| Vega | **Negative** | σ\_mid falls further — MtM loss = ν × (4.50% − 6.50%) |
| Alpha | **Negative** | Model was wrong or mispricing widened |

**This is the worst case for a vol buyer.** Vol collapses post-event, spot is quiet, theta bleeds out.

#### Scenario C — CHEAP signal → Buy Vol → RV Confirms but IV Stays Low

| P\&L Source | Outcome | Condition |
|-------------|---------|-----------|
| Gamma/Theta | **Positive** | RV > σ bought — gamma hedging profits exceed theta cost |
| Vega | **Neutral/Negative** | σ\_mid doesn't reprice — no MtM gain from IV move |
| Alpha | **Partial** | Model was right on RV but IV didn't converge |

**Mixed outcome.** The realized vol bet pays off but you don't capture the vega revaluation. Common in trending FX markets where spot delivers variance but the vol surface stays depressed.

#### Scenario D — Vol Shock (Tail Event)

**Setup:** Any position, then σ\_mid jumps +4% in a day

| If Long Vol | If Short Vol |
|-------------|--------------|
| **Windfall.** Long Γ profits from spot move, long ν profits from IV spike. All sources positive. | **Catastrophic.** Short Γ hemorrhages from spot move, short ν takes massive MtM loss. Gamma/theta P&L swamps any theta collected. |

**Asymmetry:** Long vol has convex payoff (limited downside = premium paid, unlimited upside from tail events). Short vol has concave payoff (limited upside = premium collected, unlimited downside from tail events). This asymmetry is why the Variance Risk Premium exists — sellers demand compensation for this tail risk.

### 5.4 — Decision Matrix Summary

| Signal | Action | You Are | You Win If | You Lose If | Key Risk |
|--------|--------|---------|------------|-------------|----------|
| **CHEAP** | Buy options (delta-hedged) | Long Γ, Short Θ, Long ν | RV > σ\_bought or IV rises | RV << σ\_bought and IV falls | Theta bleed in low-vol regime |
| **EXPENSIVE** | Sell options (delta-hedged) | Short Γ, Long Θ, Short ν | RV < σ\_sold or IV falls | RV >> σ\_sold or IV spikes | Tail events, gap risk |
| **FAIR** | No trade | Flat | — | — | Opportunity cost |

---

## 6. Sensitivity & Assumptions

### Key Parameters & Sensitivity

| Parameter | Value | Impact of ±10% Change |
|-----------|-------|----------------------|
| W₁ (RV weight) | 0.65 | Shifts σ\_fair toward/away from realized anchor. Higher W₁ → more backward-looking |
| W₂ (GARCH weight) | 0.35 | Shifts σ\_fair toward/away from model. Higher W₂ → more regime-sensitive |
| RP (risk premium) | 1.20–1.60% | Directly shifts σ\_fair up/down. Most sensitive parameter for signal direction |
| Threshold | 0.20% | Lower → more signals, more noise. Higher → fewer signals, higher conviction |
| α\_book | 0.20 | Stronger → faster position reduction. Weaker → allows larger concentration |

### Assumptions

1. **IB model greeks are accurate** — BS IV inversion depends on IB's pricing model. Illiquid strikes may have stale or synthetic greeks.
2. **Continuous future ≈ actual underlying** — CONTFUT roll methodology may introduce small basis noise in OHLC data.
3. **Risk premium is constant per tenor** — in reality RP is time-varying and regime-dependent. A constant RP is a first-order approximation.
4. **GARCH(1,1) is correctly specified** — no leverage effect (EGARCH), no fat tails (Student-t), no long memory (FIGARCH). Adequate for EURUSD which has near-symmetric vol dynamics.
5. **Delta-space interpolation is smooth** — PCHIP handles this well for 5+ data points, but can produce artifacts with fewer strikes.
6. **Vega limits are static** — in production, these should scale with portfolio NAV and volatility regime.

### What Can Break

- **Regime shift not captured by GARCH:** A structural break (e.g., ECB policy shock) can make the unconditional variance estimate obsolete. The model will lag for ~20–40 days until the new regime feeds through.
- **Risk premium inversion:** In crisis periods, realized vol can exceed implied vol (negative VRP). The model's constant RP assumption will overestimate σ\_fair in these regimes.
- **Liquidity withdrawal:** During stress, FOP bid-ask spreads widen massively. σ\_mid becomes unreliable and signals become noise.

---

## 7. Appendix A — Day-Weight Framework: Why Not Used, When Needed

### The Framework

In interbank FX options desks, ATM implied volatility is not modeled as a smooth function of calendar time. Instead, total variance to expiry T is decomposed into discrete daily variance bricks, each scaled by a weight ω\_i reflecting the expected variance contribution of that calendar date.

#### Standard Model (no weights)

Total variance to calendar time T with a single flat vol σ:

$$
\text{Var}(T) = \sigma^2 \cdot T
$$

ATM implied vol is trivially σ for all tenors — a flat term structure.

#### Day-Weight Model

Replace calendar time with **economic time** by assigning a weight ω\_i to each day i from today to expiry. Total variance becomes:

$$
\text{Var}(T) = \sigma^2 \sum_{i=1}^{n} \omega_i \cdot dt, \quad dt = \frac{1}{365}
$$

where n is the number of calendar days to expiry and ω\_i is the weight for day i.

ATM implied vol for that expiry is then backed out from total variance using calendar time:

$$
\sigma_{\text{ATM}}(T) = \sqrt{\frac{\text{Var}(T)}{T}} = \sigma \sqrt{\frac{\sum_{i=1}^{n} \omega_i \cdot dt}{T}}
$$

The ratio of economic time to calendar time drives the ATM level:

$$
\frac{T_{\text{econ}}}{T_{\text{cal}}} = \frac{\sum_{i=1}^{n} \omega_i \cdot dt}{n \cdot dt} = \frac{\sum_{i=1}^{n} \omega_i}{n}
$$

If ω\_i = 1 for all days → ratio = 1 → flat ATM curve. If weekends have ω = 0 → ratio < 1 → ATM vol is lower than flat, with saw-tooth pattern at weekly frequency.

#### Forward Overnight Variance

The daily (forward overnight) implied variance between day i and day i+1:

$$
\text{Var}_{\text{fwd}}(i) = \sigma^2 \cdot \omega_i \cdot dt
$$

Forward overnight implied vol:

$$
\sigma_{\text{fwd}}(i) = \sigma \sqrt{\frac{\omega_i}{365}}
$$

#### Typical Weight Schedule

| Day Type | ω\_i | Rationale |
|----------|------|-----------|
| Regular weekday | 1.0 | Baseline |
| Saturday | 0.0–0.05 | Market closed, near-zero variance |
| Sunday | 0.0–0.05 | Market closed, near-zero variance |
| Currency holiday | 0.1–0.3 | Reduced liquidity, low but nonzero variance |
| Major event (NFP, ECB, FOMC) | 1.5–3.0 | Elevated expected variance from data release |
| Pre-event day | 1.0–1.2 | Slight positioning-driven variance increase |

Desks calibrate these weights by fitting to interbank broker quotes across O/N, T/N, 1W, 2W, 1M, 2M, 3M, 6M, 1Y tenors, ensuring non-negative forward variance at every date.

---

### Why This System Does Not Use Day Weights

#### Reason 1 — Wrong layer of the stack

The day-weight framework is a **curve construction** tool: given a base vol level, distribute variance across dates to produce a full ATM term structure that's arbitrage-free (non-negative forward variance). It answers *"what should σ\_ATM be on any given expiry date?"*

This system is a **signal generation** tool: compare model fair vol against market IV at specific tenors. It answers *"is 1M vol cheap or expensive?"* These are different problems at different layers:

```
Layer 3 (this system):  σ_fair vs σ_mid → signal → trade
                              ↑
Layer 2 (day weights):  daily ω_i → ATM curve construction → σ_ATM(t) for any date
                              ↑
Layer 1 (data):         interbank broker quotes, event calendars, holiday schedules
```

This system operates at Layer 3. Day weights live at Layer 2.

#### Reason 2 — The effect cancels in the signal

The signal is:

$$
\text{signal}(T) = \sigma_{\text{fair}}(T) - \sigma_{\text{mid}}(T)
$$

Both σ\_fair and σ\_mid are evaluated at the **same expiry date**. The weekend/holiday effect is already embedded in the market's σ\_mid (the market has already done its own day-weight adjustment). For the signal to be affected, the day-weight pattern would need to differentially impact σ\_fair vs σ\_mid — but σ\_fair is built from realized vol (which is computed from actual daily returns, inherently reflecting weekends as zero-return days) and GARCH (which is calibrated on the same daily series). The weekend effect is a common factor on both sides of the spread.

Quantitatively: the weekend saw-tooth amplitude on a 1M option is ~5–15 bps. The signals this system generates are 100–200 bps. The weekend effect is noise at <10% of signal magnitude.

#### Reason 3 — Insufficient data to calibrate daily weights

The day-weight model requires fitting ~365 daily weights. To avoid an underdetermined system, desks use 15–25 liquid interbank tenor quotes (O/N through 2Y) plus known event dates. This system reads 4–6 IB FOP expiries — massively insufficient to constrain 365 weights. Any calibration would be degenerate.

#### Reason 4 — Trade instruments are tenor-bucket level

The system trades vanilla calls/puts on specific FOP expiries + futures for delta hedging. Trade decisions are at the tenor level (buy 1M vol, sell 3M vol), not at the daily level (buy Wednesday expiry vs Thursday expiry). Day-weight granularity would not change any trade decision the system currently makes.

---

### When Day Weights Would Be Required

The day-weight framework becomes necessary if the system evolves to include any of the following:

#### Extension 1 — O/N or 1W Option Trading

For overnight or 1-week options, a single weekend represents 29% (2/7) of calendar time. If ω\_weekend ≈ 0, economic time is ~71% of calendar time → ATM vol for a Monday-expiry O/N is ~16% lower than for a Friday-expiry O/N (same flat vol assumption). At this tenor, the saw-tooth effect is **larger than typical signals** and must be modeled to avoid false mispricings.

**Implementation:** Insert between Step 1 and Step 2. After extracting raw σ\_mid from IB (Step 1), decompose into daily variance bricks using weights, then compute σ\_fair in economic time (Step 2) with the same weight schedule. The signal becomes:

$$
\text{signal}(t) = \sigma_{\text{fair,econ}}(t) - \sigma_{\text{mid,econ}}(t)
$$

where both are expressed in economic-time-adjusted terms.

#### Extension 2 — Calendar Spread Trading

A calendar spread (e.g., sell 1M, buy 2M) isolates the **forward variance** between months 1 and 2. The P&L depends on:

$$
\text{Var}_{\text{fwd}}(1M, 2M) = \text{Var}(2M) - \text{Var}(1M) = \sigma^2 \sum_{i=n_{1M}+1}^{n_{2M}} \omega_i \cdot dt
$$

If an NFP or ECB meeting falls inside the 1M–2M strip, the forward variance should be higher. Without day weights, the system would estimate forward variance as a smooth interpolation and miss the event premium — producing a false CHEAP signal on the forward strip.

**Implementation:** New Step 1.5 between current Step 1 and Step 2. Build a daily weight vector from an event calendar (ECB dates, NFP dates, EURUSD holidays), compute forward variance strips, and use these as inputs to σ\_fair construction.

#### Extension 3 — Intraday Expiry Precision (Cuts)

FX options can expire at different cuts within the same day (NY cut 10:00 ET, Tokyo cut 15:00 JST). Two options expiring on the same calendar date but at different cuts have different economic times if an event (e.g., ECB announcement at 13:45 CET) falls between the cuts. This requires sub-daily weight granularity:

$$
\text{Var}(t_{\text{cut}}) = \sigma^2 \sum_{i=1}^{n} \omega_i \cdot dt + \sigma^2 \cdot \omega_{\text{intraday}}(t_{\text{cut}}) \cdot dt_{\text{partial}}
$$

This level of granularity is only relevant for market-making desks. Not applicable to the current buy-side setup.

#### Summary — Implementation Roadmap

| Extension | Day Weights Needed | Insert At | Data Required | Priority |
|-----------|--------------------|-----------|---------------|----------|
| O/N / 1W options | Yes — critical | Step 1.5 (between Step 1 and Step 2) | Event calendar, holiday calendar | High if trading short-dated |
| Calendar spreads | Yes — for forward strips | Step 1.5 | Event calendar + 10+ tenor quotes for calibration | Medium |
| Intraday cuts | Yes — sub-daily | Step 1.5 with time-of-day precision | Exact event times, cut schedules | Low (market-making only) |
| Current system (1M+ vanilla FOP) | No — effect cancels at <10% of signal | N/A | N/A | N/A |

---

## 8. Appendix B — W₁/W₂ Sensitivity Analysis

### Why W₁ + W₂ = 1

σ\_fair is a **convex combination** of two volatility estimators:

$$
\sigma_{\text{fair}}(T) = W_1 \cdot \text{Anchor}(T) + (1 - W_1) \cdot \sigma_{\text{GARCH}}(T) + \delta_{\text{book}}(T)
$$

The constraint W₁ + W₂ = 1 ensures σ\_fair lies **between** the two estimators (ignoring the small δ\_book correction). Without this constraint, σ\_fair could extrapolate beyond both sources — economically meaningless. If we believe fair vol is somewhere between what *did* happen (RV + RP) and what the model *projects* (GARCH), the estimate must be an interpolation, not an extrapolation.

This reduces the problem to a single free parameter W₁ ∈ [0, 1].

### Why W₁ = 0.65 Is Arbitrary

The current value W₁ = 0.65 is a subjective prior, not an empirically calibrated optimum. The reasoning behind W₁ > 0.50:

1. **Estimator robustness.** Yang-Zhang RV is non-parametric — it measures realized path variance directly from OHLC data with no distributional assumptions. GARCH(1,1) is parametric — it assumes Normal innovations, specific variance dynamics, and stationarity. On EURUSD where vol is approximately mean-reverting and jumps are rare, the non-parametric estimator has lower model risk.

2. **Information content.** The anchor (RV + RP) uses the *same* data as GARCH (daily returns) plus additional information (OHLC range via Yang-Zhang). GARCH extracts its signal from close-to-close returns only. The anchor is informationally richer.

3. **Bias direction.** GARCH forward projections converge to the unconditional mean σ\_∞ at long horizons regardless of current conditions. If the unconditional mean is mis-estimated (e.g., structural break), GARCH pulls σ\_fair toward a stale level. The RV anchor adapts faster because the rolling window drops old observations.

However, **0.65 specifically** (vs 0.60 or 0.70) has no formal justification. The optimal W₁ depends on the forecasting horizon, the current vol regime, and the specific pair traded. It should be calibrated empirically.

### Optimal W₁ — Calibration Framework

The theoretically correct W₁\* minimizes the out-of-sample forecasting error:

$$
W_1^* = \arg\min_{w \in [0,1]} \sum_{t=1}^{T} \Big(w \cdot \text{Anchor}_t + (1-w) \cdot \sigma_{\text{GARCH},t} - \sigma_{\text{realized},t+\tau}\Big)^2
$$

where σ\_realized,t+τ is the actual realized vol over the subsequent τ-day window (matching the tenor). This is a simple univariate OLS problem with a closed-form solution:

$$
W_1^* = \frac{\sum_t (A_t - G_t)(R_{t+\tau} - G_t)}{\sum_t (A_t - G_t)^2}
$$

where A\_t = Anchor\_t, G\_t = σ\_GARCH,t, R\_{t+τ} = σ\_realized,t+τ, clipped to [0, 1].

This calibration requires a walk-forward backtest on historical data — a natural task for alpha-research v2.

### Sensitivity Table — Current Data (2026-04-09)

Components per tenor:

| Tenor | σ\_mid | Anchor (RV+RP) | σ\_GARCH | δ\_book |
|-------|--------|-----------------|----------|---------|
| 1M | 6.6968% | 9.0311% | 7.0396% | −0.00001% |
| 3M | 6.5020% | 9.3586% | 7.0353% | −0.00000% |
| 4M | 6.5155% | 8.6509% | 7.0332% | −0.00000% |
| 6M | 6.5481% | 7.9160% | 7.0289% | −0.00000% |

**Observation:** For the current snapshot, Anchor > σ\_GARCH > σ\_mid across all tenors. Both estimators independently conclude that market IV is cheap. This means the signal direction is **invariant to W₁** — any W₁ ∈ [0, 1] produces a CHEAP signal.

σ\_fair as a function of W₁:

| W₁ | W₂ | σ\_fair 1M | écart 1M | σ\_fair 3M | écart 3M | σ\_fair 4M | écart 4M | σ\_fair 6M | écart 6M |
|------|------|------------|----------|------------|----------|------------|----------|------------|----------|
| 0.00 | 1.00 | 7.0396% | +0.34% | 7.0353% | +0.53% | 7.0332% | +0.52% | 7.0289% | +0.48% |
| 0.20 | 0.80 | 7.4379% | +0.74% | 7.5000% | +1.00% | 7.3567% | +0.84% | 7.2063% | +0.66% |
| 0.35 | 0.65 | 7.7366% | +1.04% | 7.8485% | +1.35% | 7.5994% | +1.08% | 7.3394% | +0.79% |
| **0.50** | **0.50** | **8.0354%** | **+1.34%** | **8.1970%** | **+1.69%** | **7.8421%** | **+1.33%** | **7.4725%** | **+0.92%** |
| **0.65** | **0.35** | **8.3341%** | **+1.64%** | **8.5454%** | **+2.04%** | **8.0847%** | **+1.57%** | **7.6055%** | **+1.06%** |
| 0.80 | 0.20 | 8.6328% | +1.94% | 8.8939% | +2.39% | 8.3274% | +1.81% | 7.7386% | +1.19% |
| 1.00 | 0.00 | 9.0311% | +2.33% | 9.3586% | +2.86% | 8.6509% | +2.14% | 7.9160% | +1.37% |

### Key Observations

**1. Signal direction is W₁-invariant for this snapshot.**

All écarts are positive for every W₁ ∈ [0, 1]. The signal is always CHEAP. This is because both sources (Anchor and GARCH) are above σ\_mid. The choice of W₁ affects **signal magnitude** (conviction), not **signal direction** (trade decision).

**2. W₁\* that would make σ\_fair = σ\_mid is negative.**

Solving W₁\* analytically from σ\_fair = σ\_mid:

$$
W_1^* = \frac{\sigma_{\text{mid}} - \sigma_{\text{GARCH}} - \delta_{\text{book}}}{\text{Anchor} - \sigma_{\text{GARCH}}}
$$

| Tenor | W₁\* |
|-------|------|
| 1M | −0.17 |
| 3M | −0.23 |
| 4M | −0.32 |
| 6M | −0.54 |

W₁\* < 0 means no convex combination of the two sources can reproduce current market IV — the market is pricing vol below *both* estimators. Either the market is genuinely cheap, or both estimators are biased upward (e.g., RV still reflects a recent high-vol episode that the market considers over).

**3. W₁ mostly scales signal magnitude, not relative tenor ranking.**

The tenor ordering of écarts is stable: 3M > 1M ≈ 4M > 6M regardless of W₁. This means tenor-relative positioning (e.g., "buy 3M vol over 6M vol") is robust to the weight choice. Only the absolute conviction level changes.

**4. Sensitivity is proportional to (Anchor − σ\_GARCH).**

$$
\frac{\partial \sigma_{\text{fair}}}{\partial W_1} = \text{Anchor}(T) - \sigma_{\text{GARCH}}(T)
$$

| Tenor | ∂σ\_fair/∂W₁ |
|-------|-------------|
| 1M | +1.99%/unit |
| 3M | +2.32%/unit |
| 4M | +1.62%/unit |
| 6M | +0.89%/unit |

3M is the most W₁-sensitive tenor (largest gap between Anchor and GARCH). 6M is the least sensitive (GARCH has nearly converged to the anchor level at 6-month horizon). This means W₁ calibration error has the largest impact on short/medium tenors where the two estimators disagree most.

### When W₁ Choice Matters

The current snapshot is an edge case where both sources agree on direction. W₁ becomes **decision-critical** when:

$$
\text{Anchor}(T) > \sigma_{\text{mid}}(T) > \sigma_{\text{GARCH}}(T) \quad \text{or} \quad \sigma_{\text{GARCH}}(T) > \sigma_{\text{mid}}(T) > \text{Anchor}(T)
$$

In these regimes, σ\_mid lies *between* the two sources — the convex combination can land on either side of σ\_mid depending on W₁. A W₁ that's too high might produce a CHEAP signal when the GARCH model says EXPENSIVE (or vice versa). This is exactly when proper calibration matters.

**Example (hypothetical):** Anchor = 6.00%, σ\_GARCH = 7.50%, σ\_mid = 6.80%.

| W₁ | σ\_fair | écart | Signal |
|----|---------|-------|--------|
| 0.65 | 6.525% | −0.275% | EXPENSIVE (sell vol) |
| 0.50 | 6.750% | −0.050% | FAIR (no trade) |
| 0.35 | 6.975% | +0.175% | FAIR (no trade) |
| 0.20 | 7.200% | +0.400% | CHEAP (buy vol) |

Here the signal **flips direction** between W₁ = 0.65 and W₁ = 0.20. This is the regime where an uncalibrated W₁ generates wrong trades.

### Calibration Roadmap

The proper calibration of W₁ belongs in **alpha-research v2** (Step 6 of the project roadmap):

1. Collect historical daily data: Anchor(t), σ\_GARCH(t), σ\_realized(t+τ) for each tenor τ
2. Walk-forward OOS regression: rolling 1Y train → 3M test, compute W₁\*(t) per window
3. Analyze stability: if W₁\* is stable across windows (e.g., 0.55–0.70), the fixed-weight approach is validated. If W₁\* varies widely, implement regime-conditional weights
4. Extend to regime-switching: W₁(t) = f(persistence\_t, VRP\_t, vol-of-vol\_t) where the weight itself is a function of market state

Until this calibration is performed, W₁ = 0.65 is a reasonable but unvalidated prior.
