# Audit 6 — Architecture & Code Quality — Findings Report

**Date:** 2026-08-01
**Auditor:** automated review agent (Audit 6, per `docs/audits/06-architecture-code-quality.md`)
**Scope:** entire `src/` tree, `.importlinter`, `pyproject.toml` tooling config, repo hygiene.
**Method:** import-linter run, ruff (configured set + `--select ALL` survey), mypy per package,
grep sweeps for hidden I/O / env reads / logging / dead code, manual review of flagged areas.
No source code was modified.

---

## Executive summary

The v2 layer boundaries are **real in the import graph** — all 5 import-linter contracts
pass (225 files, 349 dependencies), ruff is clean on the configured rule set, there is no
Qt/desktop residue, no pre-rename paths (`api/models`, `api/services`, `src/services`,
`redis_client`, `db_queue`), no `tests/old`, no `print()` in `src/`, and Pydantic usage is
uniformly v2. Repo hygiene is good (no tracked `__pycache__`, `src/fxvol.egg-info` ignored).

The boundaries are, however, **not fully real in substance**. The two High findings are the
same root problem seen from two angles: the one allow-listed exception
(`api.routers.cockpit → engines.execution.structures`) is documented as "pure data classes"
but actually ships a third, behavior-bearing Black-76 pricer that the api calls at request
time — and that pricer is the third independent Black-Scholes implementation in the tree,
each with different unit and edge-case conventions. Everything else is Medium/Low:
scattered env reads that contradict the "single env-var schema" claim (including one env-var
name split and one same-name/different-default drift), a staged-cutover feature flag left in
its "legacy" position so batch analytics run twice, a docs/reality gap around the 6th
(analytics) engine, a split logging stack where most modules bypass structlog, and an mypy
enforcement gap where three packages are already clean but nothing is enforced.

**Counts: 0 Blocker · 2 High · 8 Medium · 4 Low.**

---

## Findings table

| ID | Severity | Location | One-liner |
|----|----------|----------|-----------|
| A6-01 | High | `src/engines/execution/structures.py` + `src/api/routers/cockpit.py:131-159` | Allow-listed "data-classes-only" exception actually ships a full Black-76 pricer that api executes at request time |
| A6-02 | High | `src/core/pricing/bs.py`, `src/core/trade_preview.py:33-72`, `src/engines/execution/structures.py:61-99` | Three independent Black-Scholes implementations with diverging unit/edge-case conventions |
| A6-03 | Medium | `src/core/vol/tenors.py`, `src/core/trade_preview.py:25`, `src/engines/execution/structures.py:107`, `src/engines/vol/engine.py:64`, `src/core/risk/vega_pca.py:18` | Tenor→DTE/years maps duplicated in 5 places across 3 layers |
| A6-04 | Medium | `src/core/trade_preview.py:366,488` | `datetime.now(UTC)` inside `core/` with no injection point — breaks the documented determinism convention |
| A6-05 | Medium | ~40 call sites (see §A6-05) | `shared/config.py` is not the single env-var schema; incl. `EXECUTION_URL` vs `EXECUTION_ENGINE_URL` split and `PCA_SNAPSHOT_INTERVAL_MIN` default drift (55 vs 60) |
| A6-06 | Medium | `src/engines/vol/engine.py:63`, `docker-compose.yml:212`, `src/engines/analytics/` | `VOL_INLINE_ANALYTICS` staged-cutover flag never flipped — batch analytics computed twice by two engines |
| A6-07 | Medium | `README.md:82-142`, `CLAUDE.md` (§Architecture), `.importlinter:73` | Docs say 11 containers / 5 engines; reality is 12 core services incl. the 6th `analytics-engine`; other counts/paths stale |
| A6-08 | Medium | `src/shared/logging.py` + 41 modules | Split logging stack: 41 modules use stdlib `logging` (plain text, `extra=` fields silently dropped) vs ~10 on structlog JSON |
| A6-09 | Medium | `.github/workflows/ci.yml:62-64`, `pyproject.toml [tool.mypy]` | mypy fully advisory while `shared`/`bus`/`persistence` are already at 0 errors — free enforcement not taken |
| A6-10 | Medium | `src/api/routers/portfolio_panel.py` (2306 l.), `dev.py` (1612), `trade.py` (1377), `positions.py` (1354) | Business logic concentrated in giant routers despite `api/orchestration/` existing as the use-case layer |
| A6-11 | Low | `src/engines/execution/main.py:139-183` | Execution background loops run unsupervised (`create_task` without done-callbacks); engine-loop error policy deliberate but undocumented |
| A6-12 | Low | `src/engines/vol/main.py:3-7`, `src/api/main.py:130`, `src/api/orchestration/events/sources/__init__.py:8-15`, `src/api/routers/positions.py:1319`, `src/api/routers/regime.py:201,226` | Stale docstrings/comments: "synthetic stub" that is real, `sources/stubs.py` that doesn't exist, `[TODO]` labels on shipped modules, references to deleted `TODO.md`/`MARKETS_OPEN_TODO.md`, deprecated `datetime.utcnow()` |
| A6-13 | Low | `src/shared/contracts.py`, `src/shared/market_hours.py`, `src/shared/trace.py` vs `tracing.py` | Domain-flavored modules in `shared/`; no import-linter contract on the `core → shared` edge; confusable `trace`/`tracing` names |
| A6-14 | Low | `src/core/` (49 `Any` sites; loggers in `har_rv.py`, `ssvi.py`, `vrp.py`) | `core/` "typed explicitly, no Any" convention drifting; warning-loggers inside pure functions |

