# Audit 7 — Test Coverage — Findings (2026-08-01)

**Scope:** `tests/unit`, `tests/integration`, `tests/fixtures`, `frontend/src/**/__tests__`,
`frontend/e2e`, CI wiring in `.github/workflows/ci.yml`.
**Mode:** findings-only. No source modified, no test added, no git state changed, no gated
integration suite run, no docker, no IB contact.

**Ran:** `python -m pytest` (794 passed / 15 skipped / 8–12 s, 3 consecutive randomised-free
runs, all green), `pytest --cov=src` with a JSON report re-aggregated per package,
`npm run test:coverage` in `frontend/`.

---

## Executive summary

This audit was designed for Wave 3 — after the Wave 1–2 fixes land. Run instead on the
**pre-fix** state, it answers a sharper question: the campaign found **10 Blockers and 47
Highs**, and CI was **green the whole time**. Where is the suite blind?

The answer is not "coverage is low". Backend line coverage is **58.1%** and the suite is fast,
deterministic and, in `core/`, genuinely good. The answer is structural:

> **8 of the 10 Blockers have zero executable coverage of the defective line.** The other 2
> (FE-01, FE-02) *are* executed by passing tests that assert a different property.
> And **7 tests actively assert defective behaviour as the intended contract** — those tests
> will go red when the Blockers are fixed, and a maintainer who does not know why will
> "fix the test" and re-introduce the bug.

Six systemic blind spots explain essentially the whole campaign:

| # | Blind spot | Findings it hides |
|---|---|---|
| **B1** | **Pure functions are tested; the adapters that call them are not.** `core/` = **94.8%**, `engines/` = **42.2%**, `api/routers/` = **36.0%**. Every Wave-2 Blocker lives in the wiring, not the maths. | R3-01, R3-02, DI-01, DI-03, EX-01, FE-03 |
| **B2** | **Seven tests pin the defect as the contract.** `test_degenerate_inputs_return_zero`, `test_on_order_status_marks_rejected`, `test_does_not_block_on_stale_surface`, … | QM-01, QM-14, EX-05, EX-08, EX-12, EX-10, R3-19 |
| **B3** | **Mocks are richer than reality.** The fill mock supplies a populated `CommissionReport` that real `ib_insync` never has at `fillEvent` time; the surface fixtures use the **retired flat schema** that no producer emits. | EX-03, QM-02, DI-01, FE-13 |
| **B4** | **Every DB test runs on in-memory SQLite with foreign keys OFF.** No `PRAGMA foreign_keys=ON` anywhere. Cascades, `ON CONFLICT`, `CHECK`, and FK-RESTRICT are all inert. | DI-02, DI-03, DI-09, DI-10, DI-12, DI-23, DI-35 |
| **B5** | **No clock is ever frozen or advanced.** No `freezegun`, no `time-machine`, no `vi.useFakeTimers()`. Every "staleness" test injects `now` as a parameter into a pure comparator; nothing ever asserts that *elapsed real time* changes a rendered state. | FE-01, FE-02, FE-03, DI-14, DI-15, EX-14 |
| **B6** | **No test ever crosses a process boundary.** The compose-based e2e job boots the full stack for 20 min and then runs a spec whose `beforeEach` `page.route`-intercepts every `/api/v1/*` call. It asserts nginx serves `index.html`. | every contract mismatch in Audit 5 Appendix A |

Two CI facts compound this:

- **The backend has no coverage floor at all.** `ci.yml:66` runs `--cov=src --cov-report=term`
  with no `--cov-fail-under`. Backend coverage can go to zero and CI stays green.
- **`CLAUDE.md:215` claims the frontend gate is "≥ 70% coverage". It is `lines: 15`**
  (`frontend/vitest.config.ts:37`), and the measured value is **17.36%**. The stated gate is
  off by a factor of four from the enforced one.

The 07 exit criterion — *"every Blocker/High-risk path from audits 2–5 has at least one real
test"* — is **not met**, on 8 of 10 Blockers and 39 of 47 Highs.

---

## Part 1 — The "why didn't tests catch it" matrix

Method: for each finding, the exact source lines named in the Wave 1–2 report were looked up
in the `coverage.py` JSON (`executed_lines` vs `missing_lines`) from the green unit run, then
the nearest test file was read.

Legend for **Coverage of the defect**:
- **ZERO** — the defective statements are in `missing_lines`; no test executes them.
- **IMPORT-ONLY** — the line is a module-level constant executed by `import`; the behaviour is never run.
- **EXECUTED** — a passing test runs the line but asserts something else (or asserts the defect).

### 1a. The 10 Blockers

---

#### **QM-01 → R3-01 → DI-01 cluster: expiry-day $0 pricing**

**R3-01 (Blocker)** — `src/engines/risk/engine.py:273,338,384,397` — on expiry date, greeks
*and* `iv` write NULL; NULL `iv` is the drop-condition in `_resolve_book`, so the position
vanishes from aggregate greeks, VaR, marginal-VaR, stress, ladder and vega-PCA at once.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `engines/risk/engine.py:265-400` = 81 statements, **1 executed**. Whole module 22.1%. |
| **Tests touching this code** | `tests/unit/engines/risk/` contains exactly **two** files and **four** tests: `test_aggregate_greeks_futures.py` (3 tests on futures delta sign) and `test_engine_helpers.py` (1 parametrised test on `_days_to_tenor_bucket`). Neither imports the cycle. |
| **What they assert instead** | That a long future contributes `+qty` delta and that 45 days maps to the `"2M"` bucket. |
| **The upstream root cause is *pinned* by a test** | `tests/unit/core/pricing/test_bs.py:57-61`, `test_degenerate_inputs_return_zero`: `assert bs.bs_price(1.0, 1.0, 0.0, 0.1, "C") == 0.0`. This is QM-01 written down as the intended contract. A correct intrinsic-value fix makes this test **fail**. The vectorised twin `tests/unit/core/risk/test_greeks_vec.py:27` (`test_bs_price_vec_degenerate_returns_zero_vector`) pins QM-14 the same way. |
| **Why no test could have failed** | Grep across all of `tests/unit` for a position priced on its expiry day returns **nothing**. Every `LegSpec` in `test_position_pricing.py` uses `now=2026-05-04`, `expiry=2026-08-04` — T is always ≫ 0. The only `T=0.0` call sites in the entire suite are the two assertions that 0.0 is *correct*. |
| **Test that would have caught it** | A pricing property test: for a deep-ITM call with `T→0`, `bs_price → max(F-K,0)` and `bs_delta → 1.0` (continuity from the right at `T=1e-9`). Plus an engine-level test: build a one-option book expiring today, run one risk cycle, assert `iv IS NOT NULL` and that `n_positions` in the resulting book equals the input count. |

---

