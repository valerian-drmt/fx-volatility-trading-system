# Audit 3 — Risk Engine & Limits — Findings

**Run:** 2026-08-01 · **Scope:** `docs/audits/03-risk-engine.md` · **Wave:** 2 (builds on Wave 1)
**Method:** static line review + spec-vs-code matrix + local pure-Python scenario replays
(`PYTHONPATH=src`, no stack, no IB, no live URLs). **No source file was modified.**

---

## Executive summary

The risk engine itself is a small, well-behaved service: it is stateless across restarts
(positions re-read from Postgres every cycle), it reconnects to IB on the nightly gateway
bounce, and its position mirror is protected by a trustworthy-snapshot guard so an IB
disconnect cannot wipe the book. That part is sound.

What is not sound is everything the risk numbers are *used for*.

**There is no enforced greek limit in this system.** `core/risk/greek_limits.py` derives four
caps from a stress-loss budget exactly as `docs/strategy/risk.md` describes, and then the
only consumer is a `GET /portfolio/greek-limits` endpoint that renders them on a panel.
Nothing compares a live greek against a cap. Nothing blocks, alerts, or flattens on a breach.
The single risk gate that *does* block a trade — `vega_under_book_limit` — reads a
book-vega total that is **only ever recomputed on the mock-fill path**, so in live trading it
stays pinned at its bootstrap `0.0` and the gate degenerates into a per-trade vega check with
no memory of the book. Combined with the absence of any staleness gate on risk data, the
system is **fail-open on every risk axis**: when the risk engine is down, stale, or publishing
garbage, execution is unaffected.

Two Wave-1 findings turn out to have severe downstream consequences inside this scope, which
is why both Blockers here are consequences rather than new root causes:

- **QM-01** (`bs_*` return `0.0` at `T<=0`) plus the engine's own `T>0` guard means an option
  position **on its expiry date has every greek *and its `iv`* overwritten with NULL**. NULL
  `iv` is also the exact condition that makes `_resolve_book` drop a position, so the position
  simultaneously vanishes from the aggregate greeks, from VaR, from marginal-VaR, from the
  stress grid, from the greeks ladder and from the vega-PCA projection — on the one day it
  carries the most pin risk. Measured: a deep-ITM call worth **$1,250,000 of delta the day
  before expiry reads as NULL/0 on expiry day**.
- **QM-02** (`interpolate_iv` always returns `None`) means `price_position` always reports
  `n_surface_missing > 0`, which makes `position_monitor` take its fallback branch, which
  hardcodes **`current_delta_unhedged = 0.0`**. The automated delta hedge therefore never
  fires — and it fails *open*: missing vol data is silently read as "the book is delta-flat".

Beyond those, the recurring pattern the audit was asked to hunt is present throughout: **every
missing input degrades into a number that makes risk look smaller or cleaner than it is** — a
hardcoded 8% vol for any interpolated tenor pillar, positions dropped from VaR when their IV is
NULL (26% VaR shrink in the replayed scenario below), options dropped from a stress scenario
when the shocked IV goes negative, a fabricated spot taken from an arbitrary option strike when
no future has a mark. None of these are flagged in any payload.

Finally, the portfolio-level greeks the risk engine actually *publishes*
(`latest_greeks:portfolio`, `/ws/risk`) omit the contract multiplier entirely and use different
vega/gamma scalings from every other consumer — they are off by **125,000×** versus
`/portfolio/aggregate-greeks`. Nothing renders them today (the frontend uses the `/ws/risk`
beat only as a REST-invalidation trigger), so this is a High rather than a Blocker, but it means
the engine's own headline output is both wrong and dead.

### Counts

| Severity | Count |
|---|---|
| Blocker | 2 |
| High | 7 |
| Medium | 12 |
| Low | 6 |
| **Total** | **27** |

### Verified clean (worth recording)

- **Engine restart / state rebuild is correct.** `RiskEngine` holds no book state; every cycle
  re-reads `open_position` from Postgres and the surface from Redis. An exception inside
  `run_cycle` re-raises through `observed_cycle` and exits the container, which Docker restarts
  — no half-initialised risk publish is possible.
- **IB disconnect cannot publish a partial position snapshot.** Positions come from the DB, not
  from a live IB scan; `position_sync` gates its DELETEs on `account_is_reporting()` so a dead
  IB feed leaves the mirror intact instead of flattening it (`position_sync.py:200-224`).
- **Euler allocation is mathematically right.** Replayed 250 scenarios over a 4-position book:
  components sum to `8934.27` vs `portfolio_var_usd = 8934.28` (rounding only).
- **`compute_caps` matches the spec formula-for-formula**, including the non-obvious
  `2·β·L*/(s²·spot·1e4)` gamma inversion, and its sanity values reproduce
  (`nav_base=812k, spot=1.08 → δ $243.6k / V $5,075 / Γ $3,007 per pip`).
- **`nav_base` anti-procyclicality anchor and the EWMA are implemented as specified.**
- **Futures sign handling is correct** in `_aggregate_greeks`, `_load_positions_from_db`
  (`signed_qty` from `side`), `_resolve_book` and `reval_book`; short positions carry negative
  quantity end-to-end. A regression test locks in the `"FUTURE"` vs IB `"FUT"` literal.

---

## Findings table

