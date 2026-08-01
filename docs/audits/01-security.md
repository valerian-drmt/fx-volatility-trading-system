# Audit 1 — Security & Attack Surface

## Why

The stack is publicly reachable (`valeriandarmente.dev`, read-only API + auth-gated write
surface) and holds live credentials (IB, DB, VNC) in AWS SSM. A single auth-boundary
mistake exposes trade submission to the internet; a single logging mistake leaks a secret
into a public repo or a served log. This is the highest-stakes audit.

## Scope

- `src/api/` — all 16 routers, middleware (rate limit, logging), auth dependency wiring.
- `infrastructure/` — nginx confs (dev/prod/frontend), Dockerfiles, `docker-compose.yml`
  (networks `fxvol-public` / `fxvol-internal` / `fxvol-external`, exposed ports).
- `scripts/` — deploy + secrets loading scripts (read code only, never execute).
- `.github/workflows/` — CI secrets usage, deploy pipeline permissions.
- Frontend API client (token handling, storage).

## What to check

### Auth boundary
- [ ] Every mutating endpoint (POST/PUT/PATCH/DELETE) and every `/dev/*`, `/admin/*`,
      config endpoint requires auth — enumerate routes programmatically, not by eye.
- [ ] WebSocket endpoints: what can an unauthenticated client subscribe to or send?
- [ ] Auth implementation: token verification, expiry, timing-safe comparison, no
      default/fallback credentials.
- [ ] Rate limiting (slowapi) actually applied to auth + write endpoints, keyed correctly
      behind nginx (real client IP vs proxy IP).

### Secrets
- [ ] No secret ever reaches: logs (structlog processors), error responses, OpenAPI schema,
      frontend bundle, git history, Docker image layers (`docker history` review of Dockerfiles).
- [ ] `DATABASE_URL` / `REDIS_URL` never echoed in exception messages or health endpoints.
- [ ] SSM parameter access is least-privilege (IAM policy review, `fxvol-dev` vs `admin`).

### Network & container hardening
- [ ] Only nginx is on `fxvol-public`; postgres/redis/IB gateway have no published ports
      in prod compose.
- [ ] nginx: TLS config, security headers, no accidental proxying of internal-only routes,
      request size limits.
- [ ] Containers run non-root where feasible; no `privileged`, no docker socket mounts.
- [ ] CORS configuration on the API — origin allowlist, not `*` with credentials.

### Injection & input handling
- [ ] SQL: all queries through SQLAlchemy ORM/params (grep for f-string SQL).
- [ ] SSRF in the events pipeline (FRED/ECB/BoE/FOMC/Eurostat/ONS fetchers) — URLs built
      from config only, ids passed via `params=` (known CodeQL pattern in this repo).
- [ ] Pydantic schemas actually validate ranges/enums on write endpoints (order size,
      symbols, config values).

### CI / supply chain surface
- [ ] Deploy workflow: least-privilege AWS role, no secret echoed in workflow logs.
- [ ] No workflow triggerable by fork PRs with secret access (`pull_request_target` review).

## Method

Static review + route enumeration script (dump FastAPI routes with their dependencies) +
CodeQL alert triage + nginx conf line-by-line review. **No pentest tooling against prod.**
Anything requiring a live check is run against the local stack only, by Valérian.

## Deliverable & exit criteria

- Findings report; route-by-route auth matrix as an appendix.
- **Exit:** zero Blocker/High open; auth matrix shows every mutating route gated; secrets
  checklist 100% verified.