**R3-02 (Blocker)** — `src/api/orchestration/position_monitor.py:208,247` —
`current_delta_unhedged` hardcoded to `0.0` on the degraded-reprice branch (always, per QM-02)
⇒ the automated delta hedge can never fire, and fails **open**.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `position_monitor.py:200-255` = 17 statements, **0 executed**. |
| **Tests touching this code** | `tests/unit/core/positions/test_delta_hedge.py` — 9 tests, all excellent, all on the **pure decision function** `check_delta_hedge_needed(delta_unhedged=…)`. `tests/unit/api/orchestration/test_position_monitor_regime.py` covers only the regime-tagging helper. |
| **What they assert instead** | That `check_delta_hedge_needed(-1.4, 0.05)` returns `BUY 1`, that a cooldown blocks, that `0.07` rounds to zero contracts. All true. All irrelevant: the caller never passes a non-zero value. |
| **This is blind spot B1 in its purest form** | The function is 100% covered and 100% correct. The 4 lines that decide *what number to hand it* are 0% covered. |
| **Test that would have caught it** | A monitor-level test: seed one long-call position, a live surface, and a spot; run `monitor_position()`; assert the `delta_unhedged` passed to `check_delta_hedge_needed` equals the recomputed leg delta and is **non-zero**. A second test asserting that when the surface read fails, the monitor raises/skips rather than substituting `0.0` (fail-closed). |

---

**DI-01 (Blocker)** — `src/api/orchestration/position_monitor.py:614` — `payload.get("spot")`
on a payload that has no `spot` key ⇒ live spot is always `None`; MTM re-prices at entry spot
or a hardcoded `1.085`, persisted into `booked_position_metric_history.spot`.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `position_monitor.py:605-620` = 13 statements, **0 executed**. |
| **Tests touching this code** | None. There is no `tests/unit/api/orchestration/test_position_monitor.py`. |
| **The sibling that *is* tested, wrongly** | `QM-02`'s `interpolate_iv` (`bs.py:116-144`) is 16/18 covered — by `test_bs.py:64-75`, `test_interpolate_iv_returns_closest_strike`, whose fixture is `{"1M": {"strike_atm": …, "sigma_ATM_pct": …, "strike_25dc": …}}` — **the retired flat pillar schema**. The live producer emits the nested `{"1M": {"25dc": {"iv": …}}}` shape. The test passes on data no producer has produced since the schema change. Blind spot B3. |
| **Same fixture bug, second site** | `tests/unit/core/positions/test_position_pricing.py:11`, helper `_flat_surface()` — same retired schema, 5 tests built on it. |
| **Test that would have caught it** | A **contract test** that asserts the Redis payload shape at both ends: take the exact dict `engines/vol/engine.py` publishes to `latest_vol_surface:EURUSD`, feed it to `interpolate_iv` and to `position_monitor._reprice`, assert a non-`None` IV and a non-`None` spot. Generalised: a shared `tests/fixtures/` module holding **one** canonical surface payload, imported by every test that needs a surface — so a schema change breaks 30 tests at once instead of silently zero. |

---

#### **EX-01 (Blocker) — timed-out close double-closes**

`src/api/routers/positions.py:1246-1258` + `src/engines/execution/main.py:439-476`.
10 s client timeout on a handler that qualifies, snapshots quotes (3 s), places the order
**and** runs a full `sync_positions_from_ib`; timeout stamps the order `rejected` — a state
the close-stacking guard does not count — so the retry places a second live close.

| | |
|---|---|
| **Coverage of the defect** | **ZERO on both halves.** `positions.py:1240-1262` = 20 statements, **0 executed**. `execution/main.py:435-480` = 20 statements, **2 executed** (module 42%). |
| **Tests touching this code** | `tests/unit/api/routers/test_close_stacking_guard.py` — 4 tests, all on the **pure arithmetic helper** `close_inflight_remaining(qty, qty_filled, submitted_at, state_updated_at, now, window)`. |
| **What they assert instead** | That a fresh dispatched close counts 8 remaining, that a 10-minute-old one self-heals to 0, that `submitted_at IS NULL` counts only inside the window. Correct — and it never sees an order in state `rejected`, because the helper takes no state argument at all. The guard's *state filter* lives in the uncovered SQL at `positions.py:1106-1133`. |
| **Also uncovered** | The 10 s `httpx` timeout constant is in the 0-executed block. Nothing asserts the client budget exceeds the server's worst-case work. |
| **Test that would have caught it** | Two tests. (1) A guard test that seeds a close order in each terminal-ish state (`rejected`, `cancelled`, `submitted`, `partially_filled`) and asserts which ones block a retry — `rejected` must block until reconciled. (2) A timeout-budget test: assert `CLOSE_TIMEOUT_S` ≥ the documented 45 s reasoning at `trade.py:227-232`, or better, an executor test that stubs the engine to sleep past the budget and asserts the api records `unknown`/`pending_reconcile`, never `rejected`. |

---

#### **DI-02 (Blocker) — nominal idempotency**

`src/persistence/writer.py:80-86` — 4 of 5 `IDEMPOTENT_TABLES` key `ON CONFLICT` on a
producer-side `datetime.now()`; the 5th documents a hash-PK that no longer exists.

| | |
|---|---|
| **Coverage of the defect** | **IMPORT-ONLY.** Lines 80-86 are the `IDEMPOTENT_TABLES` dict literal — executed by `import`, which is why the file reports 22.5%. The behaviour is dead: `_execute_batch` (261-290), the retry path (234-259), the flush loop (166-192) and the enqueue path (115-138) are **all 0 executed**. |
| **Tests touching this code** | **There is no `tests/unit/persistence/test_writer.py`.** The only file under `tests/unit/persistence/` is `test_migration_032_backfill.py`. `AsyncDatabaseWriter` — the single component that decides what reaches Postgres — has **zero unit tests**. |
| **Why the 83.3% on `src/persistence` is a mirage** | `models.py` contributes 490 of 672 statements at 100%, purely because `import persistence.models` executes 28 class bodies. Excluding it, `src/persistence` is **38.5%**. The same inflation applies to `api/schemas` (112 statements, 100%, declaration-only). |
| **Why SQLite makes it unfixable in place** | Even if a writer test existed, blind spot **B4** would defeat it: every DB unit test builds `create_async_engine("sqlite+aiosqlite:///:memory:")`, and PostgreSQL's `insert(...).on_conflict_do_nothing(index_elements=…)` has different semantics (and a different dialect) there. |
| **Test that would have caught it** | A `db_integration` test against the CI Postgres already running in the `live-integration` job: publish the same logical row twice with two different producer `datetime.now()` values, assert exactly one row lands. It fails today for 4 of 5 tables. Plus a static test that every entry in `IDEMPOTENT_TABLES` names a column set backed by an actual `UniqueConstraint` in `models.py` — that one test catches DI-02, DI-09 and DI-12 together. |

---

#### **DI-03 (Blocker) — cascade wipes the greeks history**