| ID | Sev | Location | One-liner |
|---|---|---|---|
| R3-01 | **Blocker** | `src/engines/risk/engine.py:273,338,384,397` | On its expiry date an option has all greeks **and `iv`** written NULL, which also drops it from VaR/stress/marginal-VaR — $1.25M of delta disappears silently |
| R3-02 | **Blocker** | `src/api/orchestration/position_monitor.py:208,247` | `current_delta_unhedged` hardcoded to `0.0` whenever the reprice is degraded (always, per QM-02) → automated delta hedge never fires, fails **open** |
| R3-03 | High | `src/api/routers/trade.py:1156` vs `:1032` | `refresh_book_state` runs only on the mock path → in live trading `book.total_vega_usd` stays `0.0` forever and the only enforced limit becomes per-trade |
| R3-04 | High | `src/api/routers/portfolio_panel.py:795-802` | When no future carries a `market_price`, `_resolve_book` uses **an option's strike as the spot** — the whole VaR/stress family is then computed at a fabricated spot |
| R3-05 | High | `src/api/routers/portfolio_panel.py:816` | Options with NULL `iv` are silently excluded from `baselines` → VaR/ES/marginal-VaR/stress shrink with no warning (26% in the replay); `n_positions` reports the truncated count |
| R3-06 | High | `src/engines/risk/engine.py:524-538`, `src/core/vol/tenors.py:231` | `_iv_for` requires a non-null `strike`, but interpolated display pillars carry `strike=None` → the whole pillar is dropped and greeks fall back to a hardcoded **8% vol**, never flagged |
| R3-07 | High | `src/engines/risk/engine.py:540-566` | The published portfolio greeks omit the contract multiplier and use different vega/gamma scalings than every other consumer (**125,000×** off vs `/aggregate-greeks`); M6E counted as 6E |
| R3-08 | High | `src/core/risk/greek_limits.py` (all callers) | **No greek limit is enforced anywhere**: caps are display-only, no breach action, no staleness gate on risk data, no kill switch — the system is fail-open on every risk axis |
| R3-09 | High | `src/bus/publisher.py:64-65`, `src/engines/risk/engine.py:533` | A NaN IV passes both the truthiness filter and the `sigma <= 0` guards → NaN greeks → `json.dumps` emits bare `NaN`/`Infinity` (invalid JSON) on the risk channel |
| R3-10 | Medium | `src/engines/risk/engine.py:358` vs `src/core/risk/stress.py:41` | `vanna_usd` is written at `×0.01` by the engine and at `×1e-4` by the stress module — same field name, **100×** apart; the doc's unit matches only the latter |
| R3-11 | Medium | `src/api/routers/portfolio_panel.py:967-980` | Marginal-VaR `factor` tag compares quantities with ~7 orders of magnitude of scale difference → it is effectively always `"spot"`, `"curv"` is unreachable |
| R3-12 | Medium | `src/api/routers/portfolio_panel.py:1390-1391` | `/pnl-attribution` feeds **decimal** IV deltas into a `$/vol-point` vega on IB-live rows (booked rows use percent) → the vega term is 100× understated on half the book |
| R3-13 | Medium | `src/engines/risk/engine.py:350` vs `src/core/risk/greek_limits.py:101` | `delta_usd` is EUR notional (`∂PnL/∂F`), not USD cash-delta; `delta_cap_usd` compares it to a USD budget using a dimensionless shock → true stress loss at the cap is ~1.08× the budget |
| R3-14 | Medium | `portfolio_panel.py:2122-2124` vs `marginal_var.py:25` | `/var` returns VaR as a **negative** number, `/marginal-var` and `/var-factors` as **positive** losses — same quantity, same tab, opposite signs |
| R3-15 | Medium | `trade.py:130-146` vs `engine.py:253-291` | Two divergent position books (IB-mirrored `open_position` vs API-booked `booked_position`); the enforced vega limit reads one, all live greeks/VaR read the other, nothing reconciles the greeks |
| R3-16 | Medium | `config_catalog.py:49` vs `greek_limits.py:101` | The enforced `max_book_vega_usd` is a static `5000` that does not track the derived `vega_cap_usd` — at a 100k NAV the gate is 8× looser than the documented cap |
| R3-17 | Medium | `src/engines/risk/engine.py:139` | The heartbeat is written **before** the cycle work, so an engine that skips every cycle still reports healthy on `/health` (heartbeat-only) |
| R3-18 | Medium | `docs/strategy/risk.md` §VaR | VaR methodology gaps: horizon/confidence/window undocumented, `DEFAULT_SHOCKS` undocumented, realised-vol Δvol proxy undocumented, and the UI √t-scales a full-reval option-book VaR |
| R3-19 | Medium | `src/core/risk/stress.py:111-112` | `reval_book` **skips** an option whose shocked IV goes ≤ 0 instead of flooring it — the position contributes 0 P&L in exactly the most violent vol scenarios |
| R3-20 | Medium | `portfolio_panel.py:2076,2113,982` | `/var` and `/marginal-var` each run an uncached full-reval simulation synchronously on the event loop (~0.53 s blocked at 50 positions × 250 scenarios) on a public read-only endpoint |
| R3-21 | Medium | `src/core/risk/greek_limits.py:146-153` | `gl.regime_mult` is dead code — the router inlines a *different* definition (90-day median baseline, not the spec's "calm baseline") |
| R3-22 | Low | `greek_limits.py:3`, `portfolio_panel.py:2173`, `docs/README.md:43` | `docs/strategy/greek-limits.md` no longer exists (renamed `risk.md`) and the cited `§2/§6/§8` anchors have no counterpart |
| R3-23 | Low | `docs/strategy/risk.md:9-24` | Doc says book greeks come from `structures.py:bs_greeks` and that "all six" show as book before → after — `NetGreeks` has four, no multiplier, and no before/after |
| R3-24 | Low | `src/shared/contracts.py:43` | `parse_local_symbol` accepts only a 4-digit strike field; a finer strike silently drops the position from **every** risk path (`continue` at `engine.py:263`) |
| R3-25 | Low | `docs/strategy/risk.md:54` | "All eight policy values" — `CONFIG_DEFAULTS` has nine |
| R3-26 | Low | `src/core/risk/marginal_var.py:60` | `diversification_pct` can go negative (VaR_p > Σ standalone) with no clamp or label |
| R3-27 | Low | `src/engines/risk/engine.py:340` | `iv == FALLBACK_IV` is used as a "not found" sentinel — a genuine 8.000% surface IV triggers a needless BS inversion of the mark |

---

## R3-01 — **Blocker** — Expiry-day blackout: greeks *and* IV are NULLed, which also deletes the position from every VaR/stress path

**Files:** `src/engines/risk/engine.py:273, 325, 338, 384, 397` · `src/api/routers/portfolio_panel.py:816`
**Builds on:** QM-01.

### Evidence

`_load_positions_from_db` computes time-to-expiry from a whole-day difference and floors it:

```python
dte_days = (p.expiry - today).days if p.expiry else 0     # engine.py:273
T = max(dte_days, 0) / 365.0
```

On the expiry date itself `dte_days == 0`, so `T == 0.0`. `_persist_position_snapshots` then
gates the entire option branch on `T > 0`:

```python
iv: float | None = None                                    # engine.py:325
delta = gamma = vega = theta = pnl = None
...
elif right in ("C", "P") and K > 0 and T > 0:              # engine.py:338  ← not taken
    iv = self._iv_for(...)
    delta = qty * bs_delta(...) * mult
```

Because the branch is skipped, `iv`, `delta`, `gamma`, `vega`, `theta`, `vanna`, `volga` all
stay `None` and are written straight back onto the live row:

```python
live_pos.delta_usd = delta_dec   # None
live_pos.iv        = iv_dec      # None  ← overwrites yesterday's good IV
```

The `iv` NULL is the second half of the failure. `_resolve_book` — the single entry point for
`/var`, `/marginal-var`, `/var-factors`, `/stress-grid`, `/greeks-ladder` and `/vega-pca` —
requires a truthy `p.iv` to admit an option into the book:

```python
elif spec.option_type and spec.strike and p.expiry and p.iv:   # portfolio_panel.py:816
```

So the position is not merely mis-marked, it is **absent** from every risk view simultaneously.

Measured (spot 1.0800, strike 1.0000, qty 10, mult 125 000):

```
T = 1/365 (day before expiry):  delta_usd = 1,250,000
T = 0     (expiry day):         bs_price = 0.0, bs_delta = 0.0 → delta_usd = NULL
                                true value: intrinsic $100,000, delta_usd $1,250,000
```

Note that `_resolve_book` itself clamps `T = max(0.001, …)`, so the stress path *would* have
priced the position correctly — it never gets the chance, because the IV column was already
NULLed by the engine.

The same 0-floor exists independently in `core/positions/position_pricing.py:88`
(`days = max(0, (expiry - now.date()).days)`), so the booked-position path expires a day early too.

### Impact

On every expiry day the book's largest and least hedgeable exposure becomes invisible to the
operator, to the delta hedge, to the VaR chart and to the marginal-VaR table at the same
moment. CME EUR FOPs remain live for a full session on their expiry date; this is precisely the
window where pin risk is maximal.

### Recommended fix (not applied)

1. Fix QM-01 at the root: return intrinsic/limit greeks from `core/pricing/bs.py` at `T <= 0`
   (`price = max(F-K,0)`, `delta = 1{F>K}` for a call, `gamma = vega = theta = 0`).
2. Compute `T` from the actual expiry *instant* (CME EUR FOP settlement time), not a date
   difference, and floor at a small epsilon rather than 0.
3. Never write `NULL` over a previously-good `iv`: keep the last known value and add an
   explicit `iv_source` / `greeks_stale_at` column so a consumer can distinguish "no data"
   from "zero".
4. Make `_resolve_book` admit an option without an IV using a documented fallback and count it
   in the response (see R3-05), instead of dropping it.

---

## R3-02 — **Blocker** — Automated delta hedge is permanently disabled and fails *open*

**Files:** `src/api/orchestration/position_monitor.py:203-247, 374-405` ·
`src/core/positions/position_pricing.py:119-131` · `src/core/pricing/bs.py:116-144`
**Builds on:** QM-02.

### Evidence

`price_position` marks a leg as "surface missing" whenever `interpolate_iv` cannot resolve an IV:

```python
if surface is not None:
    sigma = interpolate_iv(surface, leg.tenor, leg.strike, spot)   # position_pricing.py:120
if sigma is None:
    sigma = leg.fallback_iv
    if sigma is not None:
        n_missing += 1                                             # :124
```

QM-02 established that `interpolate_iv` reads the retired flat schema while the live surface is
nested, so it returns `None` for every leg. Therefore `n_surface_missing >= 1` on every
position, always. `_monitor_one` then takes the else branch:

```python
if live_mark is not None and live_mark.n_surface_missing == 0:     # :208  ← never true
    current_delta_unhedged = live_mark.total_delta
else:
    ...
    current_delta_unhedged = 0.0                                   # :247
```

and feeds that literal zero to the hedge decision:

```python
decision = check_delta_hedge_needed(
    delta_unhedged=float(current_delta_unhedged),                  # :384  ← always 0.0
    threshold=cfg["rebalance_threshold_delta"],                    # default 0.05
    ...
)
```

`check_delta_hedge_needed` returns `skip_reason="below_threshold"` for `|0.0| < 0.05`
unconditionally. **No `HedgeOrder` row can ever be created by the monitor.**

The same zero is persisted to `booked_position_metric_history.current_delta_unhedged`
(`:332`), so it also poisons:
- `/positions` `total_delta_unhedged` (`positions.py:722`) — always `0.0`;
- `/pnl-attribution` booked rows — `delta_then`/`delta_now` always `0.0`, so the delta term of
  the Taylor decomposition is always `$0` and its whole contribution lands in `residual`.

### Impact

The documented safety mechanism that keeps net delta inside a band
(`docs/strategy/risk.md` §"Delta hedging") does not run, and its failure mode is the worst
possible one: **absent vol data is interpreted as "the book is flat"**. A structure whose delta
has drifted to ±10 futures reads as 0 and never triggers. The operator sees `total_delta_unhedged
= 0.0000` on the panel, which is indistinguishable from a genuinely hedged book.

### Recommended fix (not applied)

1. Fix QM-02 (schema alignment / consolidate onto one `core/pricing` implementation).
2. **Invert the fallback to fail closed**: when the live delta is unavailable, do not substitute
   `0.0`. Emit `current_delta_unhedged = None`, have `check_delta_hedge_needed` return a
   `skip_reason="no_delta_available"` decision, and raise an operator alert. A hedge that cannot
   be evaluated is an incident, not a no-op.
3. Add a regression test that asserts a book with a known non-zero delta and a *missing* surface
   does **not** silently produce `below_threshold`.

---

## R3-03 — High — The only enforced greek limit never sees the live book

**Files:** `src/api/routers/trade.py:130-146, 506-524, 1032-1042, 1156` ·
`src/api/orchestration/book_state_refresh.py`

### Evidence

The one blocking risk gate in the whole system is:

```python
post_vega = book_total_vega_usd + structure_vega_usd
checks.append(Check("vega_under_book_limit", abs(post_vega) <= max_book_vega_usd, ...))
```
(`core/trade_preview.py:890-894`; a failed check sets `state = "blocked"`, and
`revalidate_preview` refuses to submit a preview whose state is not `valid_for_submit`.)

`book_total_vega_usd` comes from `BookStateSnapshot.total_vega_usd`, which `_load_book`
bootstraps to `0.0` when no current row exists. The only writer is `refresh_book_state`, and
its only caller is **line 1156 — inside the mock-fill path**. The live path returns at line 1032:

```python
        ee_result = await _post_execution_engine("/internal/structure/submit", {...})
        return { "success": True, ... "execution_mode": "live", ... }   # :1032 — returns here
    # ── MOCK PATH ──
    ...
    await refresh_book_state(db)                                        # :1156 — unreachable in live
```

`grep -rn refresh_book_state src` confirms no other production caller (execution-engine cannot
import `api`, per the import-linter contracts). There is no scheduler entry either.

Two further weaknesses even when it *is* refreshed:
- it sums `BookedPosition.entry_vega_usd_per_volpt` — **entry-time** vega, never re-marked;
- it hardcodes `total_delta=0.0` (`book_state_refresh.py:83`), so there is no book-delta figure
  to enforce against at all.

### Impact

In live trading the gate evaluates `|0 + structure_vega| <= 5000` on every ticket. Vega
accumulates without bound across trades: ten consecutive $4,900/vol-pt structures all pass, for
a book vega of $49,000 against a documented cap of ~$5,075. This is the exact failure mode the
audit brief calls out — "the system can build positions far beyond intent".

### Recommended fix (not applied)

- Compute the pre-trade book greeks from the **live** source (`SUM(open_position.vega_usd)`,
  i.e. what `/portfolio/aggregate-greeks` already returns), not from a snapshot table that is
  refreshed on one of two code paths.
- If the snapshot table is kept, refresh it on the live fill cascade as well and on a timer.
- Re-run the vega gate at **submit** time, not only at preview time; `revalidate_preview`
  currently re-checks regime/expiry/z-score but not the book limit.

---

## R3-04 — High — `_resolve_book` fabricates the spot from an option strike

**File:** `src/api/routers/portfolio_panel.py:789-802`

### Evidence

```python
current_spot: float | None = None
for p in open_positions:
    spec = parse_local_symbol(p.structure)
    if spec and spec.instrument_type == "FUTURE" and p.market_price:
        current_spot = float(p.market_price); break
if current_spot is None:
    for p in open_positions:
        spec = parse_local_symbol(p.structure)
        if p.market_price and p.iv and spec and spec.strike:
            current_spot = float(spec.strike)      # ← an option STRIKE used as the spot
            break
```

The fallback picks the first option in id order and adopts **its strike** as the underlying
price. On an options-only book (no future held, or the future's `market_price` not yet
published by `position_sync`), a 1.1600-strike call makes the entire risk family evaluate at
`spot = 1.1600` while the market is at 1.0800.

Note this happens even though the live spot is one Redis `GET` away
(`latest_spot:EURUSD`); the router simply does not read it, although `/var` and `/marginal-var`
already hold a `RedisDep`.

### Impact

Every consumer of `_resolve_book` — `/var`, `/marginal-var`, `/var-factors`, `/stress-grid`,
`/greeks-ladder`, `/vega-pca` — silently reports numbers computed at a wrong spot. At an 8-figure
error in moneyness, a short ATM straddle is revalued as a deep-ITM/OTM pair: gamma and vega
collapse and VaR drops accordingly. `current_spot` is echoed in the response, so it is
*detectable*, but nothing flags it and no panel compares it against the live tick.

### Recommended fix (not applied)

Read `latest_spot:EURUSD` from Redis as the primary source, with the futures `market_price` as
the fallback. If neither is available, return `current_spot = None` and an explicit
`spot_unavailable` reason — never synthesise one from a strike.

---

## R3-05 — High — Positions with a NULL IV are silently deleted from VaR (fake diversification)

**File:** `src/api/routers/portfolio_panel.py:806-826, 2076-2091`

### Evidence

```python
elif spec.option_type and spec.strike and p.expiry and p.iv:   # :816
    baselines.append({...})
# no else — the position is dropped, nothing is counted, nothing is logged
```

`p.iv` is NULL whenever the risk engine could not resolve one: on expiry day (R3-01), before the
first snapshot after an engine restart (up to 30 s, `HISTORY_PERSIST_CYCLES`), or for any
position whose `localSymbol` did not parse (R3-24). Every downstream endpoint then reports
`n_positions = len(baselines)` — the *truncated* count — so even the coverage indicator agrees
with the truncated book.

Replayed 250 historical scenarios over a 4-position book (short 1.08 straddle, long 1.14 call,
long 4 futures):

```
full book               VaR99 = $8,934.28   diversification 61.6%
short straddle dropped  VaR99 = $6,607.71   → 26.0% understated
```

The drop is worse than 26% for the realistic case, because the legs most likely to lose their IV
(near-expiry short options) are the ones carrying the gamma.

Contrast with `/portfolio/aggregate-greeks`, which *does* expose `n_with_snapshot` alongside
`n_open_positions` — the right pattern, just not applied here.

### Impact

VaR, ES, the P&L histogram, per-factor VaR, the stress grid and the marginal-VaR table all
shrink toward zero as data quality degrades. Fewer risk factors read as more diversification,
which is the textbook version of the failure the audit brief names.

### Recommended fix (not applied)

- Return `n_open_positions` **and** `n_priced` on every `_resolve_book` consumer, plus a
  `positions_excluded: [{id, reason}]` list.
- Admit an option with a NULL IV using an explicit, labelled fallback (last known IV from
  `open_position_history`, then a documented surface ATM) rather than dropping it.
- Consider degrading the endpoint to `null` with a reason when coverage falls below a threshold
  — a VaR computed on 40% of the book is worse than no VaR.

---

## R3-06 — High — Silent hardcoded 8% vol, including for every interpolated tenor pillar

**Files:** `src/engines/risk/engine.py:66, 524-538` · `src/core/vol/tenors.py:210-231`

### Evidence

```python
FALLBACK_IV = 0.08          # engine.py:66

def _iv_for(self, surface, tenor, strike) -> float:
    pillars = surface.get(tenor)
    if not isinstance(pillars, dict):
        return FALLBACK_IV
    candidates = [
        (p["strike"], p["iv"]) for p in pillars.values()
        if isinstance(p, dict) and p.get("iv") and p.get("strike")   # ← requires a strike
    ]
    if not candidates:
        return FALLBACK_IV
```

`to_display_surface` produces two kinds of pillar. A pillar that matches a listed expiry keeps
its real cells (with strikes). A pillar that does **not** is interpolated and written as:

```python
out[pillar] = {d: {"iv": iv, "strike": None, "source": source} for d, iv in iv_by.items()}
```
(`tenors.py:231`)

Every cell of an interpolated pillar therefore fails `p.get("strike")`, `candidates` is empty,
and `_iv_for` returns the hardcoded 8% — **even though a perfectly good interpolated IV was
sitting right there**. The `"source"` field that would let the engine tell the two apart is
ignored. With 6 display pillars (1M…6M) and typically 2-4 listed EUU expiries in range, this
affects a majority of the tenor grid.

The mark-inversion rescue only fires when execution-engine has published an
`option_marks:EUR` entry for that position; with the surface present but interp-only, and no
mark, the 8% simply stands. Nothing in the persisted row, the WS payload or the API response
indicates that the IV is a constant.

Measured error, 1M options at a true 12% vol marked at the 8% fallback:

| Position | delta err | vega err | gamma err |
|---|---|---|---|
| ATM | −0.5% | 0.0% | **+50.0%** |
| 4% OTM call | **−61.3%** | **−50.3%** | −25.4% |
| 4% below spot | +9.7% | **−52.9%** | −29.3% |

`3M` also happens to be the default `reference_tenor` for signal-driven structures, so wing
positions from the risk-reversal and butterfly templates are the most exposed.

### Impact

Wing vega understated by half and ATM gamma overstated by half, persisted into
`open_position` / `open_position_history`, then consumed by `/aggregate-greeks`,
`/risk-per-tenor`, `/vega-per-tenor`, `/pnl-attribution` and (via `p.iv`) the entire VaR family.
No flag anywhere.

### Recommended fix (not applied)

- Match on the cell's `source` field, not on `strike`: accept interpolated pillars, and use the
  moneyness→delta-pillar mapping (which does not need a strike) to pick the cell.
- Replace the scalar return with `(iv, source)` and persist an `iv_source` column
  (`listed` / `interp` / `svi_fallback` / `mark_inverted` / `default`).
- Make the constant fallback loud: log at WARNING with the position id and surface a
  `n_positions_on_default_iv` counter on `/aggregate-greeks`.

---

## R3-07 — High — The published portfolio greeks are missing the contract multiplier

**File:** `src/engines/risk/engine.py:540-566`

### Evidence

`_aggregate_greeks` is the only portfolio-level output of the risk engine — it is what goes into
`latest_greeks:portfolio` and the `risk_update` channel behind `/ws/risk`:

```python
totals["delta"] += qty * bs_delta(F, K, T, iv, right)   # no × mult
totals["gamma"] += qty * bs_gamma(F, K, T, iv)          # no × mult, no 1e-4
totals["vega"]  += qty * bs_vega(F, K, T, iv)           # no × mult, no 0.01
totals["theta"] += qty * bs_theta(F, K, T, iv, right)   # no × mult
```

Fifty lines earlier, `_persist_position_snapshots` computes the *same* greeks with
`× mult` and with the display scalings (`×1e-4` for gamma, `×0.01` for vega). Measured for one
10-lot ATM 1M call at 8% vol:

| Greek | `_aggregate_greeks` (WS) | `_persist_position_snapshots` (DB) | ratio |
|---|---|---|---|
| delta | `5.05` | `630,718.53` | 125,000 |
| gamma | `161.05` | `2,013.09` | 12.5 |
| vega | `1.235` | `1,543.94` | 1,250 |

Additionally, the futures branch adds the raw contract count:

```python
if pos.get("instrument_type") == INSTRUMENT_FUTURE:
    totals["delta"] += float(pos.get("quantity", 0))
```

so an **M6E micro (12 500) counts exactly the same as a 6E full (125 000)** — a 10× error inside
the aggregate, and the existing regression test
(`tests/unit/engines/risk/test_aggregate_greeks_futures.py`) locks this in.

Mitigating: `frontend/src/hooks/streams.ts:41` subscribes `/ws/risk` as `useStream<unknown>` and
the comment at `:32-36` confirms the payload is used only as a REST-invalidation trigger. So no
user-facing number is wrong *today* — but the Redis contract documented in `bus/keys.py`
("Portfolio-level aggregates (Risk Engine → FastAPI)") is wrong, and the next consumer to read
it inherits a 125,000× error.

### Recommended fix (not applied)

Delete `_aggregate_greeks` and publish the same numbers `_persist_position_snapshots` writes
(one shared helper returning per-position greeks, summed for the payload), with the multiplier
and the display scalings applied once. Add `n_positions` / `n_priced` to the payload. Update the
futures test to assert `qty × multiplier_for(symbol)`.

---

## R3-08 — High — No limit is enforced; nothing is fail-closed

**Files:** `src/core/risk/greek_limits.py` (all call sites) · `src/api/routers/trade.py`
· `src/api/orchestration/position_monitor.py`

### Evidence

`grep -rn "compute_caps\|GreekCaps" src` returns exactly one production consumer:
`portfolio_panel.py:2232`, inside `GET /portfolio/greek-limits`, which formats the four caps
into a JSON response. There is:

- **no comparison** of any live greek against `delta_cap_usd`, `vega_cap_usd`, `gamma_cap_pip`
  or `cross_budget_usd` anywhere in the codebase;
- **no breach record** — no table, no `TradeEvent` type, no alert rule keyed on a cap;
- **no scenario-grid enforcement** of the cross budget, despite
  `docs/strategy/risk.md:41` stating "cross_usd = beta_cross · L* (vanna+volga, scenario-grid
  enforced)". `/stress-grid` renders a matrix; nothing reads it back.

