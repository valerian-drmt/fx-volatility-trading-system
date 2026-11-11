# Dependency policy

How the stack pins, refreshes, scans and licenses what it depends on
(audit 10, tickets #303 #304 #305 #306 #308). The triage log at the bottom is
the record a scanner finding points to.

## Sources of truth

| What | Pinned where | Resolved set | Refreshed by |
|---|---|---|---|
| Python | `pyproject.toml` ranges (a ceiling on every runtime dependency) | `uv.lock` (universal, Python 3.11 line) | Dependabot `uv` ecosystem, weekly, minor + patch grouped |
| Frontend | `frontend/package.json` (`^` ranges) | `frontend/package-lock.json` | Dependabot `npm`, weekly, minor + patch grouped |
| Base images | `ARG *_IMAGE=<tag>@sha256:<digest>` in every Dockerfile; `image:` in `docker-compose.yml` | the digest | Dependabot `docker` + `docker-compose`, weekly |
| GitHub Actions | `uses: owner/action@<sha> # vX.Y.Z` | the sha | Dependabot `github-actions`, weekly |
| IB Gateway | `ghcr.io/gnzsnz/ib-gateway:<build>@sha256:<digest>` in compose and `remote-deploy.sh` | the digest | by hand, after a paper soak (see below) |

Rules:

- CI installs with `uv sync --locked` and checks `uv lock --check`; every
  Dockerfile installs with `uv sync --locked --no-dev --extra <container>`.
  The image runs exactly the set the tests ran on.
- A major bump is its own PR with the test evidence; Dependabot groups only
  minor and patch bumps.
- Exact pins (`==`) need a reason in a comment next to them. Today: `ib-insync`
  (below).
- No `latest`, no bare major tag (`16-alpine`), anywhere the stack runs.
  `tests/unit/infrastructure/test_image_pins.py` enforces the tag + digest
  form, the ceilings, the lockfile and the Node line.
- Node follows the current LTS (22). Python stays on 3.11 until a deliberate
  bump of `requires-python`.

## Bumping something

```bash
# one Python package
#   1. widen or move the range in pyproject.toml
uv lock --upgrade-package starlette          # 2. re-resolve that package only
uv sync --locked --all-extras                # 3. local env follows the lock
python -m pytest                             # 4. evidence
# one frontend package
cd frontend && npm install vite@^8 --save-dev && npm run lint && npm run typecheck && npm test && npm run build
# a base image: paste the new tag@digest into the ARG (Dependabot does this for you)
```

The ib-gateway build is the exception: Dependabot ignores it and the pin moves
by hand (compose default, `remote-deploy.sh`, `test_image_pins.py`). The 10.48 ->
10.49 jump that crash-looped prod on a persisted volume is the precedent; since
#415 the deploy copies the new build into the volume itself
(`ensure_ib_gateway_build`, `infrastructure/ib-gateway/README.md`). After the
deploy, check that the engines log `ib_connected` with the new server version;
the previous build stays in the volume, so rolling back to the old pin boots.

## Weekly scan (`.github/workflows/security-scan.yml`)

Monday 03:00 UTC and on dispatch (optionally with an image tag).

| Job | Scope | Fails on |
|---|---|---|
| `pip-audit` | `uv export --all-extras` of `uv.lock` | any known vulnerability not in `PIP_AUDIT_IGNORE` |
| `npm-audit` | `package-lock.json`, shipped packages (`--omit=dev`) | high / critical; the dev toolchain audit is advisory |
| `licenses` | `scripts/ci/check_licenses.py` (Python metadata), `scripts/ci/check_licenses_npm.mjs` (shipped npm packages) | copyleft or unidentified license outside the exceptions |
| `trivy-built` | the 8 images we build, at the `sha-` tag prod reports on `/health` | HIGH / CRITICAL with a fix, not in `.trivyignore` |
| `trivy-infra` | every pinned image in `docker-compose.yml` (postgres, redis, nginx, the obs profile, the socket proxy, ib-gateway) | same |

Findings land in the Security tab (SARIF, one category per image) and in the
run log. Triage rule: a finding is judged on reachability in this stack, not
on the scanner's severity. The decision goes in the log below and, when it is
an accept, in the matching ignore list (`PIP_AUDIT_IGNORE`, `.trivyignore`,
npm `overrides`) with the date to revisit.

## Licenses

The repository is MIT. Allowed in the shipped set: MIT, BSD, Apache-2.0, ISC,
PSF, 0BSD, Zlib, CC0, Unlicense, NCSA, BlueOak-1.0.0, MPL-2.0 (file-level
copyleft, imported unchanged). Not allowed: GPL, AGPL, or a package with no
license metadata.

Exceptions on record:

| Package | License | Why it is fine |
|---|---|---|
| `psycopg2-binary` | LGPL-3.0 with linking exception | dynamically linked, never modified or vendored; db-writer only (the async path is asyncpg, BSD) |
| `stack-trace` (npm, via plotly) | MIT per its LICENSE file | the manifest has no `license` field |

The Python gate reads `importlib.metadata`, the npm gate reads the lockfile's
non-dev tree; both print the full inventory on every run.

## Maintenance-risk register

| Dependency | Risk | Decision (2026-11-10) |
|---|---|---|
| `ib-insync==0.9.86` | archived upstream (author deceased); no patch path if the IB wire protocol changes | **stay pinned for v1.0**, migrate to `ib_async` in v1.1 (#306, runbook below) |
| `react-plotly.js` | was unmaintained (2.6.0, 2022) | resolved: 4.1.0 (2026) on plotly.js 3.x, shipped with #308 |
| `slowapi` | slow upstream (0.1.10) | accepted for v1.0; the replacement is a starlette middleware with a Redis counter, one file, when a CVE or a starlette break forces it |
| `eventkit`, `nest-asyncio` | pulled by ib-insync, quiet upstream | go away with the ib_async migration |
| `pandas` 3.x | copy-on-write and string dtype semantics | held back `<3` for v1.0; revisit in v1.1 with the quant suite green on 3.x |
| React Compiler lint rules (`eslint-plugin-react-hooks` 7 preset) | 45 pre-existing findings (set-state-in-effect 18, refs 12, purity 7, immutability 3, error-boundaries 2) | the two classic hook rules stay enforced; adopting the compiler rules is its own ticket |

### ib_async migration runbook (#306)

Why not now: the migration can only be validated by the IB paper suite
(`IB_RUN_INTEGRATION=1`) and a soak, and the single paper session is owned by
prod 24/5 (`docs/ops/local-stack.md`). A mechanical rename shipped without
that evidence would be an untested change on the execution path.

Trigger for doing it earlier than v1.1: an IB Gateway build the engines cannot
log in to or subscribe from, with `ib-insync` as the cause (compare with the
volume-drift and competing-session diagnoses in `docs/ops/deployment.md`
first).

1. Branch; in `pyproject.toml` replace `ib-insync==0.9.86` with
   `ib-async==2.1.0` (exact pin, same reason); `uv lock --upgrade-package ib-async`.
2. Rename the imports: `ib_insync` -> `ib_async` (`grep -rl ib_insync src tests`,
   the `[ib]` extra, the four engines, `core/execution/contract_builder.py`
   docstring, the test fixtures in `tests/fixtures/ib.py`). Keep `tzdata`.
3. Review the behaviour differences listed in the ib_async changelog for the
   calls this stack uses: `IB.connectAsync`, `reqMktData` / `pendingTickersEvent`,
   `reqHistoricalDataAsync`, `reqSecDefOptParamsAsync`, `placeOrder` /
   `orderStatusEvent` / `execDetailsEvent`, `reqAccountSummaryAsync`
   (`$LEDGER` tags), `util.patchAsyncio`.
4. `python -m pytest` (the unit suite mocks IB through the fixture factories;
   it must stay green unchanged).
5. Weekend window: stop the prod gateway (`docker compose stop ib-gateway` on
   the host), run `IB_RUN_INTEGRATION=1 python -m pytest -m integration` locally
   against the paper account, restart prod.
6. Deploy with `TRADING_MODE=read_only` for one week of market hours: quotes,
   chains, positions, fills reconciliation all visible on the desk; then lift
   the read-only mode.
7. Rollback at any step: revert the pin and the rename, `uv lock`, redeploy;
   images rebuild from the lock, nothing else changes.

## Triage log

| Date | Finding | Where | Decision |
|---|---|---|---|
| 2026-11-10 | starlette 0.37.2 carried 9 published advisories behind the `fastapi<0.112` ceiling | pip-audit (pre-lock) | closed: fastapi 0.162.2 / starlette 1.10.0 in `uv.lock` (#303) |
| 2026-11-10 | fresh `pip install` resolved pandas 3.0 / numpy 2.4 against code written for pandas 2 | reproducibility (10#5) | closed: ceilings `pandas<3`, `numpy<3`, lock committed, Docker and CI install from it |
| 2026-11-10 | pip-audit on `uv.lock`, all extras | pip-audit | 0 findings |
| 2026-11-10 | npm audit, shipped packages | npm audit | 0 findings |
| 2026-11-10 | npm audit, dev toolchain: 10 moderate, all `minimatch` "globparts amplification" DoS reached through eslint / typescript-eslint | npm audit (advisory job) | accepted: lint-time CLI, no untrusted glob input; revisit when eslint's tree moves to a patched minimatch |
| 2026-11-10 | `node:20-alpine` end-of-life; `nginx:alpine`, `postgres:16-alpine`, `redis:7-alpine`, `python:3.11-slim` floating | image pins (10#6) | closed: tag + digest everywhere, Node 22, Dependabot docker ecosystems (#305) |
| 2026-11-10 | vite 5 / vitest 1 / eslint 8 advisories | npm audit (10#2) | closed: vite 8.3, vitest 5.1, eslint 10.14, typescript-eslint 8.72, react-plotly.js 4.1 (#308) |
| 2026-11-10 | ib-insync archived upstream | 10#8 | pinned for v1.0 with the runbook above; v1.1 migration ticket |
| 2026-11-10 | First Trivy baseline (run 42828512811): our 8 images carry only the base image's unused `pip` 24.0 / `wheel` 0.45 / `jaraco.context` 5.3 (3 HIGH) | trivy-built | fixed: the Python images uninstall pip / setuptools / wheel after copying uv (the app runs from the uv venv) |
| 2026-11-10 | nginx, redis, frontend: one MEDIUM each (zlib CVE-2026-4738, fix in Alpine zlib 1.4) | trivy-infra | below the gate; picked up by the next digest refresh from Dependabot |
| 2026-11-10 | postgres 16.15: 1 CRITICAL + 24 HIGH, all Go stdlib 1.24.6 inside `gosu` | trivy-infra | not reachable: gosu only drops privileges at container start, no network input; wait for the upstream image rebuild (Dependabot digest bump), revisit 2026-12-10 |
| 2026-11-10 | docker-socket-proxy 0.3.0: musl CVE-2025-26519 (2 HIGH), latest upstream release | trivy-infra | accepted: internal network only, reads the socket for the log shipper, no user input; revisit 2026-12-10 |
| 2026-11-10 | obs profile (grafana 11.3, prometheus 2.55, alertmanager 0.27, loki / promtail 3.2, tempo 2.6, otelcol 0.110, cadvisor 0.49): 45 CRITICAL, ~760 HIGH, mostly old Go stdlib and bundled libraries | trivy-infra | closed by #415: every obs image on its latest release, promtail replaced by Alloy (see the 2026-11-11 row) |
| 2026-11-11 | obs profile after #415 (prometheus 3.16, alertmanager 0.35, grafana 13.0.2, loki 3.7.9, alloy 1.21.3, tempo 2.10.10, otelcol 0.165, cadvisor 0.55.1, docker-socket-proxy 0.5.0): 6 CRITICAL, ~210 HIGH left (fixed upstream, unfixed ignored), down from 45 / ~760. Grafana: Go stdlib in its bundled elasticsearch plugin (unused; plugin preinstall disabled) and alpine curl; cAdvisor: Go stdlib and alpine openssl; socket-proxy: alpine pcre2 / openssl; the rest 1-3 each | local trivy 0.69.3 | accepted: each image is the latest upstream release, all run on the internal network with no user input (Grafana loopback-only behind login); Dependabot's infra-images group takes the rebuilds; revisit 2026-12-10 |
