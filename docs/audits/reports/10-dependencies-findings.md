# Audit 10 — Dependencies & Licensing — Findings Report

- **Audit spec:** [docs/audits/10-dependencies.md](../10-dependencies.md)
- **Date:** 2026-08-01
- **Auditor:** review agent (Audit 10)
- **Scope:** `pyproject.toml` (union of extras `dev,api,quant,ib,writer`, resolved), `frontend/package.json` + `package-lock.json`, all Dockerfiles + `docker-compose.yml` base images, GitHub Actions workflows, license inventory, data/API terms.
- **Method:** `pip install --dry-run --report` to resolve the full extras union without installing (92 packages), `pip-audit 2.10.1 -r <resolved> --no-deps`, `npm audit` against the committed lockfile (full + `--omit=dev`), `license-checker` on `frontend/node_modules`, license metadata from PyPI wheel metadata, manual pinning review, web research for maintenance status and data-provider terms.
- **Not run:** local Trivy / docker-scout image scan (neither tool available on the audit host). Compensating control: `.github/workflows/security-scan.yml` already runs a **weekly Trivy scan** of the 7 built images on GHCR (HIGH/CRITICAL, SARIF to the Security tab). Gap noted in DEP-11.

---

## Executive summary

The dependency posture is healthier than typical for a project at this stage: a root **MIT LICENSE exists** (the one potential Blocker in the checklist is absent), the **production frontend bundle has zero npm advisories**, there is **no GPL/AGPL anywhere** in either tree, the ib-gateway image is **pinned to `10.49.1c`** (the drift incident is fixed), all GitHub Actions are at least major-tag pinned, and a weekly Trivy image scan already exists.

The four High findings are: (1) **no Python lockfile** — container builds fresh-resolve `pip install ".[extra]"` on every build, and today's resolution already lands **pandas 3.0.5 and numpy 2.4.6** against code written for pandas 2.x / numpy 1.x, i.e. an untested double-major drift straight into the quant containers; (2) the `fastapi<0.112` ceiling freezes **starlette at 0.37.2**, which carries **9 published advisories** (triage: the multipart-DoS ones are unreachable — no form endpoints — but the Host/path URL-reconstruction ones are reachable on the public edge, and the line is unsupported upstream); (3) **ib-insync 0.9.86 is archived upstream** (author deceased, repo frozen) while the actively maintained drop-in fork `ib_async` exists — an IB API/Gateway protocol change would strand execution with no patch path; (4) the **public read-only API + WebSockets redistribute IB-sourced real-time data** (ticks, IV surfaces) on a public URL, which IB/exchange subscriber agreements prohibit for non-professional subscribers — this needs a decision before v1.0.

Medium findings cluster around floating Docker tags (`nginx:alpine`, minor-floating `redis:7`/`postgres:16`), Node 20 EOL (2026-04-30) still used in CI and the web build stage, the dev-only npm advisory pile (vite 5 / vitest 1 / eslint 8 — all EOL lines), FRED API terms (2024 update prohibits storing FRED content in a database; the pipeline stores FRED release dates in Postgres and the required "not endorsed" disclaimer is absent), and gaps in the existing weekly scan (no infra images, no pip-audit/npm-audit).

**Counts: 0 Blocker · 4 High · 7 Medium · 4 Low.**

---

## Findings table