On the staleness axis, the pre-submit path checks exactly one liveness condition —
`ib_connection_state.is_connected` for live mode (`trade.py:798`). It does **not** check:
- the risk-engine heartbeat or the age of `latest_greeks:portfolio`;
- the age of the vol surface (`revalidation.py:85-89` explicitly documents that the
  `surface_stale` gate was *removed*, and `run_pre_submit_checks` likewise dropped
  `iv_data_fresh`);
- whether the greeks backing the vega gate are older than the position.

There is no kill switch (`grep -i "kill_switch|halt_trading|trading_enabled"` → no hits). The
answer to the brief's question "is a breach fail-closed?" is: there is no breach concept.

### Impact

If the risk engine is stopped, crash-looping, or publishing 8%-vol garbage, `/trade/submit`
behaves identically to a healthy system. The only guardrail is a vega gate reading a value that
is structurally frozen at zero in live mode (R3-03).

### Recommended fix (not applied)

1. Add a `risk_gate` module in `core/` that takes `(live_greeks, caps, freshness)` and returns
   an allow/deny with the breached axis. Call it from `run_pre_submit_checks` **and** from
   `_submit_preview_impl` revalidation.
2. Make it **fail-closed**: deny when `latest_greeks:portfolio` is older than a documented
   `MAX_RISK_STALENESS_S` (the 30 s `TTL_GREEKS` is a natural anchor), or when `n_priced <
   n_open_positions`.