---

## A6-01 (High) — The allow-listed boundary exception is behavioral, not data-only

**Checklist item:** "The one allow-listed exception (cockpit → `engines.execution.structures`)
is still data-classes-only; nothing behavioral leaked through it."

**Evidence.** `.importlinter:96-104` whitelists `api.routers.cockpit -> engines.execution.structures`
with the comment "pure data classes describing option structures … that api/routers/cockpit.py
uses to serialize previews. The right fix is to promote those to core/structures.py".
In reality `src/engines/execution/structures.py` (296 lines) contains:

- a complete Black-76 pricer: `_phi`, `_N`, `bs_price` (lines 61-80), `bs_greeks` (83-99);
- behavioral methods on every "data class": `StraddleATM.legs()` / `.net_greeks()`,
  `RiskReversal25d`, `Butterfly25d`, `CalendarSpread` — each prices its legs off the live
  surface and aggregates portfolio greeks (`_sum_greeks`, lines 254-273);
- a signal-routing function `signal_to_structure()` (276-296).

`src/api/routers/cockpit.py` then *executes* this engine code inside the api process at
request time (`POST /api/v1/vol/trade-preview`):

```python
# cockpit.py:158-159
legs = s.legs(forward, surface.surface)
net = s.net_greeks(forward, surface.surface)
```

So the api container runs execution-engine pricing logic — exactly the "behavioral leak
through the boundary" the audit's exit criterion excludes. Note also the hardcoded spot
fallback `forward = … else 1.17` at `cockpit.py:139`.

**Recommended fix (not applied).** Do what the `.importlinter` comment already prescribes:
move the structure dataclasses *and* their pricing into `core/` (e.g. `core/structures.py`,
pricing delegated to `core/pricing/bs.py` — see A6-02), have both `api/routers/cockpit.py`
and `engines/execution/` import from core, then delete the `ignore_imports` line from
contract 5 so the exception ceases to exist. Remove the `1.17` literal in favor of a 404
when no forward is available.

---

## A6-02 (High) — Three independent Black-Scholes implementations with diverging conventions

**Checklist item:** "Duplicated domain logic across layers."

**Evidence.** Three separate BS/Black-76 pricers coexist:

| Implementation | `right` convention | Degenerate-input behavior | Vega units |
|---|---|---|---|
| `src/core/pricing/bs.py` (canonical, scipy-based; also `bs_vanna`, `bs_volga`, `bs_implied_vol`) | `"C"` / else put | returns `0.0` | per 1.0 vol point (`bs_vega`) |
| `src/core/trade_preview.py:33-72` (`_phi`/`_N`/`bs_price`/`bs_greeks`, math-based) | `"call"` / else put | returns **intrinsic** `max(0, F−K)` | raw per unit σ |
| `src/engines/execution/structures.py:61-99` (`_phi`/`_N`/`bs_price`/`bs_greeks`) | `"CALL"` / `"PUT"` | returns **intrinsic** | `/100` = per 1% IV; theta `/365` |

Same function names, three bodies, three unit conventions, two edge-case behaviors
(zero vs intrinsic on `T<=0`/`sigma<=0`). Any correction (e.g. a discounting or theta-sign
fix) applied to one copy silently misses the other two, and preview numbers served by
`cockpit.py` (engine copy) can disagree with previews served by `trade.py` (core
`trade_preview` copy) for the same trade — an unusual-but-plausible route to acting on a
wrong preview. This is the "wrong greeks" failure class of the audit plan.

**Recommended fix.** Single pricing kernel in `core/pricing/bs.py` with one documented
`right` literal type and one degenerate-input policy; `core/trade_preview.py` and
`engines/execution/structures.py` (or its `core/structures.py` successor per A6-01) import
it. Add a snapshot-equivalence test locking the merged outputs against both old paths
before deleting the duplicates.

---

## A6-03 (Medium) — Tenor math duplicated five ways across three layers

**Evidence.** Tenor→time maps are re-declared per module instead of imported from
`core/vol/tenors.py` (the intended home — other files even cite it in comments):

- `src/core/vol/tenors.py:25` `PILLAR_TARGET_DTE = {"1M": 30 … "6M": 180}` and `:106`
  `LABEL_DTE` (adds 9M/1Y) — the canonical maps;
- `src/core/trade_preview.py:25` `TENOR_TO_DTE` — same 30/60/…/180 values, re-typed;
- `src/engines/execution/structures.py:107` `TENOR_YEARS = {"1M": 1/12 …}`;
- `src/engines/vol/engine.py:64` `DEFAULT_TENOR_T` — same years map + 9M/1Y anchors;
- `src/core/risk/vega_pca.py:18` `TENORS` tuple (annotated "(core.vol.tenors)" but not imported).

Values are currently consistent; the duplication is pure drift surface (e.g. if 6M ever
moves from 180 to a listed DTE, four other copies stay stale).

**Recommended fix.** Export `PILLAR_TARGET_DTE`, `LABEL_DTE`, a derived
`tenor_years()` helper and the `TENORS`/`DISPLAY_PILLARS` tuples from `core/vol/tenors.py`
and import everywhere; delete local copies. (Migration files `011`/`models.py` `_TENORS`
lowercase copies are frozen history — leave those.)

---

## A6-04 (Medium) — `datetime.now(UTC)` inside `core/` with no injection point

**Checklist item:** "core/ purity in behavior … no `datetime.now()` where injection is expected."

**Evidence.** `.importlinter:17-27` documents the convention: every `core/` module must be
side-effect free and deterministic. `src/core/trade_preview.py` violates it twice:

- line 366 (`build_structure`): `expiry = (datetime.now(UTC) + timedelta(days=dte)).date().isoformat()`
- line 488 (`build_from_legs`): `now = datetime.now(UTC)` then per-leg expiry derivation.

Neither function accepts a `now`/`as_of` parameter, so identical inputs produce different
expiries across midnight — untestable with snapshot equivalence and inconsistent with the
rest of `core/` (the only two wall-clock reads in the package).

**Recommended fix.** Add `as_of: date | None = None` (default resolved by the *caller* in
api/engines) to `build_structure` and `build_from_legs`; thread it from the orchestration
layer. Grep-guard: a CI check (or ruff custom rule/vulture-style sweep) for
`datetime.now|utcnow|date.today` under `src/core/`.

---

## A6-05 (Medium) — `shared/config.py` is not the single env-var schema; two concrete drifts

