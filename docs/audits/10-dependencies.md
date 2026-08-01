# Audit 10 — Dependencies & Licensing

## Why

The stack pulls in heavyweight dependency trees (ib_insync, FastAPI stack, arch,
sklearn, plotly, React toolchain) plus third-party Docker images. Known CVEs in any of
them are inherited silently, and the repo is public — so license obligations apply to
*us* as distributor. This is the cheapest audit to run and the cheapest class of finding
to fix before a release; after v1.0 it becomes an ongoing process, so the audit also
sets up the automation.

## Scope

- `pyproject.toml` (all extras: dev/api/quant/ib/writer) — resolved tree, not just
  direct deps.
- `frontend/package.json` + lockfile.
- Docker base images: `postgres:16-alpine`, `redis:7-alpine`, `nginx:alpine`,
  `gnzsnz/ib-gateway` (pinned — verify), `python` and `node` bases in our Dockerfiles.
- GitHub Actions used in workflows (tag pinning).

## What to check

### Vulnerabilities
- [ ] `pip-audit` across the fully-resolved environment (union of extras).
- [ ] `npm audit` on the frontend lockfile (prod deps weighted over dev).
- [ ] Image scan (trivy or `docker scout`) on the four infra images + our built images.
- [ ] Triage each hit: reachable in our usage or not — findings get real severity, not
      scanner severity.

### Pinning & reproducibility
- [ ] Python: no lockfile today (pyproject ranges + pip cache key). Decide and document
      the v1.0 stance — `uv lock` committed is the likely recommendation; CI + deploy
      must install the same resolved set.
- [ ] Docker: every base image pinned to digest or specific tag (`:latest` ban — the
      ib-gateway drift incident is the precedent); same in compose.
- [ ] GitHub Actions pinned to major tag at minimum (SHA-pinning for anything with
      secret access).

### Maintenance risk
- [ ] ib_insync: project archived upstream (author deceased, community fork `ib_async`
      exists) — document the chosen fork/pin strategy for v1.0; this is a known
      strategic dependency risk.
- [ ] Flag any dep unmaintained >2 years with open CVEs or Python 3.11+ warnings.
- [ ] Duplicate/overlapping deps (two HTTP clients, two date libs…) — consolidation list.

### Licensing
- [ ] Full license inventory (pip-licenses, license-checker) — flag copyleft (GPL/AGPL)
      in the distributed set; verify compatibility with this repo's own license.
- [ ] Repo has a LICENSE file at all (release blocker if absent for a public repo).
- [ ] Data/API terms: FRED/ECB/BoE/Eurostat/ONS terms of use permit our storage and
      redisplay; IB market data redistribution rules vs the public read-only API —
      confirm what the public endpoint may legally serve.

## Method

Automated scans first (all named tools run in CI-like conditions), then manual triage
and the maintenance/licensing review. Output includes a proposed CI job so this audit
becomes continuous (scheduled weekly scan) instead of one-shot.

## Deliverable & exit criteria

- Findings report + dependency policy doc (pinning stance, upgrade cadence, scan
  automation) + the new scheduled CI scan job (as a proposal/PR).
- **Exit:** zero known-exploitable reachable CVEs; all images pinned; LICENSE present;
  data-redistribution question answered; zero Blocker/High open.