`src/engines/execution/position_sync.py:209` + `models.py:106-108,122-124` — closing a
position `DELETE`s `open_position`, cascade-wiping its entire `open_position_history` series.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `position_sync.py:195-225` = 13 statements, **0 executed**. Whole module **11.0%** — the lowest-covered non-trivial file in the repo, and it is the one that mutates the position book from IB. |
| **Tests touching this code** | `tests/unit/engines/execution/test_position_sync_reconcile.py` covers the reconcile-diff helper only. |
| **Why a test could not have caught it even if it existed** | Blind spot **B4**. SQLite defaults `PRAGMA foreign_keys` to **OFF**, and no test enables it (grep for `PRAGMA` across `tests/` returns one hit, inside a stale `.pyc` from a deleted file). `ON DELETE CASCADE` is therefore **inert in every unit test in this repo**. A test that deleted the parent and asserted the children survived would have passed — on SQLite — while production wiped ~300k rows/week/position. |
| **Test that would have caught it** | A `db_integration` test on real Postgres: insert a position + 3 history rows, close the position through `position_sync`, assert `SELECT count(*) FROM open_position_history WHERE position_id = …` is still 3. Prerequisite fix to the harness: add a shared `conftest.py` fixture that turns FK enforcement on for SQLite *and* provides a Postgres-backed variant for anything that touches `ondelete`. |

---

#### **DI-35 (Blocker) — silent permanent prune stall**

`src/engines/vol/engine.py:653-661` — `pca_signal_id` FK with no `ondelete`; one 90-day-old
referenced signal raises, rolls back the *shared* session, and a bare `except` swallows it ⇒
both prune jobs stop permanently and silently.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `vol/engine.py:640-670` = 11 statements, **1 executed**. Whole module **24.0%** (534 of 703 statements missing). |
| **Tests touching this code** | Three files under `tests/unit/engines/vol/` (`test_analytics_read_path`, `test_engine_reconnect`, `test_fair_vol_attach`). None touches the pruner. |
| **Three independent reasons no test fails** | (1) the lines are never executed; (2) FK RESTRICT is inert on SQLite (B4); (3) the bug's signature is *absence of an exception* — a bare `except` swallowing a rollback is invisible to any test that does not assert on emitted logs or on a metric. |
| **Test that would have caught it** | A pruner unit test with an injected session that raises `IntegrityError` on the first `DELETE`, asserting (a) the second `DELETE` still runs in its own transaction, and (b) an `ERROR`-level log is emitted (via `caplog`). Plus a repo-wide static test: every `except Exception:` in a scheduled/background loop must log at `ERROR` — that single test would also catch EX-28. |

---

#### **FE-01 / FE-02 / FE-03 (3 Blockers) — stale data shown as live**

These are the two Blockers whose defective lines **are** executed by passing tests.

**FE-01** — `voldesk/data/live/surface.ts:61` + `hooks/useFetch.ts:42` — markets-closed frozen
IV grid badged `live`; backend `timestamp` discarded.
**FE-02** — `useFetch.ts:42` + `data/freshness.ts:31` — `Fresh.status` computed once, never ages.

| | |
|---|---|
| **Coverage of the defect** | **EXECUTED.** `useFetch.ts` = **100% lines**, `freshness.ts` = **100%**, `surface.ts` = **100%**. Three of the best-covered files in the frontend contain a Blocker each. |
| **Tests touching this code** | `frontend/src/hooks/__tests__/useFetch.test.tsx` (2 tests) and the `describe("freshness contract")` block in `voldesk/data/__tests__/foundation.test.tsx` (3 tests). |
| **What they assert instead** | `useFetch.test.tsx` asserts *error* semantics: a rejected reload keeps the last value and flips status to `"stale"`, a never-successful fetch reports `"missing"`. Both true. Neither concerns **age**. The freshness tests call the pure comparator with an **injected `now`**: `statusFor(now - 2000, 1000, now)` — proving the arithmetic, never the plumbing. |
| **The exact line nobody asserts on** | `useFetch.ts:42`: `setState(makeFresh<T>(d, Date.now(), warnMs))`. The `asOf` is the browser's **fetch wall-clock**, not the backend's `timestamp` field — so a payload the backend stamped three days ago is `asOf = now` and renders `live`. And because it is written into React state once, no re-render ever re-evaluates it (blind spot **B5**: there is no `vi.useFakeTimers()` anywhere in the frontend). |
| **The MSW fixtures prove the point** | Every handler in `src/tests/mocks/handlers.ts` returns a hardcoded past timestamp — `"2026-04-20T12:00:00Z"`, `"2026-06-16T00:00:00Z"`. Under a *correct* freshness implementation every panel in every test would render `stale`. The suite is green precisely because the timestamp is thrown away. |
| **Tests that would have caught them** | (1) `useFetch` must derive `asOf` from a caller-supplied extractor over the payload, and a test must assert that a fetcher returning `{timestamp: "<3 days ago>"}` yields `status === "stale"` **on first render**. (2) A fake-timer test: render, assert `live`, `vi.advanceTimersByTime(warnMs + 1)`, assert the component re-renders `stale` without a new fetch. (3) A snapshot-level assertion that the MSW-served surface, whose `snapshot_ts` is months old, renders the stale badge. |

**FE-03** — `VoldeskApp.tsx:142` + `views/DashboardView.tsx:194` — "Market open" asserted from
tick recency while the engine republishes a frozen tick 5×/s forever.

| | |
|---|---|
| **Coverage of the defect** | **ZERO** — `VoldeskApp.tsx` **0%** (328 lines), `DashboardView.tsx` **0%** (452 lines). |
| **Tests touching this code** | None. **All 7 voldesk views are at 0%**: Dashboard, Portfolio (1251 lines), Risk (645), Settings (340), Signals (405), System (91), Trade (992). ~4,200 lines of the actual trading desk, untested. |
| **Test that would have caught it** | A `DashboardView` render test driving the tick stream with a mock WS that emits an *unchanging* mid at 5 Hz, asserting the market-status badge flips to closed once the underlying `market_hours` window says so — i.e. asserting the badge reads from the **session calendar**, never from message arrival rate. |

---

### 1b. Blocker scoreboard

| Blocker | Defective path coverage | Nearest test | Verdict |
|---|---|---|---|
| R3-01 | ZERO (1/81 stmts) | `test_aggregate_greeks_futures.py` (futures delta sign) | blind |
| R3-02 | ZERO (0/17) | `test_delta_hedge.py` (pure decision fn) | blind |
| DI-01 | ZERO (0/13) | none | blind |
| EX-01 | ZERO (0/20 api, 2/20 engine) | `test_close_stacking_guard.py` (pure arithmetic) | blind |
| DI-02 | IMPORT-ONLY (behaviour 0/~90) | **no writer test exists** | blind |
| DI-03 | ZERO (0/13) | `test_position_sync_reconcile.py` (diff helper) | blind + B4 |
| DI-35 | ZERO (1/11) | none | blind + B4 |
| FE-01 | **EXECUTED, 100% lines** | `useFetch.test.tsx` | asserts error semantics, not age |
| FE-02 | **EXECUTED, 100% lines** | `foundation.test.tsx` freshness block | asserts the pure comparator with injected `now` |
| FE-03 | ZERO (0%, 780 lines) | none | blind |

**8 / 10 Blockers: zero executable coverage of the defective line.**
**2 / 10: covered by green tests asserting a different property.**
**0 / 10: caught by any assertion, anywhere.**

---

### 1c. The 47 Highs

