# Audit 11 — Documentation & Release Readiness — Findings

**Run date:** 2026-08-01 · **Tree:** `main` (local), clean, 5 commits ahead of `origin/main`
**Mode:** findings only — no file outside `docs/audits/reports/` was modified, no git state
changed, no stack started, no live deployment contacted, no `gh` write command run.

| Severity | Count |
|---|---|
| **Blocker** | 2 |
| **High** | 15 |
| **Medium** | 27 |
| **Low** | 14 |
| **Total** | **58** |

The release-gate deliverable is `RELEASE-GATE-v1.0.md` (same folder).

---

## 0. Scope note — this audit ran out of order

`../README.md:40-44` places Audit 11 in Wave 3, *after* Wave 1–2 fixes land, because
"it verifies the post-fix state". It was run instead against the **pre-fix** tree, with
10 Blockers and 47 Highs from Audits 1–10 open.

Consequences, stated plainly:

- **No readiness claim is made anywhere in this report.** The release checklist is
  written as a gate document with each item's honest status, not as a sign-off.
- **Docs-vs-reality checks are unaffected** and were performed in full — a doc that
  contradicts the code contradicts it regardless of whether the code is about to change.
- **A material subset of these findings will need re-verification after the fixes land**,
  because the fixes will change what the docs *should* say. Flagged per finding as
  `[re-verify after fixes]`.
- The fresh-machine setup was performed as a **desk check** (every documented command,
  path, script and prerequisite verified to exist and be correct) rather than by executing
  the stack, per the run constraints. The blocking gap found (D-05) is structural and
  would not have been resolved by executing it.

**Read-only verifications actually executed:** `ruff check src tests` (clean),
`lint-imports` (5 contracts kept, 0 broken, 225 files), `pytest --collect-only`
(809 tests), `git tag`/`ls-remote`, `gh pr list`/`issue list`, OpenAPI regeneration +
`schema.d.ts` diff.

---

## 1. Verified clean — worth recording

Not everything drifted. These were checked and hold:

- **No pre-rename or desktop-app residue anywhere.** Grepped all 17 primary docs plus
  `src/` for `src/services/`, `api/models/`, `api/services/`, `redis_client.py`,
  `db_queue.py`, `requirements.txt`, `pytest.ini`, `ruff.toml`, Qt/PyQt/PySide/desktop.
  **Zero hits.** The single Qt mention (`src/engines/vol/chain_fetcher.py:29`) is
  correctly in the past tense. The R8 removal and the v2 rename were completed cleanly.
- **`docs/architecture/database.md` is exact.** All 27 documented `Class → table` pairs
  match `src/persistence/models.py`, including the non-obvious renames
  (`IbConnectionState → runtime_ib_session`, `SurfaceSnapshotHourly →
  pca_surface_snapshot_history`, `AppConfigScalar → config_scalar`). The migration-history
  claims at `:86-88` check out. Best doc in the repo.
- **`docs/vol-modeling/` (all four files) is exact.** Every documented model, constant,
  threshold, gate order and formula was verified line-for-line against `src/core/vol/`:
  Yang-Zhang `k`, HAR lags 5/22, GARCH blend 0.50 / `emp_kappa` 2.0, the VRP
  regime×tenor table, PCA `MIN_VARIANCE_EXPLAINED`/`MIN_N_OBS_HARD`/`MIN_CUMULATIVE_VARIANCE`,
  the 7 actionable gates in order, the skew/convexity column arithmetic, `gate_decision`'s
  `{0.0,0.5,0.7,1.0}` table, GMM `reg_covar=0.5`/`random_state=42`, the SVI seed vector,
  SSVI bounds, `LISTED_TOLERANCE_DAYS=12`. **No documented model or parameter is missing
  from the code.** This is the standard the rest of `docs/` should be held to.
- **Architecture contracts hold** — `lint-imports` reports 5 kept / 0 broken.
- **`schema.d.ts` drift gate is genuinely green** — regenerating `openapi.json` from
  `create_app()` and re-running `openapi-typescript` reproduces the committed
  `schema.d.ts` byte-identically. CI would pass.
- **CLAUDE.md's CI job table matches `ci.yml`** exactly (8 jobs, correct names).
- **Only one dangling markdown link** across the whole `docs/` tree (D-11) — link hygiene
  is otherwise good.
- **No secret value appears in any file read during this audit.** No `.env` exists in the
  repo. Both loader scripts and `remote-deploy.sh` contain parameter *names* only.
- **All 15 screenshots were captured 2026-08-01** — none is stale relative to the UI.

---

## 2. Blockers

### D-01 — The documentation asserts enforced risk limits that do not exist (Blocker)

`docs/strategy/risk.md`, `greek-limits-spec.md`, `docs/strategy/signals-to-trades.md`,
`src/core/risk/greek_limits.py:75`, `src/api/routers/portfolio_panel.py:2232`

The docs describe a bounded book. The code computes numbers and discards them.

What the docs promise, in enforcement language:

- `docs/strategy/risk.md:1` — title: *"Risk: greeks, **limits**, VaR, attribution, hedging"*
- `docs/strategy/risk.md:3-5` — *"turns a book of option legs into a small set of risk
  axes, **caps each axis against a stress-loss budget**"*
- `greek-limits-spec.md:93` — *"**Enforce** per-bucket caps AND a total parallel-shift cap.
  Add explicit risk-reversal (vanna) and butterfly (volga) exposure **limits**"*
- `greek-limits-spec.md:109` — *"`# Hard limit: worst_cell(book) >= -L*`"*
- `greek-limits-spec.md:112` — *"if standalone caps are all at 100% but the grid says
  -1.5·L*, **the grid binds first**"*
- `greek-limits-spec.md:146` — *"[ ] `delta_rehedge_trigger < delta_cap_usd` **enforced**"*
- `docs/strategy/signals-to-trades.md:68-69` — *"**capped at `max_loss_pct_capital`
  (2% of capital)**"*

What exists:

- `compute_caps` has **one** production caller — `GET /portfolio/greek-limits`
  (`portfolio_panel.py:2232`) — and that endpoint has **no frontend consumer at all**.
  The only `frontend/src/` hits for `greek-limits` are the generated `schema.d.ts` and
  three *static label strings* in a dev-tools diagram (`pages/dev/pipelines.ts:791,806,822`).
  No `greekLimits`/`delta_cap_usd`/`vega_cap_usd` reference in `endpoints.ts`, `hooks/`,
  or `RiskView.tsx`. The caps are computed, serialised, and dropped on the floor.
- `src/engines/risk/engine.py` contains **zero** occurrences of `cap` or `limit`.
- No breach state, no alert, no blocking, no scenario grid (§5, declared by the spec as
  *the* master limit), no per-tenor vega ladder, no vanna/volga limits, no utilisation
  computation server- or client-side.
- The `max_loss_pct_capital` cap was **explicitly deleted** —
  `src/core/trade_preview.py:885-888`: `# max_loss_under_capital_limit + iv_data_fresh
  removed by request — the first was too punitive…`. The doc still asserts it.
- Of the spec's 10 acceptance criteria (`greek-limits-spec.md:143-152`), only the first
  three — the formula-shape ones — are met.