| ID | Severity | Component | Finding |
|---|---|---|---|
| DEP-01 | **High** | pyproject / Docker builds | No Python lockfile; every image build fresh-resolves — resolution has already drifted to pandas 3.0.5 + numpy 2.4.6 (untested major bumps into quant containers) |
| DEP-02 | **High** | fastapi / starlette | `fastapi>=0.110,<0.112` freezes starlette 0.37.2 → 9 published advisories on the public API edge; line unsupported upstream |
| DEP-03 | **High** | ib-insync | Upstream archived (frozen at 0.9.86); no patch path for future IB API/Gateway changes; migration to `ib_async` fork undocumented |
| DEP-04 | **High** | Public API / IB data terms | Public read-only URL redistributes IB-derived real-time data (ticks, IV surface); IB subscriber agreements prohibit redistribution for non-pro subscribers — unresolved legal question |
| DEP-05 | Medium | FRED events source | FRED API ToU (2024 update) prohibits storing/caching FRED content in a database; release dates are persisted + publicly served; required disclaimer absent |
| DEP-06 | Medium | Docker base images | `nginx:alpine` fully floating (compose + web.Dockerfile runtime); `redis:7-alpine`, `postgres:16-alpine`, `python:3.11-slim`, `node:20-alpine` minor/patch-floating; no digest pins |
| DEP-07 | Medium | Node 20 (CI + web build stage) | Node.js 20 EOL 2026-04-30 — used in `web.Dockerfile` build stage and all CI `setup-node` steps; no security patches since May 2026 |
| DEP-08 | Medium | Frontend dev toolchain | 12 npm advisories, all dev-scope (vite 5, vitest 1, esbuild, babel, postcss, js-yaml, ws, form-data…); eslint 8.57 and vite 5 are EOL lines; prod bundle clean |
| DEP-09 | Medium | GitHub Actions | Major-tag pins only; no SHA pinning on workflows with secret/OIDC access (`deploy.yml` AWS role, `docker/login-action`); `trivy-action@0.24.0` stale |
| DEP-10 | Medium | Maintenance risk (frontend prod) | `react-plotly.js` 2.6.0 effectively unmaintained (last release 2021); plotly.js pinned to 2.x line (3.x current); zustand 4 (5 current) |
| DEP-11 | Medium | security-scan.yml | Weekly Trivy job scans only the 7 built images; no infra images (postgres/redis/nginx/ib-gateway), no pip-audit, no npm audit, no license gate |
| DEP-12 | Low | Licensing (Python) | psycopg2-binary is LGPL-3.0 (only weak-copyleft dep); certifi MPL-2.0; arch NCSA — all compatible with MIT distribution; no action beyond documenting |
| DEP-13 | Low | Data-source attribution | ONS (OGL v3), Eurostat (CC BY 4.0), ECB, BoE reuse terms all require attribution; no attribution statements in README/frontend for calendar data |
| DEP-14 | Low | Dependency hygiene | fastapi 0.111 drags fastapi-cli/typer/rich into every api image (0.112+ made these opt-in); `pip install --upgrade pip` unpinned in Dockerfiles; two Postgres drivers (asyncpg + psycopg2) — justified by writer split, document it |
| DEP-15 | Low | pytest-asyncio resolution | Resolver lands pytest-asyncio 0.23.3 with pytest 9.1.1 — a combination never tested upstream (0.23.x predates pytest 9); symptom of DEP-01 |

---

## 1. Vulnerabilities

### 1.1 Python resolved set (pip-audit)

Resolved union of `dev,api,quant,ib,writer` on Python 3.11: **92 packages** (full pin list retained in audit scratch; regenerable via `pip install --dry-run --report`). `pip-audit -r <pins> --no-deps` result: **9 known vulnerabilities, all in one package — starlette 0.37.2**. Every other package (numpy, pandas, scipy, sklearn, redis-py, SQLAlchemy, asyncpg, psycopg2, uvicorn, httpx, pydantic, grpcio, protobuf, websockets…) is clean at today's resolution.

#### DEP-02 (High) — starlette 0.37.2 held back by the fastapi ceiling

`pyproject.toml` pins `fastapi>=0.110,<0.112` (resolves fastapi 0.111.1), and fastapi 0.111 requires `starlette<0.38`. Advisories on 0.37.2 and reachability triage:

| Advisory | Issue | Fixed in | Reachable here? |
|---|---|---|---|
| PYSEC-2026-161 | `request.url` reconstructed from unvalidated `Host` header | 1.0.1 | **Yes** — runs on every request; impact limited (no `url_for`/`RedirectResponse`/absolute-URL generation found in `src/`), but the API is public and fronted by nginx which forwards client `Host` |
| PYSEC-2026-248 | Request path not validated before URL reconstruction | 1.3.0 | **Yes** — same surface, same limited impact |
| PYSEC-2026-249 | `request.form()` lacked `max_fields`/`max_part_size` enforcement | 1.3.1 | No — no `Form(`/`File(`/`request.form()` usage in `src/` |
| PYSEC-2026-1943 (CVE-2024-47874) | Multipart parts without filename buffered unbounded in memory (DoS) | 0.40.0 | No — multipart parser only runs for form endpoints; none exist |
| PYSEC-2026-1941 (CVE-2025-54121) | Large multipart spool blocks event loop | 0.47.2 | No — same reason |
| PYSEC-2026-2281 | StaticFiles UNC-path SSRF on Windows | 1.1.0 | No — no `StaticFiles`, Linux containers |
| PYSEC-2026-2280 | `HTTPEndpoint` handler-selection flaw | 1.1.0 | No — no `HTTPEndpoint` usage (FastAPI routing only) |