| ID | Sev | Defective path coverage | Nearest test / what it asserts instead |
|---|---|---|---|
| **F-01** rate-limit key attacker-controlled | High | `rate_limit.py` **100%** — EXECUTED | `test_login_rate_limit.py` fires 6 requests from the *same* client and asserts a 429. Never sends an `X-Forwarded-For` header. One test with a spoofed XFF per request, asserting the 6th still 429s, catches it. |
| **F-02** Redis password logged | High | ZERO (`main.py:100-115` = 0/11) | No lifespan test. A log-scrubbing test (assert no startup log record contains `://` credentials) catches it and never regresses. |
| **F-03** unbounded Prometheus label | High | `timing.py` **100%** — EXECUTED | No test asserts label cardinality. A test hitting 200 distinct paths and asserting `< N` histogram children catches it. |
| **QM-01** T≤0 returns 0 | High | EXECUTED — **and pinned** | `test_degenerate_inputs_return_zero` asserts the defect *is* the contract. |
| **QM-02** dead `interpolate_iv` | High | EXECUTED (16/18) with **retired schema fixture** | `test_interpolate_iv_returns_closest_strike` — flat pillar keys no producer emits. |
| **A6-01** cockpit executes a pricer | High | **ZERO — `structures.py` is never imported by any test** (0%) | The module the api runs at request time has no test at all. |
| **A6-02** three divergent BS copies | High | ZERO for copy 3 | No cross-implementation equivalence test exists. One parametrised test over the 3 pricers × the same inputs (incl. `T=0`, `"C"/"call"/"CALL"`) catches all three divergences. |
| **DEP-01..04** | High | n/a — supply-chain/legal, no code path | Not test-addressable, except DEP-01: a lockfile-drift CI check. |
| **R3-03** `refresh_book_state` mock-only | High | ZERO (`trade.py:1020-1060` 0/16, `1140-1170` 0/9) | `test_trade_submit_gating.py` says in its own docstring: *"We don't test the full /trade/submit handler here"*. |
| **R3-04** option strike used as spot | High | ZERO (`portfolio_panel.py:790-830` = 2/30) | `test_portfolio_panel_valuation.py` always supplies a future with a `market_price`. |
| **R3-05** NULL-iv positions silently dropped | High | ZERO (`:816`) | No test seeds a NULL `iv` row. Grep for `iv=None` across router tests: one hit, in an unrelated greek-PnL helper default. |
| **R3-06** `strike=None` pillar → 8% fallback | High | ZERO (`engine.py:520-540` = 2/13) | none |
| **R3-07** greeks 125,000× off | High | ZERO (`engine.py:540-570` = 11/22; the publish block 554-564 all missing) | `test_aggregate_greeks_futures.py` covers the *other* aggregation path. A cross-consumer unit-equality test (`/aggregate-greeks` vs the risk channel, same book) catches it. |
| **R3-08** no greek limit enforced | High | `greek_limits.py` **98%** — EXECUTED | `test_greek_limits.py` — 9 thorough tests of `compute_caps`, `ewma`, `nav_base`, `regime_mult`. **Not one asserts that exceeding a cap blocks anything.** Grep for `kill.?switch|breach|halt` across `tests/unit` returns zero enforcement tests. Worse: it tests `regime_mult`, which R3-21 identifies as **dead code with no production caller** — the suite is validating a function nothing calls while the enforcement path has no function to call. |
| **R3-09** NaN IV → invalid JSON on the bus | High | ZERO (`publisher.py:55-80` = 3/9; module 35.1%) | No `bus/` test folder exists at all. A publisher test asserting `json.loads(publish_payload(nan))` round-trips catches it. |
| **EX-02** handlers lost on reconnect | High | ZERO (`order_executor.py:95-150` = 2/27; module 29.0%) | `test_reconnect_loop.py` uses a `FakeExecutor` whose `connect()` sets a bool — **there is no `IB()` object in the fake**, so "the handlers are re-attached to the new `IB()`" is not expressible in this harness. It asserts `connect_calls == 5`. Tautological w.r.t. the defect. |
| **EX-03** commission permanently 0 | High | EXECUTED (`fills_handler.py:160-200` = 20/22) — **and the mock hides it** | `test_fills_handler.py:_fake_fill` builds `commissionReport=SimpleNamespace(commission=2.0)`. Real `ib_insync` delivers an **empty** `CommissionReport` at `fillEvent` time. The test asserts `total_commission_usd == 2.5` and passes because the mock supplies data the real callback never has. **Textbook blind spot B3.** |
| **EX-04** no `errorEvent` handler | High | ZERO (absent code) | Nothing to cover. A test asserting `ib.errorEvent` has ≥1 subscriber after `connect()` catches it. |
| **EX-05** `Inactive` → terminal `rejected` | High | EXECUTED — **and pinned** | `test_on_order_status_marks_rejected` sends `status="Inactive"` and asserts `order.state == "rejected"`. The defect is the test's title. |
| **EX-06** unknown IB statuses dropped | High | Partially (`:96,:100` missing) | No test sends an unmapped status. A parametrised test over IB's full status vocabulary asserting every value is either mapped or logged catches it. |
| **EX-07** no startup-reconciliation gate | High | ZERO (`main.py:114-190` = 0/28) | No lifespan test in the execution engine. |
| **EX-08** risk gate fails open | High | EXECUTED — **and pinned** | `test_revalidation.py::test_does_not_block_on_stale_surface` asserts `r.passed` on a 300 s-old surface against a 120 s limit, with a comment explaining the gate "was dropped on request". The fail-open is the documented contract. |
| **EX-09** no kill switch | High | n/a (absent) | A repo-level test asserting a halt endpoint exists and, when set, makes `/trade/submit` return 503. |
| **EX-10** no price collar / notional cap | High | EXECUTED (`live_submit.py:110-180` = 27/36) | `test_marketable_limit.py::test_buy_prices_at_the_ask_snapped_up` pins "use IB's raw touch" as correct. No test feeds an absurd touch (e.g. 100× theoretical) and asserts a refusal — because no code refuses. |
| **EX-11** rollback loses unwind rows | High | Mostly EXECUTED (`rollback_runner.py:155-245` = 25/30) | `test_rollback_runner.py` tests the happy path and a single-failure path. It never fails **mid-loop on leg 2 of 3** and asserts legs 0–1 are durable. That is the exact uncovered branch (164,167,171,207,211). |
| **EX-12** fail-open dedup lock | High | EXECUTED — **and pinned** | `test_preview_lock_falls_back_when_redis_unavailable` and `…_when_redis_raises` both `assert await _acquire_preview_lock(...) is True`. Fail-open is asserted twice, deliberately. The non-atomic read-modify-write at `trade.py:780-795` is separately **ZERO** (0/8). |
| **DI-04** one bad row kills the batch | High | ZERO (`writer.py:261-290` = 1/16) | no writer test |
| **DI-05** retry duplicates rows | High | ZERO (`writer.py:230-260` = 0/17) | no writer test |
| **DI-06** `db_events` fire-and-forget | High | `db_events.py` 100% (8 stmts, trivial), consumer 29/33 | `test_service.py` is genuinely good (scripted fake pubsub, reconnect, supervision). It tests *the subscriber surviving*, never *messages published while it was down being lost* — untestable without a broker. |
| **DI-07** queue overflow drops silently | High | EXECUTED (9/9) | `test_service.py` asserts `QueueFull` → warning + drop. It **asserts the drop is correct**; the finding is that a drop needs a metric/alert. Assertion mismatch, not coverage. |
| **DI-08** duplicate hourly PCA samples | High | vol side ZERO (0/9), analytics side EXECUTED (11/11) | `test_collect_snapshot.py` tests one engine in isolation. Nothing tests the two engines writing the same table. A static test asserting exactly one producer per table catches it. |
| **DI-09** missing unique key | High | `models.py` 100% — **declaration-only** | Importing an ORM class is not testing its constraints. A static test comparing sibling tables' unique keys catches it. |
| **DI-10** two uncoordinated writers | High | ZERO both sides (risk 0/17, sync 0/28) | no concurrency test anywhere |
| **DI-11** Float vs Numeric split | High | `models.py` 100% — declaration-only | A static test asserting every money/PnL column is `Numeric` catches it in 10 lines. |
| **DI-12** manual events skip `event_hash` | High | ZERO (`regime.py:365-385` = 3/13) | `test_hashing.py` tests the hash function; nothing asserts every insert path calls it. |
| **DI-13** config hot-reload never converges | High | ZERO (`vol/main.py` **never imported**; `engine.py:517-518` missing) | `test_config_guard.py` covers the api-side validation only. |
| **DI-14** no staleness field on vol reads | High | EXECUTED (`vol_service.py:35-90` = 12/15) | `test_vol_service_fair.py` asserts the *values* returned. Nothing asserts a freshness/staleness field exists — it doesn't, so the assertion was never written. Sibling of FE-01/02. |
| **DI-15** stale hash fields served as live | High | ZERO both sides (0/14, 4/15) | none |
| **DI-36** interleaved partial fills race | High | EXECUTED sequentially (`fills_handler.py:205-245` = 23/25) | `test_on_execution_writes_fill_and_aggregates` **awaits the two partial fills one after the other** and asserts `qty_filled == 5`. The defect requires them concurrent. `asyncio.gather` on the same two fills, asserting `qty_filled == 5`, is a 3-line change that fails today. |
| **FE-04** WS status thrown away | High | `streams.ts` 100% lines / 57% branches | `useWebSocket` is 77% covered; `streams.ts` is 100%. Nothing asserts a *view* renders the status — all 7 views are 0%. |
| **FE-05** mock `SPOT = 1.0842` fallback | High | `core.ts` 84.96% | `foundation.test.tsx` adapter tests always supply spot. No test asserts the order-sizing path refuses to size off a fallback constant. |
| **FE-06** ~24 endpoints typed `unknown` | High | `endpoints.ts` 92.03% **stmts**, 52.08% **funcs** | `endpoints.test.ts` has **4 tests**: health, surface query param, price POST, non-2xx error. The 24 `apiGet<unknown>` portfolio/risk endpoints — the ones feeding every risk panel — are outside both the test and the OpenAPI drift gate. |
| **FE-07** single global error boundary | High | `ErrorBoundary.tsx` 100%; `main.tsx` **excluded from coverage** | The component is tested; its *placement* (one instance, at the root) is a wiring property no unit test can see. |
| **FE-08** prod source maps served | High | n/a — build config | A CI assertion that `dist/**/*.map` is absent from the production image catches it. |