3. Persist breaches as `TradeEvent(event_type="risk_limit_breach")` so post-trade breaches are
   auditable even when no new trade is attempted.
4. Add an operator kill switch (`config_scalar` namespace `risk`, `trading_enabled`) checked in
   the same gate.

---

## R3-09 — High — NaN / Inf from one bad quote poisons the risk channel with invalid JSON

**Files:** `src/engines/risk/engine.py:531-538` · `src/core/pricing/bs.py:15,26,36,44,52`
· `src/bus/publisher.py:64-65`

### Evidence

Two guards fail to stop a NaN:

```python
if isinstance(p, dict) and p.get("iv") and p.get("strike")   # engine.py:533
```
`bool(float("nan")) is True` — a NaN IV passes the truthiness filter and becomes the selected
candidate.

```python
if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:                 # bs.py:26 (all six functions)
    return 0.0
```
`nan <= 0` is `False`, so the guard is bypassed and every greek returns `nan`. Verified:

```
sigma NaN passes guard? sigma<=0 -> False
bs_delta NaN sigma: nan      bs_vega NaN sigma: nan
```

The NaN then reaches the publisher, which uses stdlib defaults:

```python
def _dumps(payload: dict) -> str:
    return json.dumps(payload, default=_json_default)        # allow_nan defaults to True
```