**Real severity:** the individually reachable items are low-impact (URL-reconstruction with no absolute-URL sinks). The High rating reflects the aggregate posture: the public unauthenticated edge runs a framework line that is out of upstream support and accumulating advisories it will never receive — the next starlette CVE is inherited with no fix available inside the current pin.

**Recommendation (not applied):** raise the ceiling to the current fastapi line (which tracks starlette ≥ 1.3.1), run the API test suite, and adopt `fastapi-slim` or fastapi ≥ 0.112 to shed the CLI extras (see DEP-14). This is the single upgrade that clears the entire Python CVE list.

### 1.2 Frontend (npm audit)

- **Production graph (`npm audit --omit=dev`): 0 vulnerabilities.** Prod deps are only `react`, `react-dom`, `plotly.js-basic-dist-min`, `react-plotly.js`, `zustand`, `@dagrejs/dagre`.
- **Full graph: 12 advisories (2 critical, 6 high, 3 moderate, 1 low) — every one in devDependency chains:** `vitest`/`@vitest/coverage-v8` ≤ 3.2.5 (critical: vitest UI server arbitrary file read/execute — only exploitable when the vitest UI server is running locally), `vite` ≤ 6.4.2 (dev-server path traversal, `launch-editor` NTLM leak — dev server only), `esbuild` ≤ 0.24.2 (dev-server request forgery), `@babel/core`, `postcss`, `js-yaml` (via `@redocly/openapi-core` ← `openapi-typescript`), `ws`, `form-data`, `brace-expansion`.
- **Triage / real severity:** nothing here ships in the production bundle or runs in the prod nginx container; exploitation requires running the dev server / vitest UI and visiting a malicious site, or a poisoned build input. Real severity **Medium** as a cluster (DEP-08), amplified by the fact that the vulnerable majors are also **EOL lines**: eslint 8.57 (EOL Oct 2024), vite 5 (out of support window), vitest 1.x.
- **Recommendation:** one coordinated toolchain bump — vite 7, vitest 3/4 (+ coverage-v8 to match), eslint 9 flat-config with typescript-eslint 8, latest `openapi-typescript`. This clears all 12 advisories; it touches config files only, not app code.

### 1.3 Container images

Local Trivy/docker-scout unavailable → **image CVE scan not run in this audit**. Compensating control: `.github/workflows/security-scan.yml` scans the 7 built GHCR images weekly (HIGH/CRITICAL, `ignore-unfixed: true`, SARIF upload, job fails on findings). **Gap (DEP-11):** the four infra images (`postgres:16-alpine`, `redis:7-alpine`, `nginx:alpine`, `ghcr.io/gnzsnz/ib-gateway:10.49.1c`) and the obs-profile images are never scanned, and the workflow scans the `:latest` GHCR tag, which can lag what prod actually runs. See §5 for the proposed extended job.

---

## 2. Pinning & reproducibility

### DEP-01 (High) — no Python lockfile; drift is already live

Evidence:

- Every service image installs with `pip install ".[<extra>]"` from `pyproject.toml` ranges (e.g. `infrastructure/docker/api.Dockerfile:37`, `src/engines/vol/Dockerfile:24`). No `uv.lock`, no `requirements*.txt`, no constraints file anywhere in the repo. CI caches on `hashFiles('pyproject.toml')`, so CI and deploy-time builds can silently diverge from each other and from any previously tested state.
- Today's fresh resolution (this audit, 2026-08-01) yields **pandas 3.0.5** and **numpy 2.4.6** against pins of `pandas>=2.0` / `numpy>=1.24` — the codebase (core/vol, engines) was written on the pandas 2.x / numpy 1.x-2.x line. pandas 3.0 changes defaults (copy-on-write, string dtype) that can alter numeric pipeline behavior *silently*. Any image rebuilt since the pandas 3.0 release picked this up untested — in the vol/risk containers this is a wrong-numbers → bad-trades vector, which is why this is High, not Medium.
- Same mechanism, smaller blast radius: **DEP-15** — the resolver pairs `pytest-asyncio 0.23.3` with `pytest 9.1.1`, a combination that never existed upstream (0.23.x predates pytest 9).

