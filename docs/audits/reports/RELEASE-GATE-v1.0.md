# Release Gate — v1.0

**Status as of 2026-08-01: NOT READY.**

**46 gates: 4 met ✅ · 5 partially met ⚠️ · 37 not met ❌.**

The four that pass are: all 11 audits have reported; `import-linter` contracts
hold; there is no pre-rename or desktop-app residue anywhere; and the
`schema.d.ts` drift gate is genuinely green. **Nothing in section 2 (safety
controls), section 4 (operability) or section 6 (release mechanics) passes.**

This document is the release gate itself, per `../11-docs-release-readiness.md`
("this checklist is the release gate"). It is **not** a claim of readiness — it
is the list of statements that must all be true before a `v1.0` tag is created,
each marked with its honest current status.

Audit 11 was designed to run last, after Audits 1–10 fixes land. It was run
**early, on the pre-fix state**. Every gate below is evaluated against the tree
at `main` (local), 5 commits ahead of `origin/main`, working tree clean.

Legend: ❌ not met · ⚠️ partially met · ✅ met · ⏸️ blocked on an unrun audit

---

## 1. Audit campaign closure

### 1.1 Findings by audit

| # | Audit | Blocker | High | Medium | Low | Report |
|---|---|---|---|---|---|---|
| 1 | Security | 0 | 3 | 8 | 9 | `01-security-findings.md` |
| 2 | Quant models | 0 | 2 | 8 | 5 | `02-quant-models-findings.md` |
| 3 | Risk engine | 2 | 7 | 12 | 6 | `03-risk-engine-findings.md` |
| 4 | Execution | 1 | 11 | 13 | 4 | `04-execution-findings.md` |
| 5 | Data integrity | 4 | 13 | 16 | 5 | `05-data-integrity-findings.md` |
| 6 | Architecture | 0 | 2 | 8 | 4 | `06-architecture-findings.md` |
| 7 | Test coverage | 3 | 8 | 7 | 2 | `07-test-coverage-findings.md` |
| 8 | Infra & ops | 2 | 11 | 16 | 6 | `08-infra-ops-findings.md` |
| 9 | Frontend | 3 | 5 | 10 | 8 | `09-frontend-findings.md` |
| 10 | Dependencies | 0 | 4 | 7 | 4 | `10-dependencies-findings.md` |
| 11 | Docs & release | 2 | 15 | 27 | 14 | `11-docs-release-findings.md` |
| | **Campaign total — all 11 audits** | **17** | **81** | **132** | **67** | |

**297 findings. The campaign is now complete: all 11 audits have reported.**
Audits 7 and 8 completed while this audit was running; their counts are folded in
above, and `08-runbook-draft.md` is the runbook referenced by G4.3.

Two independent corroborations of gates below:

- Audit 8's headline answers G4.4 in its own words: *"Is there a tested Postgres
  backup? **NO.**"*
- Audit 7 answers why none of this was caught: **8 of the 10 pre-existing
  Blockers have zero executable coverage of the defective line**, and **7 tests
  actively assert the defective behaviour as the intended contract** — so fixing
  a Blocker turns tests red and invites a revert. CI was green throughout.

### 1.2 Gates

| Gate | Status | Evidence |
|---|---|---|
| G1.1 — All 17 Blockers fixed or waived in a tracked issue | ❌ | 17 open, 0 fixed, 0 tracked |
| G1.2 — All 81 Highs fixed or explicitly waived | ❌ | 81 open |
| G1.3 — All 11 audits have reported | ✅ | complete as of 2026-08-01 |
| G1.4 — Audits closed (findings fixed or waived), not merely reported | ❌ | 0 of 11 closed; per `../README.md:60` an audit closes only when its exit criteria are met |
| G1.5 — Every finding filed as a GitHub issue with the `audit` label | ❌ | `gh issue list --state open` returns **0 issues**. The entire campaign — 297 findings — exists only as markdown in `docs/audits/reports/`, contradicting CLAUDE.md's "GitHub is the source of truth for task tracking" |
| G1.6 — The live-verification drills owed by Audits 3/4/5/9 executed | ❌ | 46 drills specified across four reports; **none run** (WAVE2-SUMMARY §"Live verification still owed") |

---

## 2. Safety controls the documentation asserts

A release tag states the system is safe to run unattended. These gates exist
because the documentation currently claims each control is in place.