producing `{"greeks": {"delta": NaN, "vega": Infinity}}` — **not valid JSON**. Any strict parser
(browser `JSON.parse`, `serde_json`, Go `encoding/json`) throws on it, so a single bad IV cell
takes down every `/ws/risk` subscriber and every reader of `latest_greeks:portfolio` rather than
degrading one number.

On the REST side the failure is different but also total: Starlette's `JSONResponse` uses
`allow_nan=False`, so a NaN greek raises `ValueError: Out of range float values are not JSON
compliant` and the whole endpoint 500s.

The mirror-image bug is a silent zero: a **negative** sigma (possible from an SVI wing
extrapolation) hits the `sigma <= 0` branch and returns `0.0` — a position with a corrupt IV is
marked as having no risk at all.

### Recommended fix (not applied)

- Validate at the boundary: in `_iv_for`, accept a candidate only if
  `math.isfinite(iv) and 0 < iv < 5`.
- Change the `bs_*` guards to `if not (sigma > 0) or not (T > 0) …` so NaN takes the degenerate
  branch instead of propagating (paired with the QM-01 intrinsic fix so the branch is correct).
- Set `allow_nan=False` in `bus.publisher._dumps` and handle the exception — better a logged
  publish failure than a corrupt channel.
- Sanitise the aggregate before publishing: replace any non-finite total with `None` and add a
  `n_positions_unpriceable` counter.

---

## Medium findings

### R3-10 — `vanna_usd` carries two conventions 100× apart

`engine.py:358` writes `vanna = qty * bs_vanna(...) * mult * 0.01` ("Δ change in $ per 1 vol
pt"), while `core/risk/stress.py:41` uses `_VANNA_SCALE = 1e-4` ("per pip · per vol pt").
Measured on a 10-lot ATM 1M call: `714.79` vs `7.15`. `docs/strategy/risk.md:20` documents the
unit as `USD / vp·fig`, which matches the stress module, not the engine. The same field name
(`vanna_usd`) is served by `/risk-per-tenor` (engine convention) and `/stress-grid?output=vanna`
(stress convention). Volga happens to agree (`(0.01)² == 1e-4`) — by coincidence, not design.
**Fix:** pick one convention, define it in `core/risk/units.py`, and have both call sites import
it; correct the doc unit for volga too (it is `USD/vp²`, documented as `USD/vp`).

### R3-11 — The marginal-VaR `factor` tag is degenerate

```python
cand = {"spot": abs(pos.delta_usd), "level": abs(pos.vega_usd),
        "skew": abs(pos.vanna_usd), "curv": abs(pos.volga_usd)}
meta[...]["factor"] = max(cand, key=lambda k: cand[k])    # portfolio_panel.py:980
```
These four columns are in four unrelated units at wildly different scales. For one 10-lot ATM 1M
call: `spot 630,718 · level 1,544 · skew 715 · curv 0.025`. `"spot"` wins for essentially any
option with a non-trivial delta, and `"curv"` — seven orders of magnitude smaller — can never
win. The Risk-tab column is therefore a constant.
**Fix:** normalise each greek by its factor's 1-day 99% shock (the same
`var_factors.DEFAULT_SHOCKS` used for the per-factor VaR) before comparing — i.e. rank by
scenario P&L contribution, not by raw greek magnitude.