**Highs scoreboard:** 39 of 47 have **zero or import-only** coverage of the defective path.
6 are **executed by green tests that assert the defect as the contract** (QM-01, EX-05, EX-08,
EX-10, EX-12, DI-07). 2 (EX-03, QM-02) are executed by tests whose **mocks or fixtures are
richer/staler than production**, which is what makes them pass.

---

## Part 2 — Suite-quality findings

| ID | Severity | Location | Finding |
|---|---|---|---|
| **T7-01** | **Blocker** | 8 of 10 Blocker paths | The suite has zero executable coverage of the defective line on 8 of the 10 campaign Blockers, and 39 of 47 Highs. The 07 exit criterion is not met. |
| **T7-02** | **Blocker** | 7 test functions | Tests assert defective behaviour as the intended contract. Fixing the Blocker turns these red, inviting a revert. Full list below. |
| **T7-03** | **Blocker** | all DB unit tests | In-memory SQLite with `PRAGMA foreign_keys` **OFF** (never enabled), plus `BigInteger→Integer` metadata mutation. Cascades, FK RESTRICT, `CHECK`, `ON CONFLICT` and `Numeric` semantics are all inert or divergent. Structurally hides DI-02/03/09/10/12/23/35. |
| **T7-04** | **High** | `.github/workflows/ci.yml:66` | Backend coverage is measured and uploaded but **no `--cov-fail-under`**. There is no backend coverage gate of any kind. |
| **T7-05** | **High** | `CLAUDE.md:215` vs `frontend/vitest.config.ts:37` | Documented gate "≥ 70% coverage"; enforced gate `lines: 15`; measured **17.36%**. The doc overstates the gate 4.7×. |
| **T7-06** | **High** | `frontend/e2e/dashboard.spec.ts` + `fixtures.ts` + `ci.yml` `frontend-e2e-compose` | The compose job boots postgres+redis+api+nginx+frontend (20 min budget) and then runs a spec whose `beforeEach` calls `mockBackend(page)`, which `page.route`-intercepts **every** `/api/v1/*` route. The job proves nginx serves the SPA and nothing else. **There is no round-trip test anywhere** (UI → api → redis → engine → back). |
| **T7-07** | **High** | `src/engines/execution/structures.py` | Never imported by any test — 0% — while `api/routers/cockpit.py` executes its Black-76 pricer at request time (A6-01). Also `bus/client.py`, all 5 engine `main.py`, `vol/chain_fetcher.py`, `vol/historical_fetcher.py`, `api/orchestration/position_close.py`, `trade_preview_expirer.py`, `pca_refit_scheduler.py`: **68 source files never imported by the suite**. |
| **T7-08** | **High** | `tests/unit/engines/execution/test_fills_handler.py:70-80` | Mock realism: `_fake_fill` supplies a populated `CommissionReport`. Real `ib_insync` delivers an empty one at `fillEvent`. The mock is the reason EX-03 is green. |
| **T7-09** | **High** | `tests/unit/core/pricing/test_bs.py:64`, `tests/unit/core/positions/test_position_pricing.py:11`, `frontend/src/tests/mocks/handlers.ts:35`, `frontend/e2e/fixtures.ts` | Surface fixtures use the **retired flat schema** in four places. No shared canonical payload exists (`tests/fixtures/` contains only `.gitkeep`), so a schema change breaks zero tests. |
| **T7-10** | **High** | repo-wide | No frozen clock. No `freezegun` / `time-machine` / `vi.useFakeTimers`. 10 backend test files call live `datetime.now()`. Consequence: no test can express "this went stale", which is the whole of Theme C. |
| **T7-11** | **High** | `tests/unit/engines/risk/` (4 tests), `src/persistence/writer.py` (0 tests), `src/bus/` (0 tests), `src/api/ws/` (0 tests) | Four components with no meaningful unit suite: the risk engine (22.1%), the DB writer (behaviour 0%), the Redis adapter (`publisher.py` 35.1%), the WS bridge (`redis_bridge.py` 32%, `connection_manager.py` 35%). Audit 7's checklist explicitly names the last two. |
| **T7-12** | Medium | `tests/integration/pipeline_execution_engine/` | 11 test functions are `@pytest.mark.skip` + `raise NotImplementedError` — pure placeholders since STEP4/5. They are the *only* tests for submit→fill→rollback→close end-to-end. |
| **T7-13** | Medium | `tests/` | No `conftest.py` anywhere; `tests/fixtures/` is empty despite `STRUCTURE.md` documenting it as "reusable builders". `_make_session()` + `_coerce_bigint_to_integer()` are copy-pasted into **11 files**. |
| **T7-14** | Medium | `tests/unit/api/orchestration/events/test_calendar_sources.py` | Time bombs: `test_ecb_returns_at_least_4_future_meetings`, `test_all_sources_only_emit_future_events` etc. assert hardcoded 2026 schedules against live `datetime.now()`. They will fail on a date, not on a defect. |
| **T7-15** | Medium | `tests/unit/engines/execution/test_reconnect_loop.py`, `test_ib_connection.py`, `test_service.py` | Wall-clock polling loops (`for _ in range(300): await asyncio.sleep(0.01)`). Deterministic on this machine; load-sensitive on a shared CI runner. Combined with `playwright.config.ts:15` `retries: 1` in CI, flakiness is masked rather than surfaced. |
| **T7-16** | Medium | `pyproject.toml [dev]` | No `pytest-randomly`, no `pytest-xdist`. Test order is fixed file order, so inter-test state leakage (11 modules each creating global SQLAlchemy metadata mutations via `_coerce_bigint_to_integer`) is never shaken out. **`_coerce_bigint_to_integer` mutates the shared `Base.metadata` in place** — a real cross-test global mutation that only fixed ordering keeps benign. |
| **T7-17** | Medium | coverage denominator | The 58.1% global figure is inflated by ~602 declaration-only statements (`persistence/models.py` 490 + `api/schemas` 112) that reach 100% purely on import. Excluding them: **55.8%**. `src/persistence` excluding `models.py` is **38.5%**, not 83.3%. |
| **T7-18** | Medium | `frontend/src/voldesk/views/` | All 7 views at **0%** (~4,200 lines). The 17.36% is carried entirely by `data/live/*` adapters (89.85%) and `api/` (90.04%) — the audit's suspicion inverted: it is not trivial *component* tests propping up the number, it is good *adapter* tests, while every rendered surface is dark. |
| **T7-19** | Low | `tests/unit/core/test_greek_limits.py:88` | `test_regime_mult_clamped_and_safe` tests `greek_limits.regime_mult`, which R3-21 identifies as dead code with no production caller. The suite spends assertions on an unreachable function while the reachable enforcement path has none. |
| **T7-20** | Low | `tests/unit/core/risk/test_greeks_vec.py` (3 fns) | Flagged assertion-free by AST scan; they use `np.testing.assert_allclose`, so this is a false positive — recorded so a future scan does not re-raise it. |