**Why Blocker rather than High.** The severity scale reserves Blocker for "can lose money
in normal operation". A docs finding qualifies when the document *is* the operator's model
of the system. Audits 3 and 4 independently found there is no structural ceiling on
exposure (R3-08, EX-08, EX-09, EX-10, R3-03). This finding is why nobody noticed: the
written record says the ceiling is there. An operator sizing a position against
`risk.md`'s "caps each axis against a stress-loss budget" is reasoning about a control
that has never existed. That is a money-loss path created by documentation.

Aggravating: the one gate that *is* enforced — `vega_under_book_limit` against
`max_book_vega_usd`, `trade_preview.py:891-894` — belongs to an **entirely separate,
undocumented limit system** (`config_scalar` namespace `risk`). Neither `risk.md` nor
`greek-limits-spec.md` mentions `max_book_vega_usd`, `run_pre_submit_checks`, or
`apply_regime_to_limits`; conversely nothing in the enforced path reads `GreekCaps`. The
documented system and the real system do not intersect.

Also: `max_n_open_structures` and `max_book_vega_per_tenor_usd` are declared in
`config_catalog.py:45,50`, regime-scaled in `trade_preview_regime.py:32-33`, editable in
the Settings UI — and read by **no check anywhere**.

`[re-verify after fixes]` — the correct resolution is to implement the controls, not to
delete the docs. Re-check once Theme B lands.

---

### D-02 — Delayed market data is documented and published as "live" / "real-time" (Blocker)

`README.md:5,21,51`, `docs/execution/ib-integration.md:46-49`,
`src/engines/market_data/main.py:130`, `src/engines/vol/chain_fetcher.py:123`,
`src/engines/execution/order_executor.py:130`

The system requests **delayed** IB market data everywhere:

- `market_data/main.py:130` — `ib.reqMarketDataType(3)` (delayed, ~15–20 min)
- `vol/chain_fetcher.py:123-124` — `reqMarketDataType(3)`, logged as
  `"delayed market data enabled"`
- `execution/order_executor.py:130` — `reqMarketDataType(4)` (delayed-**frozen**)

The documentation says the opposite, in the three most-read lines of the repo:

- `README.md:5` — *"turns a **live** Interactive Brokers feed into research-grade
  volatility signals… serves it all through a **real-time** web desk"*
- `README.md:21` — *"**live** IB tick stream"*
- `README.md:51` — *"**Real-time** tick stream published on Redis (throttled ~200 ms)"*

The only accurate disclosure in the entire repo is
`docs/execution/ib-integration.md:46-49` — four lines, five directories deep, in the
execution section.

This is published, not just written. The public deployment at
`valeriandarmente.dev/fx-volatility-trading-system` renders these prices as 5-decimal
mids with a green freshness dot and no disclaimer of any kind. Compounding it, Audit 9
found the frontend discards the backend's real data timestamps and re-stamps with
`Date.now()` (FE-01), computes freshness once at fetch (FE-02), and asserts "Market open"
from a never-expiring dict (FE-03) — so on nights and weekends the site shows Friday's
close, twenty minutes stale on top of that, labelled live.

**Why Blocker.** Three converging reasons: (1) anyone reading a price off the public site
is reading a number the documentation told them was real-time and is not; (2) it directly
aggravates DEP-04 — the redistribution terms that apply are strictest for the
entitlement class the docs claim; (3) unlike FE-01/02/03, which are UI defects, this one
is a factual assertion in the project's own README, which is what a reader trusts when the
UI looks plausible.

Minimum fix: correct README's three claims, add L2 to a known-limitations section, and
surface a "delayed data" marker in the UI. Resolving §8.1 of the gate document may change
what can be served at all.

---

## 3. High

### D-03 — `analytics-engine` is invisible: 6th engine, in prod, in no document, in no health probe

A sixth first-party engine exists, is built by CI, ships to production, and is monitored
by nothing:

- **Exists:** `src/engines/analytics/{Dockerfile,__init__.py,engine.py,main.py}` +
  `tests/unit/engines/analytics/`
- **Runs:** `docker-compose.yml:324-374`, container `fxvol-analytics`, `profiles:
  ["engines"]` — so it starts on **every** `--profile engines up`, unconditionally. The
  only service in the stack with a CPU cap (`cpus: 0.5`).
- **Ships:** `build.yml:139-140` (6-entry engine matrix), `remote-deploy.sh:69`
  (`ANALYTICS_ENGINE_IMAGE`)
- **Scraped:** `obs/prometheus.yml:21` → `analytics-engine:9106`

Absent from **every** document and **every** runtime registry:

| Surface | Says | Reality |
|---|---|---|
| `README.md:82` | "11-container core stack (6 ship our Python code)" | 12 / 7 |
| `README.md:86-125` | ASCII diagram | no node |
| `README.md:127-139` | container table, 11 rows | no row |
| `README.md:142`, `:252` | "The 5 Python engines" | 6 |
| `README.md:207` | "7 images → GHCR" | 8 |
| `CLAUDE.md:118`, `:96`, `:120-127`, `:155-161` | 11 core / 6 Python / 5 engines | 12 / 7 / 6 |
| `docs/README.md:5,20` | "five async Python engines", 11/6 | 6, 12/7 |
| `docs/architecture/overview.md:9,24,27-39,41,43` | five engines, 11 containers, 11-row table, alt-text | 6, 12 |
| `docs/architecture/backend.md:17` | "5 long-running services" + list | 6 |
| `docs/architecture/data-flow.md:30-38` | Redis channel/producer table | its publications undocumented |
| `docs/ops/local-stack.md:17,31,38` | "the 5 engines" ×3 | 6 |
| `docs/ops/deployment.md:17,20-21,59` | "seven images" ×2, five-engine list | 8, 6 |
| `docs/observability/conventions.md:45,61` | `engine` label set, scrape targets | `analytics_engine` missing |
| all 12 `docs/diagrams/*.svg` | — | **zero occurrences of "analytics"** |
| `.importlinter:73` | contract-4 comment lists 5 engines | comment only; the contract is package-level so it *is* covered |

**Worse than a docs gap — it is operationally invisible:**

- `src/api/routers/health.py:24` — `ENGINES = (MARKET_DATA, VOL, RISK)`. The readiness
  probe monitors **3 of 6** engines. `db_writer`, `execution` and `analytics` can all be
  dead while `/health/extended` reports `OK`.
- `src/api/routers/dev.py:58-61,120-125` — `_DEV_KEYS` and `ENGINES_CONFIG` list 5
  engines. The `/dev` console never shows analytics.
- `frontend/src/voldesk/data/live/system.ts:26-32` (`ENGINE_META`) and
  `frontend/src/pages/dev/StackOverview.tsx:72-95` — 5 engines, 16 nodes, no analytics.
- `src/api/orchestration/analytics_service.py:27-29` — `_MONITORED_ENGINES` = 3.
- `src/bus/keys.py:104` **does** define `ENGINE_ANALYTICS = "analytics_engine"` — the key
  exists and nothing consumes it.

`docs/pictures/architecture.png`, captioned *"the 11 services healthy"*, is a capture of
that same blind `/dev` console — so the screenshot is simultaneously current with the
shipped UI and wrong about the system.

### D-04 — `VOL_INLINE_ANALYTICS` cutover residue: prod computes the same batch twice

`docker-compose.yml:209-212`, `src/engines/vol/engine.py:63`, `infrastructure/ec2/remote-deploy.sh`