### R3-12 — `/pnl-attribution` mixes decimal and percentage IV in the same endpoint

`_decompose_row` computes `vega_pnl = V × (iv_now − iv_then)` with `V` in `$/vol-point`. For
booked rows the IV column is `iv_avg_legs_pct` (percent) — correct. For IB-live rows it is
`open_position.iv`, which the risk engine writes as a **decimal** (`Numeric(8,5)`, e.g.
`0.08000`), so `iv_now − iv_then` is 100× too small and the vega term is 100× understated. The
missing P&L silently lands in `residual_usd`, which the docs describe as "only genuine
higher-order convexity". **Fix:** multiply by 100 on the IB path, or normalise `open_position.iv`
to percent (with a migration) so one convention holds.

### R3-13 — `delta_usd` is EUR notional, and the cap treats it as USD

`delta_usd = qty · Δ · mult` has units of EUR notional (equivalently `∂PnL/∂F` in USD per unit
of spot), not USD cash-delta — a USD cash-delta needs a further `× spot`. Meanwhile
`compute_caps` inverts a **fractional** spot shock: `delta_usd = β·L*/s`, so the implied stress
loss is `delta × s`, whereas the actual loss for a 2.5% move is `delta × s × spot`. At
spot 1.08 the true loss at the cap is ~8% above the intended budget. `docs/strategy/risk.md:16`
labels the axis "USD cash-delta". **Fix:** either multiply the persisted delta by spot (making it
a genuine cash-delta) or divide the cap by spot, and state the choice in the doc.

### R3-14 — VaR sign convention splits across the Risk tab

`/var` returns `var_99_usd` as a negative number (`_var_stats` reads raw percentiles of a P&L
series); `/marginal-var` (`marginal_var._loss_var` → `max(0.0, -q)`) and `/var-factors` return
positive losses. Both are documented, in different docstrings, and both feed the same tab.
**Fix:** standardise on positive-loss everywhere (the doc's wording) and rename the fields to
`*_loss_usd`.

### R3-15 — Two position books, and the limit reads the wrong one

`open_position` is the IB-mirrored book (written by `position_sync`, greeks written by the risk
engine). `booked_position` is the API's own booking (created by `/trade/submit`, greeks written
by `position_monitor`). The enforced vega limit reads the second; every live greek, VaR and
stress number reads the first. When they diverge — a leg netted flat at IB and auto-closed with
`close_reason='reconciled_flat_at_ib'`, a position opened outside the preview flow, a live fill
whose cascade did not complete — the limit is evaluated against a book that no longer matches
reality, and nothing reconciles the *greeks* between the two (the reconciler only reconciles
existence). **Fix:** designate `open_position` as the single risk source of truth and derive the
pre-trade book greeks from it; keep `booked_position` for trade-lifecycle attribution only.

### R3-16 — The enforced vega limit does not track the derived cap

`config_catalog.py:49` seeds `max_book_vega_usd = 5000` (static). `compute_caps` derives
`vega_usd = β_vega·α·nav_base / (shock_vol·regime_mult)` = `0.00625 · nav_base` at defaults —
$5,075 at the doc's 812k reference NAV, but **$625 at a 100k NAV**. The enforced number never
moves; the displayed cap does. The panel and the gate will disagree by the NAV ratio, and the
gate is the looser of the two on any drawdown. **Fix:** have the pre-submit gate call
`compute_caps` (same params from the `greek_limits` namespace) instead of reading a static
scalar, and retire `max_book_vega_usd`.

### R3-17 — Heartbeat is written before the work, so a permanently-skipping engine looks healthy

```python
while not self._stop.is_set():
    ...
    await publisher.set_heartbeat(self.redis, keys.ENGINE_RISK)   # engine.py:139 — before the cycle
    ...
    with observed_cycle("risk_engine"):
        await self.run_cycle()                                    # may return False immediately
```
`run_cycle` returns `False` without publishing when no spot is available (`:178-179`).
`/health` (`health.py:19,41-46`) treats the heartbeat as *the* authoritative liveness signal, so
the risk engine reports UP indefinitely while `latest_greeks:portfolio` has expired. The dev
dashboard is better — `dev.py:123` pairs the heartbeat with the output key and a 30 s
threshold — but `/health` is what a monitor scrapes.
**Fix:** write the heartbeat only after a successful publish, or publish a
`{"status": "degraded", "reason": …}` payload so the age of the output key is meaningful.

### R3-18 — VaR methodology is under-documented and √t-scaled in the UI

`docs/strategy/risk.md` §VaR (lines 58-68) documents the Euler decomposition and nothing else. It
does not state:
- the **horizon** (1 day), **confidence** (95 / 99 + ES 99) or **return window** of `/var`
  (~1 year of daily closes for historical-simulation; `var_lookback_days`, default 504, for the
  account-history fallback);
- that there are **two methods** with silent fallback between them
  (`_HIST_SIM_MIN_SCENARIOS = 60`, `_VAR_MIN_OBS = 5`, `_VAR_MAX_GAP_DAYS = 3`);
- `var_factors.DEFAULT_SHOCKS = {spot 150 bp, level 1.5 vp, skew 0.6 vp, curv 0.4 vp}` — four
  undocumented policy numbers that are not editable from the settings panel either;
- the **realised-vol Δvol proxy** in `hist_var.py` (honestly explained in the module docstring,
  absent from the strategy doc).

Separately, `RiskView.tsx:156,167` scales the 1-day VaR by `Math.sqrt(r.days)` for multi-day
horizons. √t scaling is defensible for a linear book; on an option book it ignores theta (which
is deterministic and dominates at short DTE) and gamma path-dependence, and it will
systematically *understate* multi-day risk for a short-gamma book.

The revaluation approach itself **does** match the docs: full BS reval via
`core.risk.stress.reval_book`, not delta-gamma. That claim checks out.

**Fix:** extend `docs/strategy/risk.md` with a VaR section stating horizon/confidence/window/
method-selection and the factor shocks; move `DEFAULT_SHOCKS` into `config_scalar`; either drop
the √t scaler or re-derive multi-day VaR by replaying n-day overlapping returns.

### R3-19 — `reval_book` drops options whose shocked IV goes negative

```python
new_iv = _shocked_iv(p, new_spot, dvol_vp, dskew_vp, dfly_vp)
if new_iv <= 0:
    continue                                     # stress.py:111-112
```
A skipped position contributes **zero** P&L to that scenario, so the book looks *less* risky in
exactly the scenarios where the vol shock is largest. In the historical-simulation path the Δvol
proxy is a change in 21-day realised vol, whose tail moves are several vol points, so a low-IV
short-dated position can be knocked out of the worst scenarios.
**Fix:** floor at a small positive vol (e.g. `max(new_iv, 1e-4)`) so the position still
revalues, and count clipped legs in the response.

### R3-20 — Unbounded synchronous reval on a public endpoint

`_hist_sim` runs `n_positions × n_scenarios` full BS revaluations inline on the event loop, and
`/var` and `/marginal-var` each call it independently with no caching (`portfolio_panel.py:2076,
2113, 982`). Measured on this machine:

```
 5 positions × 250 scenarios:   49.7 ms
20 positions × 250 scenarios:  196.9 ms
50 positions × 250 scenarios:  534.2 ms
```

