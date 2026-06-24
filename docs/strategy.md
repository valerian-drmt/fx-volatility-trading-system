# Trading strategy & desk philosophy

> How the FX-vol desk is meant to be used. One principle drives the whole UX:
> **the desk surfaces information; it does not advise.** The trader interprets the
> indicators and decides the trade. No automated recommendation on the live desk.

## 1. The desk is an instrument panel, not an autopilot

This is a **single-trader, discretionary** vol desk. The edge is the *trader's*
reading of the volatility surface — not a black-box "buy this" signal. So the
live desk:

- **shows values** (IV, z-scores, greeks, VaR, regime gate) for the trader to interpret;
- **never prescribes** a trade, a side, or a structure;
- leaves the **composition of the position fully free** (products, strikes, tenors).

Automated execution (a "robot": regime gates + Kelly sizing + auto delta-hedge) is
explicitly **out of scope** here — it belongs to a later, opt-in research phase
(R12+), behind a flag and a kill-switch, paper-first.

## 2. PCA modes = indicators, not recommendations

The vol surface is decomposed into **3 principal components** (Signals view). Each
is a **z-score the trader reads** — rich / cheap / neutral relative to history:

| Mode | What it measures | High z (+σ) | Low z (−σ) |
|------|------------------|-------------|------------|
| **PC1 — level** | overall vol up/down | vol rich | vol cheap |
| **PC2 — skew** | put/call asymmetry (10Δp vs 10Δc) | puts bid / downside fear | calls bid |
| **PC3 — convexity** | wings vs ATM (butterfly shape) | wings rich | wings cheap |

These are **diagnostics**. The desk does **not** turn a z-score into a "trade X"
recommendation — no `recommended_structure`, no in-strategy flag, no nudge. The
trader looks at PC1/PC2/PC3 (+ the per-cell IV z-score field) and forms a view.

## 3. The IV surface = a z-score field

The Signals heatmap prints, per (tenor × Δ) cell, the **IV** and a **cross-sectional
z-score** (cell vs the whole current surface): + = rich vs the surface (wings),
− = cheap (ATM); 10Δp vs 10Δc reveals the put/call skew. It shows **only the tenors
the engine actually emits** — no padded / fake rows. Again: a field to *read*, not
a buy list.

## 4. The Trade tab = free product composition

The trader builds the position **freely**:

- **products**: EUR FX futures + options (6E €125k, M6E €12.5k);
- **delta levels**: 10Δp · 25Δp · ATM · 25Δc · 10Δc;
- **tenors**: 1M, 2M, … (whatever the surface offers);
- **side**: BUY / SELL, any leg.

Common option **structures** (straddle, strangle, butterfly, calendar, …) are
**vocabulary and reference** — the preview can name what a leg-set expresses and
tells its **risk truth** (max-loss, greeks, premium) — but **nothing is imposed**.
There is **no fixed catalogue of "the N strategies you may trade"** and no mapping
that forces a PCA mode onto a specific structure.

## 5. Safety boundary (live)

- **Read-only public**: anyone can read the desk; **writes (orders/config) require auth**.
- **Paper first**: `READ_ONLY_API=yes` / paper trading until an *explicit* decision to go live.
- The desk shows the **risk truth** before any submit (max-loss, net greeks, margin) —
  but the decision, always, is the trader's.

## 6. What this means for the code

- Signals surfaces z-scores (PC1/PC2/PC3 + IV-surface field) — **no recommendation rendering**.
- Trade/OrderBuilder is **product/delta/tenor driven** — no forced `structure_type`.
- The "in-strategy structures" recommendation set is **dropped** (no advice layer).
- Structure *templates* may remain as **buildable references** (named previews), never as prescriptions.