**Checklist item:** "`shared/config.py` is truly the single env-var schema — no `os.environ`
reads scattered elsewhere."

**Evidence.** `shared/config.py` declares 8 fields (SERVICE_NAME, REDIS_URL, IB_*,
MARKET_SYMBOL, DATABASE_URL, LOG_LEVEL) and its docstring claims single-source-of-truth.
A sweep finds ~40 direct `os.environ`/`os.getenv` reads outside it, defining ~30
undeclared env vars, e.g.:

- `src/engines/execution/main.py:61-111` — 11 vars (SYNC_INTERVAL_S, STUCK_*, REAPER via
  `reaper.py:49-50`, IB_READ_ONLY, EXECUTION_USE_COMBO, REDIS_URL re-read, IB_* re-read…);
- `src/api/orchestration/position_monitor.py:630-632`, `position_close.py:44,176`,
  `pca_refit_scheduler.py:103-106` — POSITION_MONITOR_*, EXIT_AUTO_EXECUTE_ENABLED, PCA_REFIT_*;
- `src/api/main.py:148` FRED_API_KEY; `src/api/routers/dev.py:1489,1516` PROMETHEUS_URL,
  DOCKER_SOCK; `src/shared/market_hours.py:40` FORCE_RUN_MARKET_HOURS (read per-call);
- `src/engines/vol/chain_fetcher.py:46,53` generic `os.getenv` helpers.

Two concrete drifts this has already produced:

1. **Env-name split for the same endpoint.** The execution-engine base URL is read as
   `EXECUTION_URL` in `api/routers/orders.py:26`, `trades.py:31`, `positions.py:1244`
   but as `EXECUTION_ENGINE_URL` in `api/routers/trade.py:225`,
   `api/orchestration/position_close.py:176`, `position_monitor.py:165`.
   `docker-compose.yml:74` sets only `EXECUTION_URL` — the `EXECUTION_ENGINE_URL` readers
   work solely because the hardcoded fallback happens to match. Repointing the service via
   compose would silently affect only half the call sites.
2. **Same var, two defaults.** `PCA_SNAPSHOT_INTERVAL_MIN` defaults to `"55"` in
   `src/engines/vol/engine.py:1520` and `"60"` in `src/engines/analytics/engine.py:197`
   (interacts with A6-06).

**Recommended fix.** Fold the recurring vars into `shared.config.Settings` (or per-service
`Settings` subclasses like `api/config.py` already does), unify on one execution-URL name,
and keep raw `os.environ` reads only where a Settings import is impossible (alembic env,
Dockerfile healthchecks). A CI grep for `os.environ|os.getenv` outside `shared/`,
`persistence/migrations/` and Dockerfiles would hold the line.

---

## A6-06 (Medium) — `VOL_INLINE_ANALYTICS` staged cutover never completed: analytics computed twice

**Checklist item:** "Feature flags … that no longer have a future" / migration residue.

**Evidence.** The 6th engine (`src/engines/analytics/`, "standalone 6th engine (no IB)" per
its own docstring) was extracted precisely to take the heavy hourly batch (PCA surface
snapshot, GMM refit, PC3 history) off the vol-engine's real-time path. But
`src/engines/vol/engine.py:56-63` keeps the legacy inline path behind
`VOL_INLINE_ANALYTICS`, default **"1"**, with the comment "flip to '0' once analytics is
verified live" — and `docker-compose.yml:212` also defaults it on
(`VOL_INLINE_ANALYTICS: ${VOL_INLINE_ANALYTICS:-1}`). The analytics container runs
unconditionally (compose `analytics-engine`, profile `engines`).

Consequences while both run: the GMM refit, PCA snapshot and PC3 recompute execute in two
containers every hour; both writers gate on the last `SurfaceSnapshotHourly` timestamp but
with different gap defaults (55 vs 60 min, see A6-05) and a read-then-write race window, so
occasional duplicate/off-cadence snapshot rows are possible; and the vol-engine still burns
real-time-path CPU the extraction was meant to remove.