Both endpoints are unauthenticated on the public read-only deployment, and the frontend
re-fetches them on every `/ws/risk` beat. This compounds Wave-1 F-03 (single-container API,
512 MB, restart loop).
**Fix:** memoise `_hist_sim` per `(book_hash, bars_ts)` for the risk cycle duration, and run it
through `asyncio.to_thread` as the vol engine already does for SVI/SSVI.

### R3-21 — `gl.regime_mult` is dead code with a different definition than the one in use

`greek_limits.regime_mult(current_vol, calm_baseline_vol, 1, 3)` implements the spec
(`clamp(current_vol / calm_baseline, 1, 3)`) and has **no production caller**.
`portfolio_panel.py:2217-2231` inlines a different rule: `clamp(vol_levels[-1] /
median(last 90 days), 1, 3)` from `regime_snapshot.vol_level_pct`, guarded by
`len(vol_levels) >= 10`. A rolling 90-day median is not a "calm baseline" — during a sustained
high-vol regime the median rises with the market and `regime_mult` decays back toward 1.0,
loosening the caps exactly when they should stay tight.
**Fix:** delete the dead function or make the router call it; document which baseline is
intended and pin the calm baseline to a stable estimate (e.g. a long-window low quantile).

---

## Low findings

- **R3-22 — Broken doc links.** `greek_limits.py:3`, `portfolio_panel.py:2173` and
  `docs/README.md:43` all cite `docs/strategy/greek-limits.md`, which was renamed to
  `docs/strategy/risk.md` in `6ef53f04`. The `§2 / §6 / §8` anchors those docstrings reference
  have no counterpart in the new file (it has no numbered sections), so the traceability between
  spec and implementation is unresolvable.
- **R3-23 — Greeks-source claim is wrong.** `docs/strategy/risk.md:9-11` says per-leg greeks come
  from `bs_greeks` in `engines/execution/structures.py`; the live book's greeks come from
  `core/pricing/bs.py` (structures.py is only used for the signal→structure preview in
  `cockpit.py:159`). The doc also claims the order builder shows "all six as book *before →
  after* on every ticket" — `NetGreeks` (`structures.py:48-52`) has **four** fields, no vanna or
  volga, `_sum_greeks` applies **no contract multiplier** (so its `$/vol_pt` label is wrong by
  125,000×), and there is no before/after book delta in the preview payload. This is the
  documentation face of Wave-1 A6-02.
- **R3-24 — 4-digit strike regex silently drops positions.**
  `_OPT_RE = r"^EUU([FGHJKMNQUVXZ])(\d) ([CP])(\d{4})$"` (`shared/contracts.py:43`) matches only a
  4-digit strike field. Any finer strike (a 5-digit `localSymbol`) returns `None`, and
  `engine.py:263` does `continue` — the position vanishes from greeks, from the DB snapshot and,
  because `iv` is never written, from `_resolve_book` too. It is dropped with a comment
  ("skip rather than mislead the BS path") but no log line and no counter. Relatedly,
  `build_ib_local_symbol` uses `int(strike*1000):04d`, which truncates a 1.0625 strike to `1062`.
- **R3-25 — "All eight policy values"** (`docs/strategy/risk.md:54`) vs nine keys in
  `CONFIG_DEFAULTS`.
- **R3-26 — `diversification_pct` can go negative** when `VaR_p > Σ standalone` (possible with a
  concentrated book and few scenarios); `marginal_var.py:60` neither clamps nor labels it, and
  the UI presents it as a percentage.
- **R3-27 — Sentinel-value IV comparison.** `if iv == FALLBACK_IV or iv is None:`
  (`engine.py:340`) uses the float `0.08` as a "not found" marker, so a genuine 8.000% surface IV
  triggers a needless `bs_implied_vol` inversion of the mark — and, conversely, a mark-inverted
  IV that lands on 0.08 is indistinguishable from the default. Return an explicit
  `(iv, source)` pair instead.

---

## Appendix A — Spec-vs-code limits matrix

Source of truth: `docs/strategy/risk.md` (the file `greek_limits.py` still calls
`greek-limits.md`, see R3-22). "Enforced" means *some code path blocks, alerts on, or otherwise
acts on* a breach.

### A.1 — The four derived caps

| # | Spec statement (`docs/strategy/risk.md`) | Code | Computed? | **Enforced?** | Verdict |
|---|---|---|---|---|---|
| 1 | `L* = alpha × nav_base` | `greek_limits.py:99` | ✅ exact | — | ✅ match |
| 2 | `delta_usd = beta_delta · L* / shock_spot` | `greek_limits.py:101` | ✅ exact | ❌ **never** | ⚠️ R3-08; units mismatch R3-13 |
| 3 | `vega_usd = beta_vega · L* / shock_vol` | `greek_limits.py:102` | ✅ exact | ⚠️ *a different, static limit is* (`max_book_vega_usd = 5000`) | ⚠️ R3-03, R3-16 |
| 4 | `gamma_pip = 2·beta_gamma·L* / (shock_spot²·spot·1e4)` | `greek_limits.py:103` | ✅ exact (inversion verified) | ❌ **never** | ⚠️ R3-08 |
| 5 | `cross_usd = beta_cross · L*` — "vanna+volga, **scenario-grid enforced**" | `greek_limits.py:104` | ✅ exact | ❌ **never** — `/stress-grid` renders, nothing reads back | ❌ spec claims enforcement that does not exist |

### A.2 — Policy parameters

| Spec | Default in spec | Code default | Editable via settings? | Verdict |
|---|---|---|---|---|
| `alpha` | 0.05 | `ALPHA = 0.05` | ✅ `greek_limits` namespace | ✅ |
| `beta_delta` | 0.15 | 0.15 | ✅ | ✅ |
| `beta_vega` | 0.50 | 0.50 | ✅ | ✅ |
| `beta_gamma` | 0.25 | 0.25 | ✅ | ✅ |
| `beta_cross` | 0.10 | 0.10 | ✅ | ✅ |
| `shock_spot` | 2.5% | `SHOCK_SPOT = 0.025` | ✅ | ✅ |
| `shock_vol` | 4 vol pts | `SHOCK_VOL = 4.0` | ✅ | ✅ |
| `nav_hwm_floor` | 0.9 (implied by "hwm × 0.9") | 0.9 | ✅ | ✅ |
| `nav_halflife_days` | 20d | 20.0 | ✅ | ✅ |
| — | "**All eight** policy values" | **nine** keys | — | ⚠️ R3-25 |
| `var_factors` shocks | *not in spec* | `{spot 150bp, level 1.5, skew 0.6, curv 0.4}` | ❌ hardcoded | ⚠️ R3-18 |

### A.3 — Anti-procyclicality guards

| Spec | Code | Verdict |
|---|---|---|
| `nav_base = max(hwm × 0.9, ewma(nav, halflife=20d))` | `greek_limits.nav_base` — exact; fed by 504 d of daily `account_history.net_liq_usd` | ✅ |
| `regime_mult = clamp(current_vol / calm_baseline, 1, 3)` | `greek_limits.regime_mult` implements it but is **dead**; the router inlines `clamp(vol_level_pct[-1] / median(90d), 1, 3)` | ⚠️ R3-21 |

### A.4 — Greek definitions and units