The analytics extraction was staged behind a flag and the cutover never happened:

- `docker-compose.yml:212` — `VOL_INLINE_ANALYTICS: ${VOL_INLINE_ANALYTICS:-1}` on
  `vol-engine`; the comment at `:209-211` says `1` (default) *"keeps the legacy inline
  GMM/snapshot/PC3; flip to `0` to serve the analytics engine's published results"*.
- `src/engines/vol/engine.py:63` — `_INLINE_ANALYTICS = os.environ.get(
  "VOL_INLINE_ANALYTICS", "1") != "0"`, read **at module import**, so it is not
  hot-reloadable.
- `remote-deploy.sh` **never sets it** → prod runs the `1` default.
- `analytics-engine` runs unconditionally on the same `engines` profile.

So in production both paths compute the hourly GMM refit + PCA surface snapshot + PC3
history, on different schedules (`PCA_SNAPSHOT_INTERVAL_MIN=55` on vol-engine vs
`ANALYTICS_INTERVAL_MIN=60` on analytics, `docker-compose.yml:193` and `:347-348`).
Audit 5 already traced the consequence: duplicate hourly `pca_surface_snapshot_history`
rows (DI-08, DI-26).

The flag, its default, its cutover state, and the fact that a whole container's output is
currently ignored appear in **no** document — zero hits in `README.md`, `CLAUDE.md`,
`docs/` (outside `docs/audits/`), `frontend/`, `infrastructure/`, `scripts/`. The only
test of the `=0` branch is `tests/unit/engines/vol/test_analytics_read_path.py`.

### D-05 — There is no fresh-machine setup path; a stranger cannot start this system

The audit's central completeness question — *"clone → env setup → local stack up → tests
green, following only written docs"* — fails structurally, not for want of detail.

Desk check of `README.md:173-197` + `docs/ops/local-stack.md`:

| Step | Documented | Verified |
|---|---|---|
| `python -m venv .venv` | ✅ | works |
| `pip install -e ".[dev,api,quant,ib,writer]"` | ✅ | all 5 extras exist in `pyproject.toml:40-80` |
| `. .\scripts\local\load_secrets.ps1` | ✅ path exists | **unusable by anyone else** |
| `.\scripts\local\stack.ps1` | ✅ path exists | **cannot proceed** |

The blocking chain:

1. `docker compose` interpolates `${DB_PASSWORD:?DB_PASSWORD is required}`
   (`docker-compose.yml:20`) at **parse time** — the stack will not even parse without it.
2. The only documented source of that value is `load_secrets.ps1`, which reads
   `/fxvol/prod/*` from **one private AWS account** and requires an authenticated
   `fxvol-dev` profile (`scripts/local/README.md`: *"Needs an authenticated AWS profile"*).
3. `stack.ps1:310` hard-requires the `aws` CLI: `throw "Missing required tool"`.
4. There is **no `.env.example`**, no default-secrets path, no "for local development
   without AWS, do X" section anywhere in the repo.
5. There is no documented way to produce `AUTH_PASSWORD_HASH` / `AUTH_SALT` — the
   pbkdf2 parameters are described only in `docs/audits/reports/01-security-findings.md`.