**Recommended fix.** Verify analytics-engine output parity (it has been live), flip the
compose default to `0`, and after a release cycle delete the inline path + flag from
`vol/engine.py` entirely (it is read once at import, so a restart applies it).

---

## A6-07 (Medium) — Container/module docs do not match reality (analytics engine invisible)

**Checklist item:** "Container/module boundaries in docs match reality."

**Evidence.**

- `README.md:82` — "**11-container core stack** (6 ship our Python code)". Actual
  `docker-compose.yml` core services: postgres, redis, api, market-data, vol-engine,
  risk-engine, db-writer, **analytics-engine**, execution-engine, frontend, ib-gateway,
  nginx = **12** services, **7** shipping our Python code.
- `README.md:127-139` container table and the ASCII diagram (86-125) omit
  `analytics-engine` entirely; `README.md:142` — "The **5** Python engines live behind the
  `engines` compose profile" (there are 6 on that profile).
- `CLAUDE.md` §Architecture: same "11 core containers, 6 ship our Python code" claim, a
  5-engine table, and a source-layout tree listing `engines/` with 5 subpackages —
  `src/engines/analytics/` (engine.py + main.py + Dockerfile, metrics port 9106) is absent.
- `.importlinter:73` contract-4 comment enumerates "(market_data, vol, risk, db_writer,
  execution)" — analytics missing (the *contract* still covers it; only the prose is stale).
- `README.md:146` — "SQLAlchemy 2 ORM (**27** classes)"; actual `src/persistence/models.py`
  has **28** `class` statements (CLAUDE.md says 28).
- `CLAUDE.md` §Stack commands references `scripts/ops/load_secrets.ps1` / `scripts/ops/stack.ps1`
  and later `scripts/up.ps1` / `down.ps1`; the actual tree has only `scripts/local/`
  (load_secrets.ps1, stack.ps1) and `scripts/aws/`.

**Recommended fix.** One docs pass: add analytics-engine to README table + diagram +
CLAUDE.md table/tree, fix the 11→12 / 5→6 / 6→7 / 27→28 counts, update the
`.importlinter` comment, and normalize the script paths in CLAUDE.md.

---

## A6-08 (Medium) — Two logging stacks; stdlib path drops structured fields

**Checklist item:** "Logging: structlog everywhere, consistent event naming, no `print()`."

**Evidence.** `print()` — zero occurrences in `src/` (pass). But
`shared/logging.py:configure_logging` wires **structlog** (JSON renderer, contextvars
`service_name`) *and* stdlib `logging.basicConfig(format="%(message)s")` side by side —
and 41 modules across api/engines/persistence/shared take the stdlib path via
`logging.getLogger(__name__)` (e.g. `engines/vol/engine.py:44`, `engines/execution/*`,
`api/routers/trade.py:62`, `api/ws/*`), versus ~10 modules using structlog. Effects:

- stdlib lines are plain text: no JSON, no timestamp, no `service_name`, no `cycle_id`
  (structlog contextvars don't propagate to stdlib records) — undermining the stated Loki
  "filter without regex-parsing" design and `observed_cycle`'s "logs filterable via jq
  cycle_id" criterion for any line emitted between the structlog cycle markers;
- `extra={...}` payloads are **silently dropped** by the `%(message)s` format — e.g.
  `logger.info("vol_engine_started", extra={"symbol": self.symbol})`
  (`engines/vol/engine.py:530`) loses the symbol; same pattern in market_data/risk engines.

**Recommended fix.** Either (a) migrate the 41 modules to `structlog.get_logger()` (mostly
mechanical), or (b) bridge stdlib through `structlog.stdlib.ProcessorFormatter` in
`configure_logging` so both paths render the same JSON with contextvars merged. (b) is one
file and immediately fixes the dropped-`extra` problem; (a) can follow incrementally.

---

## A6-09 (Medium) — mypy enforcement gap: three packages already clean, nothing enforced

**Evidence.** CI (`.github/workflows/ci.yml:62-64`) runs `python -m mypy` with
`continue-on-error`-style advisory semantics ("Type check (mypy, advisory)"). Measured
per-package counts (mypy 1.x, config from `pyproject.toml`, 2026-08-01):

| Package | Files checked | Errors | Status |
|---|---|---|---|
| `shared/` | 10 | **0** | clean — enforceable today |
| `bus/` | 4 | **0** | clean — enforceable today |
| `persistence/` | 61 | **0** | clean — enforceable today |
| `core/` | 51 | **12** (6 files) | near-clean |
| `api/` | 58 | **68** (13 files) | worst: `portfolio_panel.py` (22), `trade.py` (16), `dev.py` (8), `cockpit.py` (7) |
| `engines/` | 33 | **95** (14 files) | worst: `execution/live_submit.py` (34), `vol/engine.py` (26), `execution/rollback_runner.py` (17) |
| **Total** | 217 | **175** | |

The 12 `core/` errors are small and real (e.g. `trade_preview.py:512,535` passes a bare
`str` where `Literal["BUY","SELL"]` is expected — the very validation the Literal exists
for; `vol/tenors.py:146` `int | None` arithmetic; `vol/garch.py:46` `"Garch"` vs the arch
library's `"GARCH"` literal). Meanwhile three packages sit at zero errors with no CI gate
holding them there — regressions cost nothing today.

**Recommended fix.** See Appendix B roadmap: gate `shared`+`bus`+`persistence`
immediately (zero work), fix the 12 `core/` errors and gate it next, then burn down
`api`/`engines` file-by-file.

---

## A6-10 (Medium) — Business logic concentrated in oversized routers

**Evidence.** `api/orchestration/` exists as the use-case layer (per CLAUDE.md: "use-case
orchestration (was: api/services/)"), yet the four biggest files in the api are routers:
`portfolio_panel.py` (2306 lines), `dev.py` (1612), `trade.py` (1377), `positions.py`
(1354) — together more lines than the whole `core/vol/` package. They hold non-HTTP logic:
close-order construction and marketable-limit math (`positions.py:964-970`,
`trade.py:70`), ledger folding calls, Redis cache protocols, module-level env/config
constants (see A6-05). `trade.py` and `positions.py` are also the top mypy offenders in
api (A6-09). This is maintainability erosion, not a boundary break (import-linter can't
see it).

**Recommended fix.** No big-bang rewrite; adopt a ratchet: new endpoint logic goes into
`api/orchestration/*` modules, and each substantive change to one of the four routers moves
the touched block out. Candidate first extractions: the close-order pipeline shared by
`positions.py`/`trade.py`, and the portfolio aggregation helpers of `portfolio_panel.py`.

---

## A6-11 (Low) — Engine error-handling policy is coherent but undocumented; execution tasks unsupervised

**Checklist item:** "Error-handling policy consistent: catch-and-log vs crash-and-restart —
one deliberate policy, not per-file accident."

**Evidence.** Two policies coexist, split by engine style, and are *internally* consistent:

- **Cyclic engines** (market_data, vol, risk, analytics): `while not self._stop` loops with
  per-stage try/except (prune, hooks, publish) but **no top-level catch-all** around
  `run_cycle()` — an unexpected exception exits `run()`, the process dies, and
  `restart: unless-stopped` (docker-compose, all services) restarts it. Crash-and-restart.
- **Execution engine sub-loops** (`reaper.py:281`, `reconciler.py:121`,
  `order_reconciler.py:158`, `ib_heartbeat.py:95,161`, `position_sync.py:545`):
  `while True: try … except Exception: log` — catch-and-continue, never crash.

Neither choice is written down anywhere, and the execution engine's 7 background loops are
started with bare `asyncio.create_task(...)` (`main.py:139-183`) with no done-callback:
if a loop dies through the one unguarded path (an exception raised inside its own `except`
handler, or a bug before the `try`), the container stays "healthy" while e.g. the reaper is
silently gone until shutdown.

**Recommended fix.** Document the two-policy rule (cyclic = crash-restart, execution
sub-loops = catch-continue) in `docs/architecture/` or the engines' package docstring; add
`task.add_done_callback` logging (or a small supervisor that flips the health state) to the
execution engine's `create_task` calls.

---

## A6-12 (Low) — Stale docstrings, dangling doc references, deprecated API

**Evidence** (all confirmed against current code):

- `src/engines/vol/main.py:3-7` — "SANDBOX NOTE (sandbox/r9-pipeline-verif) … `fetch_ohlc`
  remains a synthetic stub until the historical bar fetch is ported." The same file wires
  the real `engines.vol.historical_fetcher.fetch_daily_ohlc` at line 123.
- `src/api/main.py:130` — "The 6 other sources are stubbed in `sources/stubs.py`" — no
  `stubs.py` exists; `ecb.py`, `boe.py`, `fomc.py`, `eurostat.py`, `ons.py` are real modules.
- `src/api/orchestration/events/sources/__init__.py:8-15` — lists those same shipped
  sources as `[TODO]`.
- `src/api/routers/positions.py:1319` references `MARKETS_OPEN_TODO.md`;
  `src/api/routers/regime.py:226` references `TODO.md` — neither file exists in the repo.
- `src/api/routers/regime.py:201` — `datetime.utcnow()` (naive, deprecated since 3.12);
  sole occurrence in `src/`, everything else uses `datetime.now(UTC)`.

**Recommended fix.** One sweep commit: rewrite/delete the four stale notes, repoint or drop
the dead doc references, replace the lone `utcnow()`.

---

## A6-13 (Low) — `shared/` scope and the unguarded `core → shared` edge

**Checklist item:** "`shared/` hasn't become a dumping ground."

**Evidence.** `shared/` is mostly genuine cross-cutting infra (config, logging, backoff,
ib_connection, db_events, observability, tracing). Three nuances:

- `shared/contracts.py` (130 lines) — contract multipliers (EUR 125 000 / M6E 12 500),
  localSymbol/expiry parsing: **domain metadata** that risk math depends on (a wrong
  multiplier is a 10× greeks error). It is pure Python (`re`, `dataclass`, `date`) and
  would fit `core/` naturally; it sits in `shared/` presumably because core "imports
  nothing". Note that `.importlinter` contract 1 forbids core → {api, engines, bus,
  persistence} but **not** core → shared — so the pure layer's isolation from
  infrastructure on that edge is convention-only today (currently upheld: zero core→shared
  imports; `core/trade_preview.py:288` references it only in a comment).
- `shared/market_hours.py` — trading-calendar domain logic plus a per-call
  `FORCE_RUN_MARKET_HOURS` env read (line 40), i.e. hidden config outside the schema (A6-05).
- `shared/trace.py` (correlation id) vs `shared/tracing.py` (OTel) — two near-identical
  names for unrelated concerns; a rename (`correlation.py`?) would prevent wrong-import
  accidents.

**Recommended fix.** Move `contracts.py` (and arguably the pure part of `market_hours.py`)
into `core/`; add `shared` to contract 1's forbidden list at that point (or add a dedicated
"core does not import shared" contract now); rename `trace.py`.

---

## A6-14 (Low) — `core/` typing convention drift and loggers in pure functions

**Evidence.** `.importlinter:21` sets the convention "typed explicitly (no Any, no implicit
Optional)" for `core/`. Current state: 49 `Any` annotations across `core/` (9 flagged by
ruff ANN401 as explicit `Any` parameters), 3 blind `except` (BLE001), and stdlib loggers
emitting warnings from inside pure computation in `core/vol/har_rv.py:35`,
`ssvi.py:29,108`, `vrp.py:22,57`. Logging is I/O-adjacent but tolerable for diagnostics;
`Any`-typed surface dicts (`dict[str, Any]` for the vol surface) are the main source and
are a real typing-debt item, since the same shape is re-validated ad hoc at every consumer.