### T7-02 — the seven tests that pin a defect

| Test | Pins |
|---|---|
| `tests/unit/core/pricing/test_bs.py::test_degenerate_inputs_return_zero` | **QM-01** — `T≤0 ⇒ 0.0` asserted as correct |
| `tests/unit/core/risk/test_greeks_vec.py::test_bs_price_vec_degenerate_returns_zero_vector` | **QM-14 / QM-01** vectorised twin |
| `tests/unit/core/positions/test_position_pricing.py::test_price_position_zero_when_no_iv_available` | asserts `mark_value_usd == 0` for a real position — the R3-01 shape |
| `tests/unit/engines/execution/test_fills_handler.py::test_on_order_status_marks_rejected` | **EX-05** — `Inactive ⇒ rejected` |
| `tests/unit/core/execution/test_revalidation.py::test_does_not_block_on_stale_surface` | **EX-08** — stale surface passes the gate |
| `tests/unit/api/routers/test_trade_submit_gating.py::test_preview_lock_falls_back_when_redis_unavailable` (+ `…_when_redis_raises`) | **EX-12** — fail-open dedup |
| `tests/unit/engines/execution/test_marketable_limit.py::test_buy_prices_at_the_ask_snapped_up` | **EX-10** — raw IB touch, no collar |

Each of these must be **rewritten in the same commit as its fix**, with a comment pointing at
the audit finding — otherwise the red test reads as a regression.

---

## Part 3 — Per-package coverage baseline (2026-08-01, pre-fix)

Measured with `pytest --cov=src` on the 794-test unit run. Record this table for future
comparison; the "should be" column is a proposed gate, not a current one.

### Backend — top level

| Package | Statements | Covered | **Coverage** | Proposed floor |
|---|---:|---:|---:|---:|
| `src/core` | 2,463 | 2,335 | **94.8%** | 90% |
| `src/persistence` | 672 | 560 | 83.3% | — |
| ↳ *excluding `models.py` (declaration-only)* | *182* | *70* | **38.5%** | 80% |
| `src/shared` | 295 | 237 | **80.3%** | 80% |
| `src/bus` | 119 | 68 | **57.1%** | 80% |
| `src/api` | 4,743 | 2,139 | **45.1%** | 60% |
| `src/engines` | 3,293 | 1,389 | **42.2%** | 70% |
| **TOTAL** | **11,585** | **6,729** | **58.1%** | 65% |
| *TOTAL excl. declaration-only* | *10,983* | *6,127* | *55.8%* | — |

### Backend — the packages that matter for the campaign

| Module | Stmts | **Coverage** | Blockers/Highs living here |
|---|---:|---:|---|
| `src/core/vol` | 935 | **94.1%** | QM-03..QM-13 (all Medium/Low) |
| `src/core/execution` | 308 | **95.8%** | EX-08 (asserted fail-open) |
| `src/core/positions` | 287 | **98.6%** | R3-02's callee |
| `src/core/risk` | 238 | **93.7%** | R3-08 (calculator only) |
| `src/core/pricing` | 84 | **89.3%** | QM-01, QM-02 |
| `src/engines/db_writer` | 133 | **91.0%** | DI-06, DI-07, DI-16 |
| `src/engines/market_data` | 120 | **85.8%** | — |
| `src/engines/analytics` | 160 | **69.4%** | DI-08 |
| `src/engines/execution` | 1,829 | **44.3%** | EX-01..EX-28, DI-03, DI-36 |
| ↳ `position_sync.py` | 277 | **11.0%** | **DI-03**, DI-10, DI-15 |
| ↳ `hedge_executor.py` | 84 | **18.0%** | EX-15, DI-38 |
| ↳ `order_executor.py` | 239 | **29.0%** | **EX-02**, EX-23, EX-24 |
| ↳ `reconciler.py` | 61 | **33.0%** | EX-07 |
| ↳ `main.py` | 278 | **42.0%** | **EX-01**, EX-04, EX-07, EX-22 |
| ↳ `structures.py` | — | **0% (never imported)** | A6-01, A6-02, EX-18, EX-19 |
| `src/api/routers` | 3,203 | **36.0%** | R3-03..R3-07, EX-01, EX-12, DI-12 |
| `src/api/orchestration` | 1,053 | **59.8%** | **R3-02**, **DI-01**, DI-14, DI-20 |
| `src/api/ws` | 71 | **33.8%** | F-11, DI-32 |
| `src/engines/vol` | 703 | **24.0%** | **DI-35**, DI-08, DI-13, DI-21 |
| `src/engines/risk` | 348 | **22.1%** | **R3-01**, R3-06..R3-09, DI-10, DI-15 |
| `src/persistence/writer.py` | 120 | **22.5%** | **DI-02**, DI-04, DI-05 |
| `src/bus/publisher.py` | 77 | **35.1%** | R3-09, DI-26 |

