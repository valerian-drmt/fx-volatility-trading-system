# Wave 2 — Consolidated Findings (2026-08-01)

Audits run: **3 Risk**, **4 Execution**, **5 Data integrity**, **9 Frontend**.
Findings-only: no source modified, no git state changed, no live stack or IB contact.

## Totals

| Audit | Blocker | High | Medium | Low |
|---|---|---|---|---|
| 3 — Risk engine | 2 | 7 | 12 | 6 |
| 4 — Execution | 1 | 11 | 13 | 4 |
| 5 — Data integrity | 4 | 13 | 16 | 5 |
| 9 — Frontend | 3 | 5 | 10 | 8 |
| **Wave 2** | **10** | **36** | **51** | **23** |
| Wave 1 | 0 | 11 | 31 | 22 |
| **Campaign** | **10** | **47** | **82** | **45** |

## The three systemic themes

Wave 2's 10 Blockers are not 10 independent defects. They cluster into three root causes,
and the cluster boundaries matter more than the individual findings for fix sequencing.

### Theme A — duplicated/degenerate pricing (root cause of 6 findings)

Wave 1 identified three divergent Black-Scholes implementations (A6-02) and a `T<=0`
branch returning 0.0 instead of intrinsic (QM-01), plus a dead `interpolate_iv` from
schema drift (QM-02). Wave 2 found where those land:

- **R3-01** (Blocker) — on expiry date, greeks *and* `iv` write NULL; NULL `iv` is the
  drop-condition in `_resolve_book`, so the position vanishes from aggregate greeks, VaR,
  marginal-VaR, stress grid, ladder and vega-PCA simultaneously. **$1.25M of delta → NULL
  overnight.**
- **R3-02** (Blocker) — QM-02 forces the fallback branch that hardcodes
  `current_delta_unhedged = 0.0`: **the automated delta hedge can never fire**, and it
  fails open (missing vol reads as "book is flat").
- **DI-01** (Blocker) — `position_monitor` reads a phantom `spot` key from
  `latest_vol_surface`; always `None`, so every 60s MTM re-prices at entry spot or a
  hardcoded **1.085**, persisted into `booked_position_metric_history.spot`. Direct QM-02
  sibling, same call path.
- Plus R3-10, R3-23, R3-27.

**Consolidating pricing into one `core/pricing` implementation closes all of these.**

### Theme B — nothing bounds exposure (fail-open on every axis)

Three independent audits converged here:

- **R3-08** — no greek limit is enforced anywhere; `compute_caps` has one consumer, a
  display endpoint. No breach concept, no staleness gate.
- **EX-08** — the risk gate at submit fails open: 2 checks hardcoded to pass, 2 deleted,
  the one real gate reads a book snapshot with no freshness check (absent ⇒ all-zeros book).
- **EX-09** — no kill switch exists anywhere (zero hits in code, compose, frontend, docs).
- **EX-10** — no price collar, no notional cap; limit taken from IB's raw touch under
  delayed-**frozen** market data.
- **R3-03** — `refresh_book_state` runs only on the mock path, so live `total_vega_usd`
  stays 0.0 forever and the only enforced check degenerates to per-trade with no memory.

Individually these are Highs. Together they mean **no structural ceiling on what a
malfunctioning submit path can do.**

### Theme C — stale data presented as live

- **FE-01/FE-02/FE-03** (3 Blockers) — the backend sends real data timestamps; the
  frontend discards them and re-stamps with `Date.now()`; freshness is computed once at
  fetch and never re-evaluated; and "Market open" is asserted from a never-expiring
  `latest` dict republished at 5 msg/s. **Every night and weekend the desk shows a green
  dot, "Market open", and Friday's close as a live 5-decimal mid.** IB data is
  `reqMarketDataType(3)` (~20 min delayed) — disclosed nowhere.
- **DI-14** — no staleness field on any vol read path; `timestamp` silently means either
  "live" or "days old".
- **FE-04** — the WS hook tracks `connecting|open|closed` correctly with backoff, but
  `streams.ts` throws the status away; connection state renders in none of the 7 views.

## Other Blockers (outside the three themes)

- **EX-01** — 10s HTTP budget on a close handler that qualifies, snapshots quotes (3s),
  places the order **and** runs a full `sync_positions_from_ib` before replying. Timeout
  stamps the order `rejected` — a state the close-stacking guard doesn't count — so the
  retry places **a second live close of the same lots**. The 45s reasoning is documented
  at `trade.py:227-232` and was never applied here.
- **DI-02** — db-writer idempotency is nominal: 4 of 5 `IDEMPOTENT_TABLES` key
  `ON CONFLICT` on a producer-side `datetime.now()` (can only collide on byte-identical
  redelivery); the 5th documents a deterministic hash-PK that no longer exists.
- **DI-03** — closing a position `DELETE`s `open_position`, and cascade wipes its entire
  `open_position_history` greeks/PnL series (~300k rows/week/position). The code comment
  says the trail survives "in snapshots" — that *is* the cascaded table.
- **DI-35** — `pca_signal_id` FKs pruned rows with no `ondelete`; one 90-day-old
  referenced signal raises, rolls back the *shared* session, and a bare `except` swallows
  it ⇒ both prune jobs stop **permanently and silently**.

## Contract table result (Audit 5, Appendix A)

20 Redis keys + 10 channels mapped producer→consumer→payload→TTL→timestamp.
**4 hard schema mismatches** (`latest_vol_surface` phantom `spot`; `config:changed`
version ignored; `positions` fabricated spot; `db_events` unschema'd) and **9 orphans**
(`publish_account` has zero call sites; `CH_SYSTEM_ALERTS` subscribed but never
published; `market_status` neither). Five live keys absent from `bus/keys.py`.

## Recommended fix sequencing

1. **Theme B first** — it is the only theme where the failure mode is *unbounded action*
   rather than wrong numbers. Kill switch, notional/collar caps, fail-closed risk gate,
   EX-01's timeout budget.
2. **Theme A second** — one pricing consolidation closes 6 findings including 3 Blockers.
3. **Theme C third** — largely mechanical (thread the timestamps the backend already
   sends; render the WS status the hook already tracks).
4. **DI-02/DI-03/DI-35** — data-loss and silent-stall bugs; independent of the themes.

## Live verification still owed

Audits 3/4/5/9 each produced a "needs live verification" checklist (10 + 13 + 13 + 10
drills) with exact commands — autogenerate diff, kill-db-writer-mid-batch, poison-message,
DI-35's blocking-FK query, WS disconnect drills. **None were run.** They need the local
stack and are Valérian's to execute.

## Next

Wave 3 (7 Coverage, 8 Infra/ops, 11 Docs) is designed to run *after* fixes land.
Given 10 Blockers, fixing before Wave 3 is the sequencing the plan intends.