**Recommended fix.** Introduce a `TypedDict`/dataclass for the surface payload in
`core/vol/` (one definition, reused by api/engines), then tighten `core/` toward
`mypy --strict` per the Appendix B roadmap. Not a gate for v1.0.

---

## Verified clean (no finding)

- **Import contracts:** `lint-imports` — 5/5 kept (225 files, 349 dependencies).
- **Ruff (configured set):** `python -m ruff check src tests` — All checks passed.
- **Dead code / migration residue:** no references to `api/models`, `api/services`,
  `src/services`, `redis_client`, `db_queue`; no Qt/desktop imports; `tests/old` removed
  (pyproject `testpaths` pinned to `tests/unit` + `tests/integration`, CI has no `tests/old`
  reference); all suspected orphan modules (`core/regime_patterns.py`, `core/ledger.py`,
  `core/trade_preview_regime.py`, `core/config_catalog.py`, `shared/backoff.py`,
  `shared/db_events.py`) have live importers + tests.
- **Core hidden I/O:** no `open()`, network, `os.environ`, or RNG in `core/` (GMM fits use
  a fixed `random_state` — `core/vol/gmm_regime.py:70`); the only wall-clock reads are A6-04.
- **Pydantic:** uniformly v2 (`model_config` / `ConfigDict` / `model_validate`); no
  `class Config`, `@validator`, `parse_obj`, or `orm_mode` anywhere in `src/`.