The inversion is exact: **the three lowest-covered non-trivial packages
(`engines/risk` 22.1%, `persistence/writer` 22.5%, `engines/vol` 24.0%) contain four of the
ten Blockers**, while the highest-covered package (`core/` 94.8%) contains none.

### Frontend (vitest v8, `include: src/**/*.{ts,tsx}`)

| Area | **Lines** | Note |
|---|---:|---|
| `api/` | **90.04%** | but 52.08% *functions* — the 24 `unknown`-typed endpoints (FE-06) |
| `voldesk/data/` | **91.45%** | `freshness.ts` 100% — and FE-02 lives there |
| `voldesk/data/live/` | **89.85%** | the adapter layer; genuinely well tested |
| `voldesk/auth/` | **95.65%** | |
| `store/` | **91.83%** | |
| `components/` (ErrorBoundary) | **100%** | |
| `hooks/` | **61.32%** | `useFetch` 100% lines — FE-01/FE-02 live there; `useWsLog.ts` 0% |
| `voldesk/components/` | **2.88%** | `OrderBuilder.tsx` (848 l.) 0%, `PositionsTable.tsx` (438 l.) 0%, `FreshBadge.tsx` 0% |
| `voldesk/` root | **0%** | `VoldeskApp.tsx` (328 l.) — FE-03 |
| **`voldesk/views/`** | **0%** | all 7 views, ~4,200 lines |
| `pages/` + `pages/dev/` | **0%** | ~5,900 lines of dev tooling |
| `components/layout`, `components/charts` | **0%** | |
| **All files** | **17.36%** | gate: `lines: 15`. Doc claims 70%. |

Branch coverage is 73.88% against 17.36% line coverage — the classic signature of a suite that
exercises a small number of files exhaustively and the rest not at all.

---

## Part 4 — Tests to write, ordered by the severity of what they guard

Descriptions only, per the audit's findings-only rule. Tiers 0–1 are the release gate.

### Tier 0 — harness fixes that must land first (nothing below works without them)

1. **`tests/conftest.py` with a shared DB fixture.** One `db_session` fixture. For SQLite it
   must register an `event.listens_for(engine, "connect")` hook issuing
   `PRAGMA foreign_keys=ON`; it must **not** mutate the shared `Base.metadata` (the current
   `_coerce_bigint_to_integer` copy-paste does, in 11 files). Guards: **DI-03, DI-35, DI-10**
   — none of which is expressible today. *(T7-03, T7-13)*
2. **A Postgres-backed variant of that fixture**, marked `db_integration`, wired into the
   `live-integration` CI job that already runs Postgres 16. Anything touching `ON CONFLICT`,
   `ondelete`, `CHECK` or `Numeric` must use it. Guards: **DI-02, DI-03, DI-09, DI-12, DI-23**.
3. **`tests/fixtures/payloads.py` — one canonical instance of every Redis payload and API
   response**, generated from or asserted against the producer's real emission. Every surface
   fixture in `test_bs.py`, `test_position_pricing.py`, `handlers.ts` and `e2e/fixtures.ts`
   imports from it. Guards: **QM-02, DI-01, FE-13**, and turns every future schema drift into
   30 red tests. *(T7-09)*
4. **Add `freezegun` (backend) and adopt `vi.useFakeTimers()` (frontend).** Prerequisite for
   every FE-01/02/03 and DI-14/15 test below. *(T7-10)*
5. **Add `--cov-fail-under` to the backend CI step** and per-package floors (proposed values in
   Part 3). Correct `CLAUDE.md:215` to the real frontend gate. *(T7-04, T7-05)*

### Tier 1 — one test per Blocker (release gate)

6. **Expiry-day pricing property test** — deep-ITM call at `T=1e-9`: price → `max(F-K,0)`,
   delta → 1.0, and continuity from the right. Rewrite `test_degenerate_inputs_return_zero`
   in the same commit. *Guards QM-01, R3-01 root, QM-14, EX-19.*
7. **Risk-cycle expiry-day integration test** — one option expiring today through a full
   `RiskEngine` cycle: assert `iv IS NOT NULL`, greeks non-NULL, and that the position is
   still present in the aggregate/VaR/stress outputs. *Guards R3-01, R3-05.*
8. **Position-monitor delta-hedge wiring test** — assert the `delta_unhedged` handed to
   `check_delta_hedge_needed` equals the recomputed leg delta and is non-zero; a second test
   asserting the degraded-reprice branch **fails closed** (skip/alert), never `0.0`.
   *Guards R3-02.*
9. **Surface-payload contract test** — the exact dict `engines/vol/engine.py` publishes, fed
   to `interpolate_iv` and to the monitor's reprice: assert non-`None` IV **and** non-`None`
   spot. *Guards QM-02, DI-01.*
10. **Close-handler state test** — seed a close order in each of `rejected`, `cancelled`,
    `submitted`, `partially_filled`; assert which block a retry. `rejected` must block until
    reconciled. Plus a budget assertion that the client timeout exceeds the handler's
    worst-case path. *Guards EX-01, EX-14, EX-21.*
11. **Writer idempotency test on real Postgres** — same logical row published twice with two
    producer timestamps ⇒ exactly one row, for all 5 `IDEMPOTENT_TABLES`. Fails today on 4.
    *Guards DI-02.*
12. **Static constraint test** — every `IDEMPOTENT_TABLES` entry maps to a real
    `UniqueConstraint` in `models.py`; sibling snapshot tables have matching unique keys; every
    money/PnL column is `Numeric`. ~30 lines, guards **DI-02, DI-09, DI-11, DI-12, DI-23**.
13. **Cascade-durability test on real Postgres** — close a position, assert its
    `open_position_history` rows survive. *Guards DI-03.*
14. **Pruner resilience test** — injected session raising `IntegrityError` on the first
    `DELETE`: assert the second still runs in its own transaction and an `ERROR` is logged
    (`caplog`). Plus a repo-wide static test that no background-loop `except Exception:` is
    silent. *Guards DI-35, EX-28, DI-21.*
15. **`useFetch` age test (fake timers)** — a payload stamped 3 days ago renders `stale` on
    first paint; and after `advanceTimersByTime(warnMs+1)` a live panel re-renders `stale`
    without a refetch. *Guards FE-01, FE-02.*