Compounding, the prerequisites list is incomplete. `README.md:175` says *"Docker Desktop
+ Python 3.11 + Node 20"*. Actually required and undocumented: the **AWS CLI**, an **AWS
account with 11 provisioned SSM parameters**, an **IB paper account** (`IB_USERID` /
`IB_PASSWORD` with no signup instructions), **WSL2** (implicit in
`stack.ps1`'s `-Refresh`), and **git** (`stack.ps1:310`).

**Tribal knowledge required to get to a running stack** (none of it written down):
which AWS account and profile; that the SSM parameters must be created by hand in the
console (`load_secrets.ps1:74` throws pointing at a console URL, with no schema of what to
create); how to compute the auth hash; that an IB paper account and a Gateway login are
needed for any engine to become healthy; that `docker compose up` alone yields a core
stack with no data.

This is the finding that answers "could a stranger stand this up unaided": **no**, and not
because of a missing paragraph.

### D-06 — No incident runbooks exist, and the one real runbook has never been executed

`docs/observability/runbooks.md` is named for runbooks and contains none. Its eight
sections are *"Bring the stack up"*, *"Loki — query logs"*, *"Prometheus — metrics"*,
*"Tempo — traces"*, *"Grafana — dashboards"*, *"The /dev Hardware tab"*, *"Common tasks"*,
*"Retention"*. Every entry under "Common tasks" (`:104-110`) is a **query**, not a
remediation.

Missing, all of them:

| Scenario | Status | Prior real incident? |
|---|---|---|
| Host reboot / stack restart | missing | — |
| Deploy rollback | missing here; a procedure exists only at `docs/ops/deployment.md:125-130` | — |
| DB restore | missing here; `infrastructure/ec2/RESTORE.md` exists, unlinked from `docs/` | — |
| IB session recovery | missing — only *"Is IB connected?"* as a PromQL query (`:109`) | **yes, three times**: gnzsnz volume version drift, single-session kick, watchdog exec-bit |
| Kill switch / halt trading | missing entirely — no doc anywhere covers the `READ_ONLY_API` flip as an emergency stop | — |
| Disk-full | missing | **yes** — root at 95%, `compose pull` stall, opaque SSM timeout |
| Engine stalled / crash-looping | detection only (`:107`), no remediation | yes |

Three of the seven have already happened in production. The recovery steps live in
`MEMORY.md` and in Valérian's head.

**And the one genuine runbook has never been run.** `infrastructure/ec2/RESTORE.md` is
good — nightly `pg_dump -Fc` to S3, pre-migration dump before every deploy, lifecycle
policy, five numbered restore steps, a schema-verification step. It opens with *"**An
untested backup is not a backup.** Run §3 once after the first deploy and record the date
at the bottom of this file."* The log at the bottom reads:

```
| Date | Dump | Result |
| _none yet — run once before go-live_ | | |
```

It is also unlinked from `docs/README.md` and from `runbooks.md`; only
`docs/ops/deployment.md:130` mentions it in passing. Its §5 records that the backup
freshness alarm is *"Not armed yet"*.

`[re-verify after fixes]` — Audit 8 is drafting a runbook and owns this gate.

### D-07 — Release mechanics cannot be executed as documented

`.github/WORKFLOW.md:84-87`, `pyproject.toml:7`, `src/api/main.py:168`,
`frontend/package.json:4`, `build.yml`, git tag state

Seven independent defects, any one of which blocks tagging:

1. **`v1.0` is already taken.** `git tag -l` shows 14 tags: `v1.0`, `v1.1.0`…`v1.12.0`,
   `oms-v1.0`. `v1.0` points at `4be1b54d` (2026-04-17, *"chore: ignore
   docs/COMMIT_METHODOLOGY.md"*). **13 of the 14 are not ancestors of `main`** — orphaned
   by the history rewrite. Creating `v1.0` requires `-f` and silently reuses a name that
   already means a different tree.
2. **No tags exist on the remote.** `git ls-remote --tags origin` returns **nothing**. All
   14 are local-only. GitHub shows zero tags and zero Releases across 218 merged PRs — so
   WORKFLOW.md §7's "closed by a `vX.Y.Z` tag on `main`" has never actually happened.
3. **Three conflicting version numbers, none matching any tag:** `pyproject.toml:7` =
   `0.1.0`; `src/api/main.py:168` FastAPI `version="0.4.0"` (this is what the public
   OpenAPI advertises); `frontend/package.json:4` = `0.1.0`.
4. **No CHANGELOG** — no `CHANGELOG*` anywhere in the repo.
5. **No versioning policy.** WORKFLOW.md §7 is two bullets. No semver/calver choice, no
   definition of what bumps major/minor/patch, no mapping between the board's
   `R11/R12/R13` release buckets and `vX.Y.Z`.
6. **Tags do not deploy and do not build.** `build.yml:10-27` triggers on `push:
   branches: [main]` and `workflow_dispatch` — **no `tags:` trigger**. `deploy.yml`
   chains off `build-and-push` on `main`. Pushing `v1.0` produces no image and no deploy.
   The tag-vs-main behavior the audit asks to be "decided" is currently decided by
   omission: tags are inert.
7. **No release procedure is written down** — nothing describes creating a GitHub Release,
   generating notes, or a tag → verify → announce sequence.

### D-08 — A running deployment cannot identify itself

`src/api/routers/health.py:28-31`, `deploy.yml:158-166`, `frontend/`

`GET /api/v1/health` returns exactly `{"status": "OK"}`. `/health/extended` adds Redis /
DB / engine components and a timestamp — no version, no commit SHA, no build time.

- **No version in the API response.** The only version FastAPI knows is the stale
  `"0.4.0"` in the OpenAPI `info` block, which is a constant in source, not a build stamp.
- **No version in the UI.** No footer, no about panel; grep across
  `frontend/src/voldesk/` finds no version or build display of any kind.
- **The deploy smoke check does not verify identity.** `deploy.yml:158` — *"Smoke check
  … returns JSON"* — asserts only that the JSON parses. A deploy that silently served the
  previous image would pass.
- `IMAGE_TAG` (`sha-<commit>`) exists and is threaded correctly through
  `deploy.yml:103-129` → `remote-deploy.sh:62-69`. It reaches the container names and
  nothing else. It is never surfaced over HTTP.

Consequence: mid-incident, nothing served by the running system says which commit it is.
Determining it requires SSM access to the host and `docker inspect`. This is precisely the
"future-you at 2am" scenario the audit's *Why* section names.

### D-09 — No "known limitations" section exists, and README contradicts several of them

Grepped `README.md`, `CLAUDE.md`, `docs/**`, `SECURITY.md`, `frontend/README.md` for
paper-only / delayed / known-limitation / kill-switch / single-session language. The only
hits outside `docs/audits/` are `docs/execution/ib-integration.md:46-49,58`.

Ten limitations belong in that section; **eight are disclosed nowhere**, and four are
actively contradicted by README prose. The full table is §7 of `RELEASE-GATE-v1.0.md`.
Two deserve calling out here beyond D-02:

- **No alerting at all.** Grepped `obs/`, `docs/observability/`, `.github/workflows/` for
  alertmanager / alert rules / SNS / pager / notify: **zero hits**. `obs/` contains
  `prometheus.yml`, `loki.yml`, `promtail.yml`, `tempo.yml`, `otel-collector.yml`,
  `grafana/` — dashboards and collection, no rules and no notification path. Nothing pages
  anyone when an engine dies, when data goes stale, or when IB drops. `RESTORE.md:§5`
  confirms the backup-freshness alarm is *"Not armed yet"*. README:75-76 advertises
  *"Structured JSON logs, Prometheus metrics, OpenTelemetry traces, and an opt-in
  Grafana / Loki / Tempo observability stack"* — all true, and all pull-only.
- **No Python lockfile** (DEP-01) — undisclosed, while `README.md:161` presents
  `pyproject.toml` as *"single source of truth"*, which is true for declaration and false
  for resolution.

### D-10 — README claims `VolConfig` is hot-reloaded into the engines; the handler is `del config`

`README.md:72-73`, `src/engines/vol/engine.py:507-518`, `src/core/config/vol_params.py`

`README.md:72-73` — *"Versioned vol config in Postgres (append-only), edited in the web
desk and **hot-reloaded into the engines via Redis pub/sub**"*. `CLAUDE.md:186` repeats it.
`docs/pictures/README.md:27` showcases `settings-volconfig.png` as *"the versioned
VolConfig editor"*, and it is one of the reference screenshots.

`src/engines/vol/engine.py:507-518` — `apply_config`'s docstring promises hot-reload; the
body ends:

```python
del config  # currently unused — kept for forward compatibility
```

The pub/sub plumbing is real and the DB versioning is real. The *effect* is not: the
entire `VolTradingConfig` tree is inert. Verified individually — none of
`SignalConfig.z_threshold_arm/strong/extreme`, `pca_rolling_months`,
`variance_explained_min`, `TradeStructuresConfig.pc1_structure`…`default_tenor_days`,
`SizingConfig.base_size`/`max_loss_pct_capital`/`event_dampener_multiplier`,
`RegimeConfig.stressed_sizing_multiplier`/`event_dampener_horizon_days`,
`DeltaHedgeConfig.mode`/`scheduled_interval_minutes` is read anywhere outside
`vol_params.py` and the generated `schema.d.ts`.

The values that actually drive behavior are hardcoded elsewhere and shadow the documented
config:

| Documented knob | What actually runs |
|---|---|
| `SizingConfig.base_size` (10) | `limits.get("base_qty", 10)`, `config_scalar` ns `risk` — `trade.py:457` |
| `RegimeConfig.stressed_sizing_multiplier` (0.7) | hardcoded `{"calm":1.0,"stressed":0.7,"pre_event":0.0}` — `trade_preview.py:826` |
| `SizingConfig.event_dampener_multiplier` (0.5) | hardcoded `0.5` — `trade_preview.py:824` |
| `RegimeConfig.event_dampener_horizon_days` (5) | hardcoded `EVENT_DAMPENER_DAYS = 5.0` — `regime_engine.py:18` |
| `SignalConfig.z_threshold_arm` (1.5) | `THRESHOLDS["weak"]=1.0` in `actionable_check` |

A user edits the versioned config, sees a new version row appear, and changes nothing.

### D-11 — `docs/strategy/greek-limits.md` was never created; three live references dangle, one into the public OpenAPI

Commit `6ef53f04` — *"docs(risk): move greek-limits spec into docs/strategy"* — **moved
nothing**. `--stat` shows four files touched (`docs/README.md`, `schema.d.ts`,
`portfolio_panel.py`, `greek_limits.py`); there is no rename. The spec is still tracked at
**repo root** as `greek-limits-spec.md`, and `docs/strategy/greek-limits.md` does not exist.

Three references now point at nothing:

- `docs/README.md:43` — the docs index's own link (the **only** dangling link in the tree)
- `src/core/risk/greek_limits.py:3` — `"""Implements ``docs/strategy/greek-limits.md``
  §2/§6/§8"""`
- `src/api/routers/portfolio_panel.py:2173` — same path, and **this string is generated
  into the public OpenAPI description** (`frontend/src/api/schema.d.ts:850`), so the
  public API documentation cites a file that does not exist in a public repo.

Meanwhile the real file at root is referenced by **nothing** — grep for
`greek-limits-spec` across the repo returns only the file itself. A 163-line
implementation brief is orphaned in plain sight.

Correction to a prior audit: `docs/audits/reports/03-risk-engine-findings.md:114` (R3-22)
states the file was "renamed `risk.md`". It was not — `risk.md` is a separate, shorter
narrative doc. The spec was never moved or renamed.

### D-12 — The OpenAPI spec does not merely omit auth; it implies auth is optional

`openapi.json`, `src/api/auth.py:72-87`, `SECURITY.md:34-39`

- `components.securitySchemes`: **absent**.
- Top-level `security`: **absent**.
- Operations with a `security` field: **0 / 109**.
- Operations documenting `401` or `403`: **0 / 109**.

There are **23 mutating operations** (20 POST, 2 PUT, 1 DELETE). Auth is genuinely
enforced — `require_write` raises 401 and is wired via `Depends` across `admin.py`,
`orders.py`, `positions.py`, `trade.py`, `trades.py`, `regime.py`, `signals.py`,
`cockpit.py`. **1 of 23 (4.3%)** carries any signal of it, and that one is
`POST /auth/login`, whose docstring only says "set the auth cookie".

Worse than silence: because `require_write` declares
`fxvol_auth: Annotated[str|None, Cookie()] = None`, FastAPI emits a cookie parameter on
18 of the 23 — as **`"required": false`**, schema `anyOf[string, null]`, no description.
Read literally, the spec states these endpoints take an *optional* cookie: i.e. that auth
is optional. It also leaks onto 7 *reads* (`GET /auth/me` and six `/dev/*` routes),
implying a gate where the semantics differ.

`SECURITY.md:34-39` justifies keeping the docs public partly because *"interactive docs
where write endpoints return 401 demonstrate the auth boundary"*. The spec demonstrates
the opposite. See D-17 for why the interactive rendering likely does not work either.

### D-13 — 81% of operations have no response model; the three request bodies that matter are untyped

| Success response | Count |
|---|---|
| `$ref` to a component schema | 18 |
| array of `$ref` | 3 |
| **typed** | **21 / 109 = 19.3%** |
| `{type: object, additionalProperties: true}` | 64 |
| inline anonymous (`List[dict]`, `Dict[str,dict]`) | 24 |
| **untyped** | **88 / 109 = 80.7%** |

100% untyped by tag: **portfolio-panel (25/25)** — every risk and P&L endpoint the desk
depends on, including `/portfolio/var`, `/marginal-var`, `/stress-grid`, `/vega-pca`,
`/pnl-attribution`, `/greek-limits`; **positions (18/18)** including `POST
/positions/{id}/close`; **dev (14/14)**; **trade (8/8)** including `POST /trade/preview`
and `POST /trade/submit`; **orders (5/5)**; **health (2/2)**; **trades (2/2)**.

Of 15 request bodies, **3 are untyped** `{additionalProperties: true, title: "Body"}` —
and they are `POST /api/v1/orders`, `POST /api/v1/trade/submit`, and
`POST /api/v1/exec/positions/{con_id}/close`. **A consumer cannot learn how to submit a
trade from this specification.**

Where `response_model=` was used it works correctly (the 21 typed ops are concentrated in
`pricing`, `auth`, `vol`). It simply was not used for four endpoints in five. Since
`docs/` contains no REST reference at all (M-18), the OpenAPI spec *is* the API
documentation, which makes this load-bearing rather than cosmetic.

### D-14 — ~30 source files cite a normative spec directory that does not exist

`docs/vol_trading_pca/specs/` and its `STEP1`…`STEP5` sections are cited as the governing
specification by roughly thirty modules — the core algorithms among them:

`core/vol/pca_engine.py:7,204,254`, `core/vol/regime_engine.py:8,159`,
`core/vol/gmm_regime.py:17,76`, `core/vol/feature_enrichment.py:8`,
`core/positions/delta_hedge.py:1` (*"Cf. STEP5 §4"*), `core/positions/exit_rules.py:1`,
`core/positions/closing.py:1`, `core/positions/position_pricing.py:2`,
`core/trade_preview.py:7`, `core/trade_preview_regime.py:4`,
`core/execution/{contract_builder,revalidation,rollback}.py`, `engines/execution/*`,
`bus/channels.py`, `bus/publisher.py`.

**The directory does not exist.** Anyone asked to change the PCA gating or the delta-hedge
policy is directed to a specification they cannot open, for the parts of the system where
"why is this threshold 1.5?" matters most.

Same class, other missing targets: `docs/VOL_TRADING_USER_GUIDE.md` and
`docs/VOL_MODEL_REFACTOR_PLAN.md` (`core/config/vol_params.py:11-12`),
`MARKETS_OPEN_TODO.md` (`api/routers/positions.py:1319`), `TODO.md`
(`api/routers/regime.py:226`), `docker-compose.dev.yml` (`docker-compose.yml:6`).

### D-15 — Delta hedge: one of three documented modes is implemented, and the config it documents is not the config that runs

`README.md:69`, `docs/strategy/risk.md:102-104`, `src/core/config/vol_params.py:170-177`,
`src/core/positions/delta_hedge.py`

`README.md:69` — *"Delta hedge modes: **static / threshold / scheduled**"*.
`risk.md:102-104` — *"Hedge behaviour is configured by `DeltaHedgeConfig` in
`vol_params.py`: `mode` (`static`/`threshold`/`scheduled`), `threshold_delta`,
`scheduled_interval_minutes`."*

The three fields exist at `vol_params.py:170-177` and **none is read by any code**. The
only consumer of `DeltaHedgeConfig` is the Pydantic JSON-Schema export that renders the
admin form (`api/orchestration/config_service.py:173`).

The production path uses a different source and different names:
`PositionMonitor._load_hedge_config` (`api/orchestration/position_monitor.py:565-580`)
reads `config_scalar` namespace `delta_hedge` with `rebalance_threshold_delta`,
`min_hedge_qty`, `max_hedge_frequency_seconds`, `hedge_during_close` — no `mode`, no
interval. `src/core/positions/delta_hedge.py` (79 lines, read in full) implements
**only** threshold-with-cooldown. There is no `static` branch, no `scheduled` branch, no
interval timer. Grep for `hedge_mode|scheduled_interval_minutes` outside `vol_params.py`
and `schema.d.ts`: zero hits.

An operator selecting "scheduled" in the Settings panel gets threshold hedging.

### D-16 — `docs/strategy/signals-to-trades.md` documents, in the present tense, a pipeline that was deleted

`signal_to_structure` (`src/engines/execution/structures.py:276`) has **zero call sites** —
grep across `src/`, `frontend/src/` and `tests/` finds only the definition and its own
module docstring. The code states the removal explicitly, `src/api/routers/trade.py:346-350`:

```
# The desk does not propose trades : the user always composes the position
# directly … Signal-driven auto-structuring was removed — the PCA engine no
# longer emits recommended_structure.
manual_mode = True
```

`manual_mode` is a hardcoded `True`; the `from_signal` branch at `trade.py:640` is dead.

The document is written throughout as live behavior:

- `:2-4` — *"each PC's z-score **is** a tradeable signal, and each **maps to** exactly one
  canonical structure. The map **lives in** `signal_to_structure()`"*
- `:40` — *"`SignalConfig` **gates** a raw z-score into a UX badge **before it can become
  a ticket**"*
- `:59` — *"The GMM regime **modulates the raw signal before it sizes a trade**"*
- `:66-70` — *"`SizingConfig` **then sizes the position**… **capped at
  `max_loss_pct_capital` (2% of capital)**. The resulting structure + tenor + side **is
  what flows into** the order builder"*

Every config object it names is inert (D-10), and the 2%-of-capital cap it asserts was
explicitly deleted (D-01). A reader finishes this document believing the system trades its
own signals automatically, under a capital cap. It does neither.

### D-17 — The interactive API docs are almost certainly blank in prod, and SECURITY.md relies on them as a control

`src/api/main.py:166-173`, `infrastructure/nginx/nginx.conf:53,73-80`, `SECURITY.md:34-39`

`/api/docs`, `/api/redoc` and `/api/openapi.json` are proxied publicly and ungated
(`nginx.conf:73-80` → `http://api_upstream/api/`) — deliberate, per `SECURITY.md:34-39`
and tracked as F-10 (Medium) in Audit 1.

But FastAPI 0.111.1's `get_swagger_ui_html` loads
`https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js` (+ its CSS and a
`fastapi.tiangolo.com` favicon), and ReDoc loads
`https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js`. `nginx.conf:53`
sets, `always`, on the whole server block:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; …
```

The `/api/` location adds no header of its own, so it **inherits** that CSP.
`script-src 'self'` blocks both CDN bundles. The HTML shell is served; the JavaScript
never executes.

Net effect: the raw `openapi.json` is fully public and silent about auth (D-12), while the
two human-readable renderings that `SECURITY.md:36-39` cites as the justification for that
exposure — *"interactive docs where write endpoints return 401 demonstrate the auth
boundary"* — are both non-functional behind the CSP **and**, even if they rendered,
document zero 401s. A documented security control that does not work.

*Confirm with a live `curl` of `/api/docs` before acting; the config analysis is
unambiguous but was not executed against the deployment, per the run constraints.*

---

## 4. Medium

| # | Finding | Location |
|---|---|---|
| M-01 | **CLAUDE.md points at scripts that do not exist**: `.\scripts\ops\load_secrets.ps1`, `.\scripts\ops\stack.ps1` (×2), `scripts/up.ps1`, `scripts/down.ps1`. There is no `scripts/ops/`; the tree has only `scripts/local/` and `scripts/aws/`. CLAUDE.md contradicts itself — its own §Rappels uses the correct `scripts/local/` path | `CLAUDE.md:117-119,299` |
| M-02 | **Count drift cluster** (all cascade from D-03 except the last two): 11→12 containers, 6→7 Python containers, 5→6 engines, 7→8 CI images, "28 ORM classes"→27 (`class Base` miscounted; README:146,254 correctly says 27), "54 alembic revisions"→55, "~730 unit tests"→**809** collected | `CLAUDE.md:96,118,155-161,163,166,186`, `README.md:82,142,207,224,252`, `docs/architecture/overview.md:76` |
| M-03 | **README's Testing block is fenced ```powershell but written in POSIX shell.** `PYTHONPATH=src python -m pytest` and `PYTHONPATH=src lint-imports` are not valid PowerShell — the block also mixes in `$env:X = "1"` correctly two lines below, so the inconsistency is visible. Copy-pasting the documented test commands fails on the documented platform | `README.md:222-236`, `:300-306` |
| M-04 | **`.github/WORKFLOW.md` describes a git model that is not the one in use.** `:22` — *"`main`: permanent, protected. **Only receives PRs**, squash-merged. **Never a direct commit**"* — contradicted by CLAUDE.md's mandated model (direct commits on local `main`, drip-PR to origin) and by the actual history. A contributor following `.github/` would be told to do the opposite of the project's rule | `.github/WORKFLOW.md:22` vs `CLAUDE.md` §Git |
| M-05 | **WORKFLOW.md §7 "Releases" is two bullets** and is the entire release documentation: no semver policy, no major/minor/patch definition, no mapping between the board's `R11/R12/R13` buckets and `vX.Y.Z`, no CHANGELOG step, no GitHub Release step | `.github/WORKFLOW.md:84-87` |
| M-06 | **`build.yml` header comment is two refactors stale**: *"Build + push the **two** application images"* (builds 8) and *"No deploy side-effect — R8 will layer a deploy job on top of a new tag once the EC2 target is provisioned"* (deploy is live and chains off this workflow). `:164` also says "the **four** parallel matrix jobs" (6) | `.github/workflows/build.yml:3-6,164` |
| M-07 | **Stale comments inside `docker-compose.yml`**: `:6` references `docker-compose.dev.yml`, which does not exist; `:598` says "the full **17-container** local stack" (19 services); `:582-583` says *"Grafana also gets `fxvol-public`"* but `:772-773` lists only `fxvol-internal` (access is via the `127.0.0.1:3000` host port); `:526-528` lists IB TrustedIPs `.10/.11/.12` but `execution-engine` at `.14` also connects | `docker-compose.yml` |
| M-08 | **`scripts/local/README.md` is wrong about what the script does and is textually damaged**: `:57` claims the pipeline runs *"prereqs → **`git pull`** → venv"*, contradicted by `docs/ops/local-stack.md:72` and by `stack.ps1:18,320` (*"No git pull: containers are built from the LOCAL working tree"*); `:82-83` ends in an orphaned sentence fragment (*"long-lived shell, so secrets loaded once stay in RAM for later actions."*) with no subject — text lost in an edit. `stack.ps1:12`'s own synopsis says "5 engines" | `scripts/local/README.md:57,82-83` |
| M-09 | **Secrets inventory incomplete**: `docs/ops/secrets.md:12-18` documents 9 parameters; **`/fxvol/prod/AUTH_SALT`** (fetched `remote-deploy.sh:19`, consumed `api/config.py:53,82-83`, `docker-compose.yml:95` — part of the auth boundary the doc claims to cover) and **`/fxvol/prod/TRADING_MODE`** (both loaders, `docker-compose.yml:489`) are undocumented. The doc also presents the parameter set as uniform across the three load paths; it is not (`FRED_API_KEY` absent from `scripts/aws/load_secrets.sh`, `GHCR_TOKEN` only in `remote-deploy.sh`) | `docs/ops/secrets.md` |
| M-10 | **No `.env.example` and no documented non-AWS local path** — see D-05. Called out separately because it is the single cheapest partial mitigation | repo root |
| M-11 | **OpenAPI: 0 of 109 summaries are hand-written.** All 109 are byte-identical to FastAPI's `func_name.replace("_"," ").title()` default — verified programmatically, 0 differ. Swagger UI shows `Get Current Config`, `Refit`, `Me`, `Bars`, `Db Schema`. The nominal "100% summary coverage" is an artifact | `openapi.json` |
| M-12 | **OpenAPI: 29 of 109 operations (26.6%) have no description at all**, and the undocumented set skews to the write surface — the **entire `orders` router (5/5)**, 4 of 7 `admin` config ops, 6 `positions` ops, 4 `trade` ops, both `pricing` POSTs, `POST /auth/logout` | `openapi.json` |
| M-13 | **OpenAPI `info` is two keys.** No `description`, no `contact`, no `license`, no `termsOfService`, no `servers`, no `openapi_tags` metadata. A reader landing on the docs gets a title and a stale version number. `servers` being absent also means "Try it out" and generated clients resolve against the wrong root — the real deployment is under `/fx-volatility-trading-system/api/` | `src/api/main.py:166-173` |
| M-14 | **OpenAPI documents no error responses.** Only `200` (109) and `422` (70) appear in the whole spec. **0 of 109** document any 4xx/5xx beyond FastAPI's automatic 422 — no 401, no 403, no 404 (despite 20 path parameters that do 404 in practice), no 409, no 429 (despite active slowapi limits and the documented 5/min `/login` throttle), no 500 | `openapi.json` |
| M-15 | **OpenAPI tag taxonomy is incoherent**: `trade` (8) vs `trades` (2), one plural apart and indistinguishable in the sidebar; `portfolio-panel` (25, the largest tag) and `cockpit` (3) name *frontend widgets*, not API domains; `cockpit` splits the `/api/v1/vol/*` prefix across two sidebar groups; `POST /admin/pca/refit` is tagged `signals` and appears nowhere near the other admin ops; `/exec/positions*` is tagged `orders`; `analytics` (3) is a grab-bag; the `websocket` tag declared at `ws.py:13` never appears in the spec | `openapi.json`, `src/api/routers/*` |
| M-16 | **WebSocket endpoints: 0 of 6 have a documented message shape; 3 of 6 are not named in any user-facing doc.** `/ws/ticks`, `/ws/vol`, `/ws/risk` are named in `CLAUDE.md:190`, `docs/architecture/data-flow.md:55`, `frontend.md:53-55`, `SECURITY.md:20`; `/ws/orders/{structure_id}`, `/ws/positions`, `/ws/exit_alerts` appear **only** inside `SECURITY.md:20`'s compressed glob. No payload schema for any of the six (two are typed `unknown` in `frontend.md`). The only place a WS payload shape is written down anywhere is a findings table in `docs/audits/reports/05-data-integrity-findings.md:1019-1027` — which also records a mismatch. Additionally `src/api/ws/redis_bridge.py:20-28` forwards **7** channels including `CH_ACCOUNT` and `CH_SYSTEM_ALERTS`, for which no WS endpoint exists | `src/api/routers/ws.py:38-69` |
| M-17 | **There is no REST API reference in `docs/`.** The docs index has no API section; across all non-audit docs the string `/api/v1/` appears **5 times**, four of them health-check URLs. The OpenAPI spec is the API documentation by default — which is what makes D-12/D-13 and M-11..M-15 load-bearing rather than cosmetic | `docs/README.md` |
| M-18 | **`npm run gen:api` / `gen:api:check` are broken locally.** Both point at `http://localhost:8000/openapi.json`, but `main.py:172` sets `openapi_url="/api/openapi.json"` — there is no route at `/openapi.json`, so both 404 against a correctly-running API. CI is unaffected because `ci.yml:127-132` reads the dumped file and bypasses HTTP. Secondary: the script uses `/tmp` and `diff -q`, so it needs Git Bash on this Windows host | `frontend/package.json:22-23` |
| M-19 | **`docs/observability/conventions.md` omits the 6th engine** from the `engine` label set (`:45`) and from the documented scrape targets (`:61`) — while `obs/prometheus.yml:21` does scrape `analytics-engine:9106` | `docs/observability/conventions.md` |
| M-20 | **`docs/execution/ib-integration.md:27` is stale**: *"The api's own `OrderExecutor` (used by `api.routers.orders`) uses clientId 4"* — `grep OrderExecutor src/api/routers/*.py` returns nothing; the api no longer instantiates it. `docs/architecture/data-flow.md:22` says the opposite ("clientID 4 is reserved for a legacy path"); one of the two must go | `docs/execution/ib-integration.md:27` |
| M-21 | **CLAUDE.md is written in French while mandating English-only.** `CLAUDE.md` §Task tracking: *"all repo content (docs, comments, commits, PRs, issues, labels) is **English only**"* — and ~56 lines of the file itself, including the entire Git/workflow and secrets sections, are in French. The repo is public | `CLAUDE.md` |
| M-22 | **`config_catalog.py:8` asserts the opposite of the truth**: *"consumers … read the same `config_scalar` rows, **so editing a knob changes real behaviour** — these are policy, not data."* False for the whole `risk` domain (the 9 greek-limit knobs, `:42`) and for `max_n_open_structures` / `max_book_vega_per_tenor_usd` (`:45,50`). The Settings UI presents dead knobs as live policy | `src/core/config_catalog.py:8` |
| M-23 | **`vol_params.py`'s module docstring is false in three ways**: it calls itself *"**Authoritative source of truth** for the admin UI and the DB `vol_config` table"* and claims constraints *"never reach the DB **or the services**"* (no service reads it, D-10); `:11-12` cites two missing files; `:212-216` documents a 2-step extension procedure whose step 2 (*"reference `get_current_config().section.field` from the consuming service code"*) describes a consumer pattern that has no instance — `get_current_config` is an API route handler (`admin.py:49`) | `src/core/config/vol_params.py:1-16,212-216` |
| M-24 | **`trade_preview.py:850` says "(7 checks)"; 5 run.** Lines 885-888 record the deletion of the other two. The stale count is echoed by `trade.py:1336-1370`, whose `?scenario=` demo fixture **fabricates a 7-element `pre_submit_checks` array**, patching indices `[2]` and `[4]` — so the demo path advertises checks the live path does not run | `src/core/trade_preview.py:850`, `src/api/routers/trade.py:1336-1370` |
| M-25 | **`docs/strategy/risk.md` miscounts and mis-attributes**: `:54` — *"All **eight** policy values are editable"* (`CONFIG_DEFAULTS` has **nine**, `greek_limits.py:36-46`); `:10-12` — *"Per-leg greeks come from Black-76 (`bs_greeks`)… The book tracks **six** sensitivities"* listing vanna/volga, but `bs_greeks` (`structures.py:83-99`) returns **four** keys and its own docstring says so; `:24-25` — *"the order builder surfaces **all six**"* — grep for `vanna|volga` across `frontend/src/` (excluding generated types) returns **zero hits**; `OrderBuilder.tsx` shows four | `docs/strategy/risk.md:10-12,24-25,54` |
| M-26 | **Orphan documentation**: `infrastructure/aws/{EC2_DEPLOYMENT,SETUP,STATE,secrets-bootstrap}.md` and `infrastructure/ec2/RESTORE.md` are linked from nothing in `docs/` or `README.md` (RESTORE.md gets one passing mention in `deployment.md:130`). `SECURITY.md` is linked from no document either. `docs/README.md`'s index claims to "list every doc" | `docs/README.md`, `infrastructure/` |
| M-27 | **`tests/STRUCTURE.md:172` says "the 5 engines"**, and CLAUDE.md's test-folder list (`:204`) omits `api/routers/`, `engines/analytics/`, `oms/` and the `core/{config,execution,positions,pricing,risk,vol}` subfolders. `tests/unit/oms/` has no `src/oms/` counterpart, so the "mirrors `src/` 1-to-1" claim is not exactly true | `tests/STRUCTURE.md:172`, `CLAUDE.md:204` |

### Cross-referenced, not double-counted

Audit 7 completed while this audit was running and produced one finding squarely in
Audit 11's scope. It is **owned by Audit 7 (T7-05) and not counted in the totals above**,
but it belongs on the docs-vs-reality list:

> **`CLAUDE.md:215` documents a frontend coverage gate of "≥ 70% coverage".** The gate
> actually enforced in `frontend/vitest.config.ts:37` is `lines: 15`, and measured
> coverage is **17.36%**. The doc overstates the enforced gate by 4.7×.
> Related: `.github/workflows/ci.yml:66` uploads backend coverage with **no
> `--cov-fail-under`** — there is no backend coverage gate at all, which
> `CLAUDE.md`'s CI table does not say either way.

Audit 7 also independently corroborates M-02's test-count drift: it ran the suite and
reports **794 passed / 15 skipped = 809**, matching this audit's `--collect-only` count
against README's *"~730 unit tests"*.

---

## 5. Low

| # | Finding | Location |
|---|---|---|
| L-01 | `obs/prometheus.yml:4` cites `docs/observability/CONVENTIONS.md`; the file is lowercase `conventions.md` — resolves on Windows, breaks on the Linux host. Same line says "the 5 engine /metrics endpoints" while listing 6 | `obs/prometheus.yml:1,4` |
| L-02 | `.importlinter:73` — contract-4's explanatory comment lists 5 engines. Comment-only: the contract is package-level (`source_modules = engines`), so `engines.analytics` **is** functionally covered | `.importlinter:73` |
| L-03 | README's diagram and table imply `8000` and `8001` are addressable; neither is ever published to the host in dev or prod (container-internal, reachable only through nginx). Conversely, README documents none of the ports that *are* published: `5433`, `6380` (dev override), `3000` (grafana), `4002`, `5900` (ib-gateway) — all loopback-scoped except nginx's `80`/`443` | `README.md:86-139` |
| L-04 | `execution-engine`'s `expose:` lists only `9104`, omitting `8001`, while every other service exposes its app port. Harmless (`expose` is a no-op for compose DNS) but inconsistent | `docker-compose.yml:432-433` |
| L-05 | `SECURITY.md:29-30` cites a fix sketch at *"`audit history / routers/ws.py`"* — not a resolvable path | `SECURITY.md:29-30` |
| L-06 | All 109 `operationId`s use FastAPI's verbose `{func}_{path}_{method}` form (e.g. `revert_config_api_v1_admin_config_revert__version__post`), so generated clients get unusable method names | `openapi.json` |
| L-07 | OpenAPI descriptions are raw docstrings piped through unmodified: 52 use reST double-backticks that render **literally** in Swagger UI's Markdown; 2 emit reST underlines (`Params\n------`) that Markdown parses as setext H2, breaking the layout (`/dev/logs/query`, `/dev/tables/{name}`); 2 leak Sphinx roles (`:class:`, `:func:`); 13 leak internal jargon (`R11 G-portfolio`, `G-risk 5.3`, `Step 5 phase 1`); 8 cite source modules as if the reader has the repo; `POST /trade/submit`'s description explains its try/except wrapper and a Loki query instead of its contract | `openapi.json` |
| L-08 | OpenAPI has **zero** `example`/`examples` in 216 KB. Parameter descriptions: **1 of 110** (0.9%). Schema property descriptions: 15 of 211 (7.1%) | `openapi.json` |
| L-09 | `docs/pictures/README.md:15` captions `architecture.png` as *"the container topology — the **11 services** healthy"*; the image shows 17 nodes (11 core + 6 obs) and is missing analytics-engine | `docs/pictures/README.md:15` |
| L-10 | `pyproject.toml:7` `version = "0.1.0"` has evidently never been maintained; if it is not going to be the source of truth it should be removed from consideration explicitly rather than left stale | `pyproject.toml:7` |
| L-11 | `.gitignore:40` comments the ignore rule as *"scripts/dump_openapi.py writes it"*; the actual path is `frontend/scripts/dump_openapi.py` | `.gitignore:40` |
| L-12 | `frontend/README.md` still ends with *"## Roadmap (R5) — This PR ships the **scaffold only**: Vite config, TS strict, ESLint, Prettier, **empty App shell**"*. The app is 7 views, a dev console, and 6 WS hooks. It also says the backend was "delivered in R4" | `frontend/README.md` |
| L-13 | `core/trade_preview_regime.py:1-11` says *"The seed migration (013) loads a flat set of limits"*; migration 024 folded `risk_limits` into `app_config_scalar` and 037 renamed it `config_scalar` — 013's table no longer exists. The same docstring's `LIMIT_MULTIPLIERS["pre_event"] = 0.0` is unreachable, since `regime_not_pre_event` blocks first | `src/core/trade_preview_regime.py:1-11` |
| L-14 | `docs/architecture/overview.md:53` says the engines get stable IPs `.10`–`.14`; `.13` is **api** and `.14` is execution-engine, while `db-writer` and `analytics-engine` take dynamic addresses from `172.20.0.128/25` | `docs/architecture/overview.md:53` |

---

## 6. Appendix — API documentation measurements

Measured by parsing a freshly regenerated `openapi.json` (OpenAPI 3.1.0). Note the spec is
**not committed** — `.gitignore:43` ignores it as a build artifact; the on-disk copy is
local output.

| Metric | Value |
|---|---|
| Paths / operations | 105 / 109 (GET 86, POST 20, PUT 2, DELETE 1) |
| Component schemas | 45 (0 orphaned) |
| Operations with a summary | 109 (100%) — **0 hand-written** |
| Operations with a description | 80 (73.4%) |
| Operations with neither | 0 |
| Operations tagged | 109 (100%), 15 distinct tags, no tag metadata |
| **Typed success response** | **21 (19.3%)** |
| Request bodies | 15, of which **3 untyped** (orders, trade/submit, exec close) |
| `securitySchemes` / top-level `security` | **absent / absent** |
| Operations with `security` | **0** |
| Mutating operations | 23 |
| Mutating ops advertising auth | **1 (4.3%)** |
| Operations documenting 401/403 | **0** |
| Operations documenting any 4xx/5xx beyond 422 | **0** |
| `info` keys | `title`, `version` only (`"FXVol API"`, `"0.4.0"`) |
| `servers` | absent |
| Deprecated operations | 0 |
| `example` / `examples` occurrences | **0** |
| Parameter descriptions | 1 / 110 (0.9%) |

**Drift gate, verified end to end (4 steps, all exit 0):** regenerate spec → `diff` vs
on-disk (identical) → `openapi-typescript` → `diff` vs committed `schema.d.ts`
(identical) → same from the regenerated spec (identical). `schema.d.ts` contains 105 path
keys and 109 operation references, matching exactly. **CI's drift job would pass right
now** — it correctly guards a spec that nobody has curated.

---

## 7. What must be re-verified after the Wave 1–2 fixes land

`[re-verify]` D-01 (implement the controls, then re-check the docs describe them),
D-02 (README wording depends on the §8.1 legal decision), D-04 (the flag should be gone,
not documented), D-10, D-15, D-16 (all three resolve either by implementing or by deleting
the claim — the choice is a product decision), D-13 (response models are code changes),
plus every count in M-02 if the analytics extraction is completed or reverted.

Everything else — dangling references, missing runbooks, release mechanics, secrets
inventory, the fresh-setup path — is independent of the code fixes and can be closed now.