- **Async hygiene in api/engines:** CPU-heavy work is offloaded (`asyncio.to_thread` for
  PCA in `api/routers/signals.py:246,407`, SVI fits in `engines/vol/engine.py:838`, GMM in
  `engines/analytics/engine.py:264`); api I/O is async end-to-end (aioredis, httpx
  AsyncClient incl. the docker-socket client in dev.py); no sync IB wrapper use in api; the
  only sync scientific call on the loop is a scipy `brentq` root-find in
  `api/orchestration/pricing_service.py:58` (micro-CPU, acceptable).
- **Repo hygiene:** no tracked `__pycache__`/`.pyc`; `src/fxvol.egg-info` git-ignored;
  16 routers on disk = the 16 documented.

---

## Appendix A — Metrics baseline (for v1.x comparison)

- import-linter: 225 files, 349 dependencies, 5/5 contracts kept.
- mypy total errors: **175** (core 12 · api 68 · engines 95 · shared/bus/persistence 0).
- ruff configured set: 0 violations. `--select ALL` survey (top signals, `src/` only):
  ANN401 ×109, BLE001 (blind except) ×69, PLR2004 (magic values) ×167, D1xx docstring gaps
  ×311, N803/N806 (quant naming — acceptable) ×129. TODO/FIXME markers in `src/`: 9.
- Largest modules: `api/routers/portfolio_panel.py` 2306, `dev.py` 1612, `trade.py` 1377,
  `positions.py` 1354; `persistence/models.py` 28 ORM classes; 54 alembic revisions.

## Appendix B — mypy enforcement roadmap

| Stage | Packages | Preconditions | CI change |
|---|---|---|---|
| **1 (now)** | `shared/`, `bus/`, `persistence/` | none — already 0 errors | make `python -m mypy src/shared src/bus src/persistence` a required check (drop advisory for these paths) |
| **2 (next PR-sized fix)** | `core/` | fix the 12 errors (Literal side types in `trade_preview.py`, `tenors.py` Optional arithmetic, `garch.py`/`gmm_regime.py`/`regime_engine.py`/`fair_term.py` annotations) | add `src/core` to the required run |
| **3 (per-file ratchet)** | `api/` | burn down 68 errors starting with `portfolio_panel.py` (22) and `trade.py` (16) — pairs naturally with the A6-10 router extractions | maintain an explicit clean-file list (mypy `files=` or a `# strict` module allowlist) that only grows |
| **4 (per-file ratchet)** | `engines/` | burn down 95 errors starting with `execution/live_submit.py` (34) and `vol/engine.py` (26) — order-path files first (money-adjacent) | same ratchet mechanism |
| **5 (stretch)** | `core/` strict | after A6-14 surface typing | flip `core/` to `mypy --strict` per the original pyproject comment |

Stages 1-2 are cheap and should land before v1.0; stages 3-5 are v1.x ratchets.