16. **Market-status test** — a mock WS emitting an unchanging mid at 5 Hz must not keep the
    badge on "Market open" once the session calendar says closed. *Guards FE-03.*

### Tier 2 — the Highs, by cluster

17. **Cross-implementation pricer equivalence** — parametrise the 3 Black-Scholes copies over
    a shared grid including `T=0`, `sigma=0`, and all three right encodings; assert agreement
    to 1e-12. *Guards A6-02, EX-19, QM-09; makes the consolidation refactor safe.*
18. **First test of `engines/execution/structures.py`** (currently never imported): multiplier,
    tenor range, `T≤0`, greek units vs `core/`. *Guards A6-01, EX-18.*
19. **Concurrent partial fills** — `asyncio.gather` the two fills in
    `test_on_execution_writes_fill_and_aggregates`; assert `qty_filled == 5`. Three lines,
    fails today. *Guards DI-36.*
20. **Realistic commission mock** — `_fake_fill` must default to an **empty** `CommissionReport`
    (real `fillEvent` shape); add a separate test for `commissionReportEvent` delivery.
    *Guards EX-03; the current test must be rewritten, not deleted.*
21. **Reconnect handler-survival test** — replace `FakeExecutor`'s bool with a fake `IB()`
    exposing `fillEvent`/`orderStatusEvent`/`errorEvent` slots; assert every handler has ≥1
    subscriber after a reconnect, and that `errorEvent` has one at all. *Guards EX-02, EX-04.*
22. **IB status vocabulary test** — parametrise over IB's full order-status set; every value is
    either mapped or logged; `Inactive` is non-terminal. *Guards EX-05, EX-06, EX-13.*
23. **Fail-closed risk gate** — submit with an absent/stale book snapshot must be **refused**.
    Rewrites `test_does_not_block_on_stale_surface`. *Guards EX-08, R3-03.*
24. **Limit enforcement + kill switch** — a book breaching each greek cap must block submission;
    with the halt flag set, `/trade/submit` returns 503 and working orders are cancellable.
    *Guards R3-08, EX-09.*
25. **Price collar / notional cap** — a touch at 100× theoretical must be refused.
    Rewrites `test_buy_prices_at_the_ask_snapped_up`. *Guards EX-10.*
26. **Mid-loop rollback durability** — fail on leg 2 of 3; assert legs 0–1 are committed and
    every placed order carries an `orderRef`. *Guards EX-11.*
27. **Publisher NaN guard** — `json.loads(publish_payload(nan))` must round-trip or raise.
    First test in a new `tests/unit/bus/`. *Guards R3-09.*
28. **Writer batch resilience** — one poison row must not discard 99 good ones; a retry after
    a possibly-committed batch must not duplicate. *Guards DI-04, DI-05.*
29. **Greek-unit equality across consumers** — same book through `/aggregate-greeks` and the
    risk channel must agree within 1e-6 (currently 125,000×). *Guards R3-07, R3-10, R3-12.*
30. **NULL-iv and no-market-price book resolution** — assert `_resolve_book` never silently
    drops rows and never uses an option strike as spot. *Guards R3-04, R3-05.*
31. **Rate-limit key test with spoofed `X-Forwarded-For`** per request; the 6th must still 429.
    *Guards F-01.*
32. **Startup log-scrub test** — no startup record contains `://user:pass@`. *Guards F-02.*
33. **Metric cardinality test** — 200 distinct paths ⇒ bounded histogram children.
    *Guards F-03.*
34. **Single-producer-per-table static test.** *Guards DI-08.*
35. **Frontend view smoke tests, all 7 views** — render against MSW, assert no crash and that
    the freshness/connection indicators are present. Lifts `voldesk/views/` off 0% and is the
    precondition for FE-04, FE-05, FE-09..FE-12 ever being testable.
36. **Type the 24 `unknown` endpoints** and extend `endpoints.test.ts` beyond its 4 cases so
    the OpenAPI drift gate actually covers the risk panels. *Guards FE-06.*

### Tier 3 — structural

37. **One real round-trip e2e.** Either drop `mockBackend` from the compose job's spec (it is
    the same spec as the preview job — they must diverge) or add a compose-only spec that
    drives UI → api → redis → engine → UI. Today the 20-minute compose job asserts nginx
    serves `index.html`. *Guards T7-06 and every Audit-5 Appendix-A contract mismatch.*
38. **Fill in or delete the 11 `NotImplementedError` skeletons** in
    `tests/integration/pipeline_execution_engine/`. They are the only end-to-end coverage of
    submit→fill→rollback→close and they have never run. *T7-12.*
39. **Add `pytest-randomly`**, then fix whatever the shared-metadata mutation breaks. *T7-16.*
40. **De-time-bomb the calendar-source tests** — assert against a frozen `now` and fixture
    schedules, not the live clock. *T7-14.*

---

## Verified clean (worth recording)

- **794 tests, 15 skipped, 8–12 s, three consecutive green runs.** No flakiness observed
  locally; no order-dependence surfaced (though nothing shuffles the order — T7-16).
- **Only 3 apparently assertion-free unit tests**, all false positives
  (`np.testing.assert_allclose`). The 11 genuine no-op tests are the explicitly-skipped
  IB paper skeletons.
- **`src/core/` is a genuinely strong suite** — 94.8%, finite-difference greek checks,
  put-call parity, IV round-trips, PCA/GMM determinism, HAR/GARCH/SVI/SSVI edge cases,
  `test_units_convention.py`. The campaign found no Blocker in `core/` and that is not a
  coincidence.
- **The FRED/ECB/BoE/FOMC/Eurostat/ONS events pipeline is well covered** — fixture-payload
  parser tests plus tz-correctness assertions per source (the audit's checklist item is met,
  modulo the T7-14 time bombs).
- **`engines/db_writer/service.py` at 91%** with a scripted-fake pubsub covering the
  reconnect/supervision fix — the best-engineered engine-side test file in the repo, and the
  model the other four engines should copy.
- **`test_portfolio_var_methods.py`** correctly asserts *refusal* on a part-filled bar cache —
  the only fail-closed assertion found in the whole suite.
- **Integration markers are correctly gated**: all 15 skips are `IB_RUN_INTEGRATION` /
  `DB_RUN_INTEGRATION` / `REDIS_RUN_INTEGRATION` guards, none accidental. No unit test imports
  `redis`, `psycopg` or `ib_insync` directly (STRUCTURE.md's reviewer rule holds).

## Not verified (out of scope for this run)

- The gated suites were **not** executed (`DB_RUN_INTEGRATION`, `REDIS_RUN_INTEGRATION`,
  `IB_RUN_INTEGRATION` all left unset per the run instructions), so the audit checklist item
  "gated suites still pass against live local resources" is **open**. CI's `live-integration`
  job is green on `main`, which covers the first two.
- Playwright was not run (needs a browser download + a served bundle); the e2e analysis is
  from reading `dashboard.spec.ts`, `fixtures.ts`, `playwright.config.ts` and the two CI jobs.