| Greek | Spec unit | Engine (`open_position.*_usd`) | Stress module | Verdict |
|---|---|---|---|---|
| Δ | "USD cash-delta" | `qty·Δ·mult` → **EUR notional** | `qty·Δ·mult` (same) | ⚠️ R3-13 |
| Γ | USD / pip | `qty·Γ·mult·1e-4` ✅ | `×1e-4` ✅ | ✅ consistent |
| V | USD / vol point | `qty·V·mult·0.01` ✅ | `×0.01` ✅ | ✅ consistent |
| Θ | USD / day | `qty·Θ·mult` ✅ (`bs_theta` is already per-day) | same ✅ | ✅ consistent |
| vanna | USD / vp·fig | `×0.01` (per vp, per unit spot) | `×1e-4` (per vp·fig) | ❌ **100× apart** — R3-10 |
| volga | USD / vp | `×(0.01)² = 1e-4` | `×1e-4` | ✅ values agree; **spec unit is wrong** (should be USD/vp²) |
| — | *published portfolio totals* | `_aggregate_greeks`: **no multiplier, no scalings** | — | ❌ R3-07 |

### A.5 — Other spec claims

| Spec statement | Reality | Verdict |
|---|---|---|
| "Per-leg greeks come from Black-76 (`bs_greeks` in `structures.py`)" | live book uses `core/pricing/bs.py`; `structures.py` only serves the cockpit signal preview | ❌ R3-23 |
| "the order builder surfaces all six as book *before → after* on every ticket" | `NetGreeks` has 4 fields, no vanna/volga, no multiplier, no before/after | ❌ R3-23 |
| "`comp_i = VaR_p · cov(pnl_i, pnl_p)/var(pnl_p)` … sums to `VaR_p`" | verified numerically: Σ components `8934.27` vs `VaR_p 8934.28` | ✅ |
| "`diversification = 1 − VaR_p / Σ standalone`" | `marginal_var.py:60` exact | ✅ (can go negative, R3-26) |
| P&L attribution anchored on t-1 greeks, 5 terms + residual | `_decompose_row` exact; gamma scaling `1/PIP_SIZE` verified dimensionally correct | ✅ except the IV-unit bug R3-12 |
| "full BS reval, not a greek heuristic" (`var_factors`, `hist_var`) | `reval_book` does full BS reval | ✅ |
| Delta hedge: `|delta| > 0.05`, cooldown 300 s, `round(|delta|)`, opposite side | `check_delta_hedge_needed` exact | ✅ *pure function correct* — but it is fed a hardcoded `0.0` (R3-02) |
| `DeltaHedgeConfig` modes `static / threshold / scheduled` | present in `core/config/vol_params.py` | not re-verified (out of aggregation scope) |

---

## Appendix B — Needs live verification by Valérian

Static analysis cannot settle these; each needs the running stack (or a paper trade). Ordered by
what they unblock.

1. **R3-01 / expiry day.** On the next EUU expiry date, query
   `SELECT id, structure, expiry, iv, delta_usd, vega_usd FROM open_position WHERE expiry = CURRENT_DATE;`
   Expect `iv` and all greek columns NULL. Then hit `GET /api/v1/portfolio/var` and confirm
   `n_positions` is short by that position's count.
2. **R3-02 / delta hedge.** `SELECT COUNT(*) FROM hedge_order;` and
   `SELECT DISTINCT current_delta_unhedged FROM booked_position_metric_history ORDER BY 1 LIMIT 5;`
   Prediction: zero hedge rows ever created by the monitor, and the delta column is uniformly
   `0.0000`. Also check the api logs for `position_monitor` and confirm no
   `hedge_persisted=1` line exists.
3. **R3-03 / book vega.** After a **live** (not mock) fill:
   `SELECT total_vega_usd, timestamp FROM book_state_snapshots WHERE is_current;`
   Prediction: unchanged from bootstrap (`0.0`), timestamp not advanced.
4. **R3-06 / fallback IV coverage.** With the vol engine warm, dump the surface and count
   interp pillars: for each display pillar in `latest_vol_surface:EURUSD`, check
   `surface[tenor][label]["source"]`. Then
   `SELECT tenor, COUNT(*) FILTER (WHERE iv = 0.08000) AS on_default, COUNT(*) FROM open_position GROUP BY tenor;`
   Prediction: every position whose tenor maps to an `interp` pillar sits at exactly `0.08000`.
5. **R3-04 / fabricated spot.** With **no** future position open (or with the future's
   `market_price` NULL), call `GET /api/v1/portfolio/var-factors` and compare `current_spot`
   against the live `latest_spot:EURUSD`. Prediction: it equals an option strike.
6. **R3-07 / published greeks.** `redis-cli GET latest_greeks:portfolio` and compare
   `greeks.delta` against `GET /api/v1/portfolio/aggregate-greeks → total_delta_usd`.
   Prediction: a ratio of ~125,000. (Read-only Redis command; no secret is printed.)
7. **R3-09 / NaN.** Cannot be triggered safely against production. Suggest a targeted unit test
   instead: feed `_iv_for` a surface cell with `iv = float("nan")` and assert the published
   payload is valid JSON.
8. **R3-17 / heartbeat.** Stop `market-data` (leave `risk-engine` up) so `latest_spot` expires,
   then check `GET /api/v1/health` after 60 s. Prediction: `risk_engine` still reports UP while
   `latest_greeks:portfolio` has expired.
9. **R3-20 / latency.** Time `GET /api/v1/portfolio/var` and `/marginal-var` with a realistic
   book and confirm the p95 against the 250-scenario cost measured here (~0.5 s at 50 positions).
10. **R3-15 / book divergence.** Compare
    `SELECT COUNT(*) FROM open_position;` against
    `SELECT COUNT(*) FROM booked_position WHERE state='open';` and reconcile the vega totals
    (`SUM(open_position.vega_usd)` vs `SUM(booked_position.entry_vega_usd_per_volpt)`).

**Not run, per the audit's hard rules:** no stack start, no IB connection, no live URL fetch, no
`scripts/local|ops|aws` execution, no environment variables printed, no `.env` read. No secret
appeared in any tool output during this audit.

---

## Appendix C — Cross-audit notes

- **Wave-1 convergence confirmed and extended.** Both Blockers here are *consequences* of QM-01
  and QM-02, and R3-10 / R3-23 are the risk-side face of A6-02. Consolidating pricing into a
  single `core/pricing` implementation with one unit convention would close R3-01, R3-02, R3-10,
  R3-23 and R3-27 at once. This raises the priority of the Wave-1 recommendation from
  "fixes the class" to "unblocks the risk engine".
- **F-03 interaction.** R3-20 gives an unauthenticated caller a ~0.5 s CPU-bound handler on the
  same 512 MB api container that F-03 can already OOM.
- **Feeds Audit 4 (Execution).** R3-02 (hedge dead) and R3-03 (limit not enforced on the live
  path) both live on the submit/hedge boundary and should be re-checked from the execution side.
- **Feeds Audit 7 (Coverage).** `tests/unit/engines/risk/` contains two files; there is **no test
  at all** for `_persist_position_snapshots`, `_iv_for`, `_resolve_book` or the expiry-day path,
  and the one aggregation test locks in the missing-multiplier behaviour (R3-07).
- **Feeds Audit 9 (Frontend).** R3-14 (sign split) and the √t scaler in R3-18 are contract-level
  here; their display consequences belong to Audit 9.