**Recommended stance for v1.0 (matches the audit spec's suggestion):**
1. Commit **`uv.lock`** resolved for `--extra dev --extra api --extra quant --extra ib --extra writer` (single Python 3.11 line, per the existing `[tool.uv]` comment).
2. Dockerfiles install via `uv sync --frozen --extra <extra>` (or `uv export --extra <extra> -o /tmp/req.txt && pip install -r /tmp/req.txt` to keep pip-based images).
3. CI installs from the lock; add a `uv lock --check` step so pyproject/lock drift fails fast.
4. Upgrade cadence: weekly scheduled scan (§5) surfaces CVEs; deliberate `uv lock --upgrade` PRs monthly or on advisory.
5. Until the lock lands, at minimum add ceilings `pandas>=2.0,<3.0` and `numpy<3` and verify what pandas/numpy the currently deployed prod images actually contain (`docker exec <ctr> pip show pandas` on EC2 — do not rebuild first).

### DEP-06 (Medium) — Docker base image pinning

| Image | Where | State |
|---|---|---|
| `ghcr.io/gnzsnz/ib-gateway:10.49.1c` | compose | **Pinned** — drift incident remediated (commit 74ba2ceb) ✔ |
| obs profile (prometheus v2.55.0, cadvisor v0.49.1, loki 3.2.0, promtail 3.2.0, tempo 2.6.0, otel-collector 0.110.0, grafana 11.3.0) | compose | Pinned to exact versions ✔ |
| `postgres:16-alpine` | compose | Major-pinned, minor floating — acceptable, prefer `16.x-alpine` + digest |
| `redis:7-alpine` | compose | Major-pinned, minor floating — same |
| `nginx:alpine` | compose line 547 **and** `web.Dockerfile` runtime stage | **Fully floating** — this is the same class of tag that caused the ib-gateway outage; a breaking nginx major would ship silently into the public edge |
| `python:3.11-slim` | all 7 Python Dockerfiles (ARG default) | Minor-pinned; fine (3.11 security-supported to 2027-10); prefer digest pin for reproducibility |
| `node:20-alpine` | `web.Dockerfile` build stage | Pinned but **EOL** — see DEP-07 |

**Recommendation:** adopt a written pin policy: exact version tag minimum for anything on the request path, digest (`@sha256:`) for prod-deployed images, Renovate/Dependabot to move the pins deliberately. `nginx:alpine` → e.g. `nginx:1.29-alpine` in both places first.

### DEP-07 (Medium) — Node 20 EOL

Node.js 20 reached end-of-life **2026-04-30**; no security patches since May 2026. Used in: `infrastructure/docker/web.Dockerfile` build stage (`node:20-alpine`) and every `setup-node` step (`node-version: "20"`) in `ci.yml`, `build.yml`, `codeql.yml`. Runtime is unaffected (nginx serves the static bundle), so this is build/CI-chain exposure only — but that chain executes the entire npm graph. **Recommendation:** bump to `node:22-alpine` (LTS to 2027-04) + `node-version: "22"` everywhere; vite 5 supports Node 22, so this is independent of the DEP-08 toolchain bump.

### DEP-09 (Medium) — GitHub Actions pinning

All `uses:` are major-tag pinned (`actions/checkout@v4`/`@v5`, `setup-python@v5`, `setup-node@v4`, `docker/*@v3`/`v5`, `codeql-action@v3`, `aws-actions/configure-aws-credentials@v4`, `aquasecurity/trivy-action@0.24.0`). No `:latest`-class refs — good baseline. Gaps per the audit spec ("SHA-pinning for anything with secret access"):

- `deploy.yml` assumes an **AWS role via OIDC** (`aws-actions/configure-aws-credentials@v4`, line 81) — a compromised tag on that action or on `actions/checkout@v5` in the same job means AWS access. SHA-pin every action in `deploy.yml` (and `build.yml`, which holds GHCR write via `docker/login-action`).
- `aquasecurity/trivy-action@0.24.0` (security-scan.yml) is a 2024 release — bump and SHA-pin.
- Consider Dependabot `package-ecosystem: github-actions` to keep SHAs moving.

---

## 3. Maintenance risk

### DEP-03 (High) — ib-insync is archived upstream

- `ib-insync==0.9.86` (exact-pinned — good) — upstream repo archived after the author, Ewald de Wit, passed away in early 2024. Zero releases since; the PyPI project cannot be updated (maintainers lost access).
- Community continuation: **`ib_async`** under the `ib-api-reloaded` GitHub org — active in 2026, current docs line 2.x, BSD-2-Clause, started as a pure rename (`import ib_async as ibi`-level migration) and has since tracked newer IB API versions.
- Why this is High and not Medium: three containers (market-data, vol-engine chain fetcher, risk, execution — client IDs 1/2/3/5) depend on it against a **moving counterparty**: IB ships new Gateway/TWS versions on its own schedule (the gnzsnz `10.48→10.49` bump that caused the July outage is the precedent). A wire-protocol or behavior change that breaks ib-insync 0.9.86 has **no patch path** — execution and market data stop, positions go unmanaged until a migration is done under incident pressure.
- **Recommended strategy for v1.0 (document, choose one):**
  - **(a) Preferred:** migrate to `ib_async` (exact-pinned) in a dedicated PR before v1.0 — mechanical import rename + full `IB_RUN_INTEGRATION=1` paper suite + a soak window on paper. Removes the frozen-dependency risk while everything is calm.
  - **(b) Acceptable:** stay on `ib-insync==0.9.86` for v1.0, but (i) keep the ib-gateway image exact-pinned forever (done), (ii) write the migration runbook now so it is not designed mid-incident, (iii) file a v1.x issue to migrate.
  - Either way, record the decision in a `docs/` dependency-policy note — the audit spec calls this a known strategic risk that must be documented, not just known.

### Other maintenance flags

- **DEP-10 (Medium):** `react-plotly.js@2.6.0` — last release 2021, effectively unmaintained; it is a thin wrapper, so risk is compatibility with future React/plotly majors rather than CVEs. `plotly.js-basic-dist-min` pinned to the 2.x line while 3.x is current. `zustand@4` (5 is current, React-18 compatible). None urgent; consolidate into one frontend-deps issue for v1.x.
- **slowapi 0.1.10** — slow-moving single-maintainer project on the auth/rate-limit path; no known advisories; watch-list only.
- **eventkit / nest-asyncio** — pulled in by ib-insync, both quiet upstream; they disappear or refresh with the `ib_async` migration.
- **Duplicate/overlapping deps: none found that need action.** Single HTTP client (httpx) — no `requests`/`aiohttp` in `src/`. Two Postgres drivers (asyncpg + psycopg2-binary) is deliberate (async app stack vs sync db-writer image) — worth one comment line in pyproject (DEP-14). Single date lib (stdlib + tzdata).

---

## 4. Licensing

### 4.1 Repo license

**`LICENSE` present at repo root — MIT, © 2026 Valérian Darmenté** — and `pyproject.toml` declares `license = { text = "MIT" }`. The would-be Blocker in the audit checklist does not apply. Note: `frontend/package.json` has no `license` field (shows as UNLICENSED in scans); it is `"private": true` so nothing is published, but adding `"license": "MIT"` removes the scanner noise (Low, fold into DEP-14).

### 4.2 Python dependency licenses (92 resolved packages)

Full inventory in Appendix A. Distribution profile: overwhelmingly MIT / BSD / Apache-2.0 / ISC / PSF. Flags:

- **psycopg2-binary — LGPL-3.0 (with linking exception)** — the only copyleft-family license in either tree (DEP-12, Low). Used unmodified as an installed library inside a Docker image; LGPL obligations (provide license text, allow relinking) are met trivially for an unmodified pip-installed dylib and do not contaminate the MIT codebase. No action needed; record the rationale.
- certifi — **MPL-2.0** (file-level copyleft; unmodified use = compliant).
- arch — **NCSA** (permissive), pandas/numpy/scipy/sklearn — BSD, typing_extensions/greenlet — PSF-2.0, email-validator — Unlicense. All MIT-compatible.
- **No GPL or AGPL anywhere in the resolved set.**

### 4.3 Frontend licenses

`license-checker --production --summary`: MIT 198, ISC 40, BSD-3 23, BSD-2 8, BlueOak-1.0.0 2, Zlib 1, 0BSD 1, Apache-2.0 1, Unlicense 1, UNLICENSED 1 (= the private root package itself), plus 3 "MIT*" (guessed) and one `Custom: jsonlint` (dev chain; jsonlint is MIT). Full graph adds Python-2.0 (argparse shim), CC-BY-4.0 (caniuse-lite — attribution satisfied by the package's own metadata). **No copyleft in the shipped bundle.**

### 4.4 Data / API terms (research summary + open questions)

What the system actually ingests matters here: the events pipeline (`src/api/orchestration/events/sources/`) stores **release-calendar dates** (FRED `releases/dates` API + scraped public calendar pages from ECB, BoE, federalreserve.gov, Eurostat, ONS) — not statistical series. The IB feed, by contrast, supplies **real-time market data** that flows to the public read-only API and WebSockets.

| Source | Terms (as researched 2026-08-01) | Assessment |
|---|---|---|
| **IB market data** | IB/exchange subscriber agreements prohibit redistribution of real-time, delayed, streaming and **derived** data outside the uses allowed for the subscriber class; non-professional subscriptions are personal-use only; derived-data display generally requires consent. | **DEP-04 (High, open question)** — see below |
| **FRED API** | ToU (updated 2024) requires the disclaimer *"This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis"*, requires attribution, and — per the 2024 update — **prohibits storing, caching or archiving FRED content or incorporating it into any database** and providing stored content to third parties. | **DEP-05 (Medium, open question)** — see below |
| **Eurostat** | Standard reuse policy: CC BY 4.0 — reuse and redistribution permitted with attribution. | Compliant if attribution added (DEP-13) |
| **ONS** | Open Government Licence v3 — reuse permitted, attribution statement required ("Contains public sector information licensed under the OGL v3.0"). | Compliant if attribution added (DEP-13) |
| **ECB** | Website/calendar content free to reuse with source acknowledgement (ECB reuse policy). | Compliant if attribution added (DEP-13) |
| **BoE / Federal Reserve (FOMC calendar)** | Public factual meeting dates; BoE site content reusable with acknowledgement; US federal government works are public domain. | Compliant; add acknowledgement for BoE (DEP-13) |

**DEP-04 (High) — IB redistribution vs the public read-only API.** The public URL (valeriandarmente.dev, read-only tier) serves IB-derived content: live/last ticks over `/ws/ticks`, IV smiles/surfaces built from IB option chains, risk figures derived from IB positions/prices. Under IB's market-data agreements this is redistribution of (at minimum) derived data, and for raw quote display, of market data itself — prohibited for a non-professional subscriber without distributor agreements. FX spot from IDEALPRO carries no exchange fee, but the IB subscriber agreement's redistribution clause is not exchange-specific. **Open questions to resolve before v1.0:** (1) does the paper-account data feed carry the same contractual restrictions (contractually: yes, same subscriber agreement); (2) is a fully-derived surface (SVI parameters, z-scores) "derived data" requiring consent, or transformed beyond recognition; (3) is a personal portfolio page shown publicly "personal use". **Recommendation:** decide a stance and implement one of: (a) auth-gate everything price-derived, leaving only system health public; (b) auth-gate raw ticks/quotes and delayed-stamp the derived analytics; (c) obtain written IB confirmation for the current shape. Cheapest defensible default is (b) + removing raw bid/ask/last from the unauthenticated tier.

**DEP-05 (Medium) — FRED storage prohibition + missing disclaimer.** `sources/fred.py` calls `api.stlouisfed.org/fred/releases/dates` and the pipeline persists results to Postgres (events repository) and re-serves them via the public API — on a plain reading of the 2024 ToU update ("no storing… or incorporating any FRED Content in any database"), persisted-and-republished release dates are inside the prohibited zone, even though the payload is minimal factual metadata. The mandatory not-endorsed disclaimer appears nowhere in the repo (README/frontend grep: no match). **Recommendation:** (1) add the FRED disclaimer + attribution to the README and the frontend footer/about; (2) re-read the current ToU verbatim (fetch was 403 for this audit — resolve manually) to confirm whether cached release *dates* fall under "FRED Content" and whether the caching clause has a transient-use carve-out; (3) if it does apply, the same dates are obtainable from the Fed's own public calendar pages (already scraped for FOMC) — swapping the source removes the constraint entirely.

**DEP-13 (Low):** add a "Data sources" section (README and/or frontend about page) with the OGL v3 statement (ONS), CC BY 4.0 attribution (Eurostat), and ECB/BoE source acknowledgements. Zero-risk goodwill fix that also documents the pipeline.

---

## 5. Proposed weekly CI scan job (proposal only — not committed)

The existing `security-scan.yml` (weekly Trivy on the 7 built images) stays; the proposal below extends the same schedule with the layers this audit had to run by hand. Add as a new workflow or extra jobs in `security-scan.yml`:

```yaml
# Proposed addition to .github/workflows/security-scan.yml (or dependency-scan.yml)
# Weekly: pip-audit on the locked/resolved Python set, npm audit on the frontend
# lockfile, Trivy on the four infra images, and a license gate.

  pip-audit:
    name: pip-audit (all extras)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Resolve full extras union (no install)
        run: |
          python -m pip install -q pip-audit
          python -m pip install --dry-run --ignore-installed --quiet \
            --report resolved.json ".[dev,api,quant,ib,writer]"
          python -c "import json;print('\n'.join(f\"{i['metadata']['name']}=={i['metadata']['version']}\" for i in json.load(open('resolved.json'))['install'] if i['metadata']['name']!='fxvol'))" > resolved.txt
        # Once uv.lock is committed (DEP-01), replace the two steps above with:
        #   uv export --all-extras --no-emit-project -o resolved.txt && uv lock --check
      - name: Audit
        run: python -m pip_audit -r resolved.txt --no-deps --strict

  npm-audit:
    name: npm audit (frontend lockfile)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults: { run: { working-directory: frontend } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - name: Audit production graph (gate)
        run: npm audit --omit=dev --audit-level=high
      - name: Audit full graph (report only)
        run: npm audit || true

  trivy-infra:
    name: Trivy scan (infra image ${{ matrix.image }})
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        image:
          - postgres:16-alpine
          - redis:7-alpine
          - nginx:alpine            # replace with the pinned tag per DEP-06
          - ghcr.io/gnzsnz/ib-gateway:10.49.1c
    steps:
      - uses: aquasecurity/trivy-action@0.24.0   # bump + SHA-pin per DEP-09
        with:
          image-ref: ${{ matrix.image }}
          format: table
          severity: HIGH,CRITICAL
          exit-code: "1"
          ignore-unfixed: true

  license-gate:
    name: License inventory gate
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - name: Frontend — forbid copyleft in prod graph
        working-directory: frontend
        run: |
          npm ci --ignore-scripts
          npx --yes license-checker --production \
            --failOn "GPL;AGPL;LGPL;SSPL;EUPL;CC-BY-NC"
      # Python side: pip-licenses over the resolved set once uv.lock exists;
      # psycopg2-binary (LGPL, allow-listed per DEP-12) is the known exception.
```

---

## Appendix A — Python license inventory (92 resolved packages, extras union, 2026-08-01)

| Package | Version | License |
|---|---|---|
| aiosqlite | 0.20.0 | MIT |
| alembic | 1.13.3 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.14.2 | MIT |
| arch | 8.0.0 | NCSA |
| ast_serialize | 0.6.0 | MIT |
| async-timeout | 5.0.1 | Apache-2.0 |
| asyncpg | 0.29.0 | Apache-2.0 |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.4.2 | BSD-3-Clause |
| colorama | 0.4.6 | BSD |
| coverage | 7.15.2 | Apache-2.0 |
| Deprecated | 1.3.1 | MIT |
| dnspython | 2.8.0 | ISC |
| email-validator | 2.3.0 | Unlicense |
| eventkit | 1.0.3 | BSD |
| fastapi | 0.111.1 | MIT |
| fastapi-cli | 0.0.32 | MIT |
| googleapis-common-protos | 1.75.0 | Apache-2.0 |
| greenlet | 3.5.4 | MIT AND PSF-2.0 |
| grimp | 3.15 | BSD |
| grpcio | 1.83.0 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| httpx | 0.27.2 | BSD-3-Clause |
| ib-insync | 0.9.86 | BSD |
| idna | 3.18 | BSD-3-Clause |
| import-linter | 2.13 | BSD |
| iniconfig | 2.3.0 | MIT |
| Jinja2 | 3.1.6 | BSD |
| joblib | 1.5.3 | BSD-3-Clause |
| librt | 0.13.0 | MIT |
| limits | 5.8.0 | MIT |
| Mako | 1.3.12 | MIT |
| markdown-it-py | 4.2.0 | MIT |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT |
| mypy | 2.3.0 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| nest-asyncio | 1.6.0 | BSD |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api / -sdk / -proto / -exporter-otlp-proto-common / -exporter-otlp-proto-grpc / -semantic-conventions | 1.44.0 / 0.65b0 | Apache-2.0 |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.5 | BSD |
| pathspec | 1.1.1 | MPL-2.0 |
| patsy | 1.0.2 | BSD |
| pluggy | 1.6.0 | MIT |
| prometheus_client | 0.20.0 | Apache-2.0 |
| protobuf | 7.35.1 | BSD-3-Clause |
| psycopg2-binary | 2.9.12 | **LGPL-3.0 (w/ exception)** |
| pydantic / pydantic_core / pydantic-settings | 2.13.4 / 2.46.4 / 2.14.2 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| PyJWT | 2.13.0 | MIT |
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 0.23.3 | Apache-2.0 |
| pytest-cov | 7.1.0 | MIT |
| python-dateutil | 2.9.0.post0 | BSD; Apache-2.0 |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| redis | 5.3.1 | MIT |
| rich / rich-toolkit | 15.0.0 / 0.20.3 | MIT |
| ruff | 0.11.13 | MIT |
| scikit-learn | 1.6.1 | BSD |
| scipy | 1.17.1 | BSD |
| shellingham | 1.5.4 | ISC |
| six | 1.17.0 | MIT |
| slowapi | 0.1.10 | MIT |
| sniffio | 1.3.1 | MIT; Apache-2.0 |
| SQLAlchemy | 2.0.51 | MIT |
| starlette | 0.37.2 | BSD-3-Clause |
| statsmodels | 0.14.6 | BSD |
| structlog | 24.4.0 | MIT OR Apache-2.0 |
| threadpoolctl | 3.6.0 | BSD |
| typer | 0.27.0 | MIT |
| typing-inspection | 0.4.2 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.3 | Apache-2.0 |
| uvicorn | 0.28.1 | BSD-3-Clause |
| watchfiles | 1.2.0 | MIT |
| websockets | 17.0.1 | BSD-3-Clause |
| wrapt | 2.3.0 | BSD-2-Clause |

*(remaining resolved packages not individually listed above are MIT/BSD utility wheels; full pin list regenerable with the §5 resolve step)*

## Appendix B — Sources

- ib_async fork: https://github.com/ib-api-reloaded/ib_async (and archived https://github.com/mattsta/ib_insync)
- Node.js 20 EOL 2026-04-30: https://dev.to/matheus_releaserun/nodejs-20-end-of-life-migration-playbook-for-april-30-2026-2onh , https://tuxcare.com/blog/node-eol/
- FRED API ToU: https://fred.stlouisfed.org/docs/api/terms_of_use.html (403 to automated fetch — clause summary via 2024 ToU-update announcement https://news.research.stlouisfed.org/2024/06/weve-updated-our-terms-of-use-action-requested/ ; verify manually)
- IB market data agreements: https://www.interactivebrokers.com/en/accounts/forms-and-disclosures-market-data.php
- Starlette advisories: PYSEC-2026-161/248/249/1941/1943/2280/2281 via pip-audit (OSV/PyPI advisory DB)