| Gate | Status | Evidence |
|---|---|---|
| G2.1 — Greek/vega/notional caps are **enforced**, not merely computed | ❌ | `compute_caps` has one production caller, `GET /portfolio/greek-limits`, which has no frontend consumer at all. `docs/strategy/risk.md:3-5` and `greek-limits-spec.md:93,109,146` describe enforcement. R3-08, D-01 |
| G2.2 — A kill switch exists, is tested, and is documented | ❌ | zero occurrences of `kill_switch`/`kill switch` in `src/`, `frontend/src/`, compose or docs. EX-09 |
| G2.3 — Pre-trade risk gate fails **closed** | ❌ | 2 of 5 checks hardcoded to pass (`trade.py:519-520` `# MVP : assume liquid`), 2 deleted, the surviving one reads an unfreshness-checked snapshot. EX-08 |
| G2.4 — Price collar / notional cap on submission | ❌ | neither exists; limit taken from IB's raw touch under delayed-frozen data. EX-10 |
| G2.5 — Greeks and IV do not go NULL on expiry day | ❌ | R3-01 (Blocker): position vanishes from every aggregate simultaneously |
| G2.6 — The automated delta hedge can fire | ❌ | R3-02 (Blocker): `current_delta_unhedged` hardcoded `0.0` on the live branch |
| G2.7 — Documentation describes only controls that exist | ❌ | D-01 (Blocker), D-10, D-16, D-17 |

---

## 3. Documentation accuracy

| Gate | Status | Evidence |
|---|---|---|
| G3.1 — README container table + diagram match `docker-compose.yml` | ❌ | README says 11 core containers / 6 shipping our Python / 5 engines; reality is **12 / 7 / 6**. `analytics-engine` appears in no diagram, no table, no prose. D-03 |
| G3.2 — CLAUDE.md source tree matches `src/` | ❌ | missing `engines/analytics/`, `core/{config,execution,positions}/`, `core/risk/*`; 28 ORM classes (27), 54 migrations (55) |
| G3.3 — Every command in the docs runs as written | ❌ | `scripts/ops/load_secrets.ps1`, `scripts/ops/stack.ps1`, `scripts/up.ps1`, `scripts/down.ps1` (CLAUDE.md:117-119,299) do not exist; README's `powershell` test block uses POSIX `VAR=x cmd` syntax |
| G3.4 — No dangling doc references | ❌ | `docs/strategy/greek-limits.md`, `docs/vol_trading_pca/specs/` (~30 source files cite it as normative), `docs/VOL_TRADING_USER_GUIDE.md`, `docs/VOL_MODEL_REFACTOR_PLAN.md`, `MARKETS_OPEN_TODO.md`, `TODO.md`, `docker-compose.dev.yml` |
| G3.5 — No pre-rename / desktop-app residue | ✅ | verified clean across `docs/`, `src/`, `.github/`. Only `chain_fetcher.py:29` mentions Qt, correctly in the past tense |
| G3.6 — Architecture lint contracts hold | ✅ | `lint-imports`: 5 kept, 0 broken (225 files, 349 dependencies) |
| G3.7 — Screenshots current with the shipped UI | ⚠️ | all 15 captured 2026-08-01, but `architecture.png` is captioned "the 11 services healthy" and the `/dev` console it captures is itself missing `analytics-engine`; `settings-volconfig.png` showcases an editor whose knobs are inert |
| G3.8 — Strategy specs match implementation | ❌ | 11 doc-describes-unimplemented findings (Audit 11 §3). `docs/vol-modeling/*` is the one clean corner — verified line-for-line against `src/core/vol/` |

---

## 4. Operability — can a stranger stand it up, and can you recover it?

| Gate | Status | Evidence |
|---|---|---|
| G4.1 — Fresh-machine setup completes from written docs alone | ❌ | **Structurally impossible.** The only documented secrets path is AWS SSM `/fxvol/prod/*` in one private account; `docker compose` hard-fails on `${DB_PASSWORD:?}`; there is no `.env.example`; `stack.ps1` requires an authenticated `aws` CLI. D-05 |
| G4.2 — Prerequisites list is complete | ❌ | README lists "Docker Desktop + Python 3.11 + Node 20"; actually also needs AWS CLI + an AWS account + 11 provisioned SSM parameters + an IB paper account + WSL2 |
| G4.3 — Runbook committed and linked: reboot, rollback, restore, IB recovery | ⚠️ | `docs/observability/runbooks.md` contains **zero** incident runbooks — it is an LGTM query guide (D-06). Audit 8 delivered `08-runbook-draft.md`, which does cover reboot / rollback / restore with exact commands — but it is a **draft in the audits folder**, not committed under `docs/`, not linked from anywhere, and never rehearsed. Gate closes when it is promoted, linked, and walked once |
| G4.4 — Backup restore tested at least once | ❌ | `infrastructure/ec2/RESTORE.md` is a real, good runbook — but its own test log reads `_none yet — run once before go-live_`, under its own heading "**An untested backup is not a backup**". It is also unlinked from `docs/` |
| G4.5 — Alerting on engine death / stale data / IB disconnect | ❌ | no Alertmanager, no alert rules, no SNS topic armed, no notification path anywhere in `obs/` or the workflows. Monitoring is dashboards-only (pull) |
| G4.6 — `/health/extended` covers every engine | ❌ | `health.py:24` monitors 3 of 6 engines (`market_data`, `vol`, `risk`); `db_writer`, `execution` and `analytics` are invisible to the readiness probe |
| G4.7 — Secrets inventory complete (names only) | ⚠️ | `docs/ops/secrets.md` documents 9 of 11; `AUTH_SALT` and `TRADING_MODE` are fetched and consumed but undocumented |
| G4.8 — No secret has been exposed | ⚠️ | no secret value appears in any repo file (verified). **But** F-02 stands: `REDIS_PASSWORD` has been logged in cleartext at every API startup and sits in Loki for the retention window. **Rotation is a prerequisite to tagging, not a follow-up** |

---

## 5. API documentation

| Gate | Status | Evidence |
|---|---|---|
| G5.1 — Every operation has a description | ❌ | **80 / 109 (73.4%)**. 29 have none — including the entire `orders` router (5/5) and 4 of 7 admin config ops. Separately, **0 of 109 summaries are hand-written** (all are FastAPI's `func_name.title()` default). M-11, M-12 |
| G5.2 — Every operation has a typed success response model | ❌ | **21 / 109 (19.3%)**. `portfolio-panel` (25), `positions` (18), `dev` (14), `trade` (8), `orders` (5), `health` (2), `trades` (2) are 100% untyped. 3 of 15 request bodies untyped — including `POST /trade/submit` and `POST /orders`. D-13 |
| G5.3 — Auth requirement machine-readable in the spec | ❌ | no `securitySchemes`, no `security` field on any operation, no 401/403 documented anywhere. **1 of 23 mutating ops (4.3%)** hints at auth. The optional `fxvol_auth` cookie param on 18 of them states the opposite — that auth is optional. D-12 |
| G5.4 — `schema.d.ts` drift gate green | ✅ | verified end to end: regenerate → `openapi-typescript` → `diff` reproduces the committed `schema.d.ts` byte-identically (105 paths / 109 operations both sides). CI would pass |
| G5.5 — Error responses documented | ❌ | the spec contains only `200` and `422`. **0 of 109** document 401/403/404/409/429/500 |
| G5.6 — Interactive docs actually render | ❌ | `/api/docs` and `/api/redoc` load Swagger/ReDoc from jsdelivr; `nginx.conf:53` sets `script-src 'self'` on the whole server block, which the `/api/` location inherits. Both should render blank. `SECURITY.md:36-39` cites these pages as the demonstration of the auth boundary. D-17 |
| G5.7 — WS endpoints documented where a consumer can find them | ❌ | **0 of 6 have a documented message shape.** 3 of 6 (`/ws/orders/{id}`, `/ws/positions`, `/ws/exit_alerts`) appear only inside a compressed glob in `SECURITY.md:20`. `redis_bridge.py` forwards 2 further channels with no endpoint at all. M-16 |
| G5.8 — A REST API reference exists in `docs/` | ❌ | none. `/api/v1/` appears 5 times across all non-audit docs, 4 of them health URLs. The uncurated OpenAPI spec *is* the API documentation. M-17 |

---

## 6. Release mechanics

| Gate | Status | Evidence |
|---|---|---|
| G6.1 — Versioning scheme chosen and recorded | ❌ | `.github/WORKFLOW.md:84-87` is two bullets ("a `vX.Y.Z` tag on `main`"). No semver policy, no definition of what bumps major/minor/patch, no mapping between the board's `R11/R12/R13` release buckets and `vX.Y.Z` |
| G6.2 — One authoritative version number | ❌ | **three, all different**: `pyproject.toml:7` = `0.1.0`, `src/api/main.py:168` FastAPI `version="0.4.0"`, `frontend/package.json:4` = `0.1.0`. None matches any tag |
| G6.3 — `v1.0` is available as a tag | ❌ | **`v1.0` already exists locally**, on commit `4be1b54d` (2026-04-17), which is **not an ancestor of `main`** — orphaned by the history rewrite. So are `v1.2.0`…`v1.12.0` and `oms-v1.0`. Tagging `v1.0` requires `-f` and would silently reuse a name that already means something else |
| G6.4 — Tags exist on the remote | ❌ | `git ls-remote --tags origin` returns **nothing**. All 14 tags are local-only. GitHub has zero tags and zero Releases despite 218 merged PRs |
| G6.5 — CHANGELOG for v1.0 written | ❌ | no `CHANGELOG*` file anywhere in the repo |
| G6.6 — A running deployment identifies itself | ❌ | `GET /health` returns `{"status": "OK"}` — no version, no commit SHA, no build time. `/health/extended` adds components but no identity. No version in the UI (no footer, no about). The deploy smoke check (`deploy.yml:158`) asserts only that the JSON parses. **Given a running prod, nothing served over HTTP says which commit it is** |
| G6.7 — Tag-vs-main deploy behavior decided and documented | ❌ | `build.yml` has **no `tags:` trigger**; `deploy.yml` chains off `build-and-push` on `main` only. Pushing a tag builds nothing and deploys nothing. The documented release act (§G6.1) is inert with respect to the pipeline |
| G6.8 — Release procedure written down | ❌ | no doc describes creating a GitHub Release, generating notes, or the tag→verify→announce sequence |
| G6.9 — Stale pipeline documentation corrected | ❌ | `build.yml:3-6` still says it builds "the **two** application images" and that "R8 will layer a deploy job on top of a new tag" — it builds 8 and deploy is live |

---

## 7. Known limitations — required before v1.0

**No "known limitations" section exists in any document.** This gate is not a
formality: several items below are currently contradicted by the marketing prose
in `README.md`. The section must state at minimum:

| # | Limitation | Currently disclosed? |
|---|---|---|
| L1 | **Paper account only.** `TRADING_MODE=paper`. No real-money session has ever run | ⚠️ `SECURITY.md` only, in the auth-acceptance rationale |
| L2 | **Market data is delayed, not live.** `reqMarketDataType(3)` (~15–20 min) for market-data and the chain fetcher; type 4 (delayed-**frozen**) for execution | ❌ one line in `docs/execution/ib-integration.md:46-49`. README:5,21,51 say "live IB tick stream" / "Real-time tick stream" / "real-time web desk". The public demo shows delayed prices as live 5-decimal mids |
| L3 | **Single IB session per userid.** Any web login to the IB portal kicks the container | ⚠️ `docs/execution/ib-integration.md:58` only |
| L4 | **Markets-closed serving.** The desk serves the last full grid when markets are closed; the frontend re-stamps it with `Date.now()` and asserts "Market open" | ❌ nowhere. FE-01/02/03 |
| L5 | **No alerting.** Dashboards only; nothing pages anyone | ❌ nowhere |
| L6 | **No enforced risk limits and no kill switch** | ❌ nowhere — the docs assert the opposite |
| L7 | **Signal→trade automation does not exist.** The desk is manual-only (`manual_mode = True`) | ❌ `docs/strategy/signals-to-trades.md` describes the removed pipeline in the present tense |
| L8 | **`VolConfig` is inert.** `VolEngine.apply_config` ends `del config` | ❌ README:73 claims it is "hot-reloaded into the engines via Redis pub/sub" |
| L9 | **No Python lockfile.** Docker builds fresh-resolve; today they land pandas 3.0.5 / numpy 2.4.6 into containers written for pandas 2.x | ❌ nowhere. DEP-01 |
| L10 | **Backups have never been restore-tested** | ⚠️ stated honestly inside `RESTORE.md`, unlinked from `docs/` |

---

## 8. Open legal / business questions

These are **not resolvable by audit**. Each needs a decision recorded before the
system is presented as a released product.

### 8.1 IB market-data redistribution (DEP-04, Wave 1)

The public read-only API and the six WebSocket channels redistribute
IB-derived market data — quotes, the option chain, and the vol surface built
from it — to anonymous internet users. IB's market-data subscriber agreements
prohibit redistribution for non-professional subscribers, and the delayed-data
entitlement does not change the redistribution clause.

- **Decision needed:** auth-gate raw quotes and chain data while keeping only
  derived analytics public (the cheapest defensible stance), or take the
  deployment private, or obtain a redistribution entitlement.
- **Aggravating factor:** L2 above — the data is currently *labelled* as live
  real-time, which is the entitlement class with the strictest terms.
- **Status:** ❌ undecided.

### 8.2 FRED terms of use — database-storage prohibition (Wave 1)

The FRED Terms of Use (2024) prohibit storing FRED content in a database. The
events pipeline persists FRED release dates into Postgres and omits the
mandatory attribution disclaimer.

- **Decision needed:** stop persisting FRED-sourced rows (cache in Redis with a
  TTL instead), or replace the source, or seek written permission. Add the
  required disclaimer either way.
- **Status:** ❌ undecided.

### 8.3 `ib-insync` → `ib_async` migration (DEP-03, Wave 1)

`ib-insync==0.9.86` is archived upstream. There is no patch path if the IB
protocol shifts — and the ib-gateway version-drift outage already demonstrated
that IB-side changes break this stack in production. `ib_async` is the
maintained community fork.

- **Decision needed:** migrate before v1.0, or stay pinned **and** publish a
  written runbook covering "IB protocol change breaks ib-insync" as an accepted
  risk with a recovery plan.
- **Status:** ❌ undecided. Compounded by DEP-01 (no lockfile), so the pin is
  not even reproducible across image rebuilds.

### 8.4 Public positioning of a paper system

The site presents live positions and P&L with no statement that the account is
simulated. `SECURITY.md` reasons about this internally; the public page does
not say it.

- **Decision needed:** a visible "paper account, delayed data" banner, or
  accept the ambiguity in writing.
- **Status:** ❌ undecided.

---

## 9. Minimum path to a defensible v1.0

Ordered by dependency, not by effort.

1. **Rotate `REDIS_PASSWORD`** (F-02). Independent of every code fix.
2. **Fix Theme B** (Wave 2): kill switch, notional/collar caps, fail-closed
   pre-trade gate, EX-01's timeout budget. This is the only theme whose failure
   mode is *unbounded action*.
3. **Fix Theme A**: one `core/pricing` consolidation closes 6 findings including
   3 Blockers.
4. **Fix Theme C**: thread the timestamps the backend already sends; render the
   WS status the hook already tracks.
5. **DI-02 / DI-03 / DI-35**: data-loss and silent-stall bugs.
6. **Before touching any Blocker, read Audit 7's T7-02 list.** Seven tests
   assert the defective behaviour as the intended contract; they go red when the
   bug is fixed. Fix the test *and* the code in the same change, or the revert
   is inevitable. Also fix T7-03 (SQLite with `PRAGMA foreign_keys` OFF) first —
   otherwise the DI fixes cannot be tested at all.
7. Fix Audit 8's 2 Blockers and promote `08-runbook-draft.md` into `docs/`,
   linked, after walking it once (G4.3).
8. **Run the live-verification drills.** 46 from Audits 3/4/5/9, plus Audit 8's
   host-side checks. They need the local stack and the EC2 host.
9. **Then re-run Audits 11 and 7** against the fixed tree — the docs cannot be
   corrected against a moving target, and the accuracy gates above are only
   meaningful once the code has stopped changing. Audit 7 re-runs because its
   central metric (Blocker-path coverage) is defined relative to the Blockers.
10. **Resolve §8.1–8.3** — these gate the *public* release specifically, and
    §8.1 may change what the API is allowed to serve, which changes the docs
    again.
11. Only then: version scheme, CHANGELOG, build identity, tag.

**Do not tag `v1.0` before step 11.** And when you do: the name `v1.0` is
already taken locally by an orphaned commit — decide whether to delete those 14
local tags or start the public line at a name that has never been used.
