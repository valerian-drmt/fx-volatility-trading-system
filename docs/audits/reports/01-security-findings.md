# Audit 1 — Security & Attack Surface — Findings

**Scope:** `src/api/` (16 routers, middleware, auth), `src/engines/execution/` HTTP surface,
`infrastructure/` (nginx, Dockerfiles, compose, AWS IAM), `scripts/` (read-only),
`.github/workflows/`, frontend API client.
**Method:** static review + programmatic FastAPI route enumeration (`PYTHONPATH=src`, app imported,
route table dumped with resolved dependency names — see appendix) + nginx/compose line review.
No live stack was started, no URL was hit, no secret-loading script was executed.
**Severity scale:** as defined in [`../README.md`](../README.md).

---

## Executive summary

The auth boundary itself is **sound and correctly wired**. Programmatic enumeration of all 115
routes shows **every mutating endpoint (POST/PUT/PATCH/DELETE) carries `Depends(require_write)`**,
with only three deliberate exceptions (`/auth/login`, `/auth/logout`, and the three stateless
pricing calculators). The token is HMAC-SHA256 signed, expiring, compared with
`hmac.compare_digest`; passwords are pbkdf2-hmac-sha256 (200k rounds) and login is fail-closed
until `AUTH_PASSWORD_HASH` is provisioned; a prod boot guard refuses the repo-default HMAC key.
There is **no f-string SQL anywhere** in `src/`, **no SSRF** in the events pipeline (every outbound
URL is a constant base with ids passed via `params=`), **no `pull_request_target`** in CI, **no
credential material committed** to the tree or reachable in history, and the frontend keeps **no
token in storage** (httpOnly cookie only). Deployment uses GitHub OIDC with no static AWS keys, no
inbound SSH, and secrets are read on-host from SSM — they never transit GitHub.

Three findings are **High**, none is a Blocker:

1. The rate-limit key is fully attacker-controlled behind nginx, so the 5/min brute-force throttle
   on the login endpoint — the single credential that unlocks trade submission — is bypassable.
2. The Redis password is printed in cleartext into the api's startup log on every boot.
3. An unauthenticated visitor can exhaust the api container's memory through unbounded Prometheus
   label cardinality.

The remaining findings are hardening gaps: two HTTP containers running as root, a traceback echoed
to the client, fail-open weak credential defaults when an SSM parameter is missing, an
unauthenticated internal order-placement API protected only by network isolation, and a privileged
cAdvisor container in a profile intended for the public host.

**Exit criteria status: NOT met** — 3 High findings open (0 Blocker). The auth matrix requirement
("every mutating route gated") **is** met.

---

## Findings table

| ID | Severity | Location | Description |
|---|---|---|---|
| F-01 | **High** | `docker-compose.yml:92`, `infrastructure/nginx/nginx.conf:78`, `src/api/middleware/rate_limit.py:17` | `FORWARDED_ALLOW_IPS: "*"` + XFF-append makes the slowapi key attacker-controlled → login brute-force throttle bypassable |
| F-02 | **High** | `src/api/main.py:107` | Redis password logged in cleartext at every api startup (`redis_url=` contains `redis://:<pass>@`) |
| F-03 | **High** | `src/api/middleware/timing.py:26` | Raw request path used as a Prometheus label → unauthenticated remote memory exhaustion of the api container |
| F-04 | Medium | `infrastructure/docker/api.Dockerfile`, `infrastructure/docker/execution.Dockerfile` | The two HTTP-serving containers run as root; all 5 engine images correctly set `USER fxvol` |
| F-05 | Medium | `src/api/routers/trade.py:753-762` | `/trade/submit` 500 handler returns exception type, message and 5 traceback frames to the client |
| F-06 | Medium | `docker-compose.yml:48,51,70,756`, `infrastructure/ec2/remote-deploy.sh:43` | Fail-open weak defaults: a missing SSM `REDIS_PASSWORD` silently ships `fxvol-dev` to prod (Grafana: `admin`) |
| F-07 | Medium | `src/engines/execution/main.py:268`, `src/api/routers/orders.py:52`, `src/api/routers/trade.py:716` | Order-placement API has no auth (network isolation only); api forwards untyped `dict` bodies |
| F-08 | Medium | `docker-compose.yml:626-654,686-695` | `privileged: true` cAdvisor (+ rootfs/docker mounts) in the `metrics` profile explicitly intended for prod |
| F-09 | Medium | `src/api/routers/auth.py:38-46`, `src/api/auth.py:72-87` | CSRF defence is `SameSite=Lax` alone — no token, no Origin check; the same site's apex serves separately-managed content |
| F-10 | Medium | `src/api/main.py:170-172` | Swagger UI + OpenAPI schema publicly served on the prod host, publishing the full write surface |
| F-11 | Medium | `src/api/routers/ws.py`, `src/api/ws/connection_manager.py:24,31` | 6 unauthenticated WebSockets incl. per-structure order/fill streams; channel registry grows unboundedly |
| F-12 | Low | `src/api/routers/auth.py:29-31` | Username validity leaks through response timing (KDF skipped on wrong username) |
| F-13 | Low | `src/api/schemas/auth.py:7-9` | `LoginBody` has no `max_length` on username/password |
| F-14 | Low | `src/api/routers/dev.py:1468-1482,1572` | `/dev/hardware` + `/dev/containers/metrics` expose host CPU/RAM/disk/GPU unauthenticated |
| F-15 | Low | `src/api/routers/orders.py:36`, `src/api/routers/positions.py:1249` | Internal hostname/port leaked in 503 detail on unauthenticated GET routes |
| F-16 | Low | `infrastructure/aws/deploy-perms.json:8-10` | `ssm:GetCommandInvocation` granted on `Resource: "*"` — the deploy role can read any command output in the account |
| F-17 | Low | `.github/workflows/ci.yml:1-18` | No top-level `permissions:` block (the only workflow without one) |
| F-18 | Low | `src/api/routers/trade.py:99-116` | Trade-preview `qty` and overrides carry no range bounds at the api tier |
| F-19 | Low | `src/api/routers/admin.py:10-12`, `src/api/routers/dev.py:5-7` vs `:40` | Stale/contradictory security comments describing the auth boundary incorrectly |
| F-20 | Low | `infrastructure/nginx/nginx.conf:44,53` | CSP `connect-src 'self' wss:` allows any wss origin; no `Permissions-Policy`; `server_tokens off` only in the 443 block |

**Counts:** Blocker 0 · High 3 · Medium 8 · Low 9.

---

## F-01 — Rate-limit key is fully attacker-controlled behind nginx (High)

### Evidence

Three settings compose into the problem.

`docker-compose.yml:88-92`:

```yaml
# uvicorn --proxy-headers only trusts X-Forwarded-For from
# forwarded-allow-ips (default 127.0.0.1) — without this, every public
# visitor behind nginx shares ONE rate-limit bucket. "*" is safe here:
# api:8000 is reachable from compose-internal peers only.
FORWARDED_ALLOW_IPS: "*"
```

`infrastructure/nginx/nginx.conf:78` (and the `/ws/` block at `:90`):

```
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

`$proxy_add_x_forwarded_for` expands to `"$http_x_forwarded_for, $remote_addr"` — it **appends**
the real peer to whatever the client already sent, it does not replace it.

`src/api/middleware/rate_limit.py:13-19` keys the limiter on `get_remote_address`, i.e.
`request.client.host`, which uvicorn's `ProxyHeadersMiddleware` rewrites. The installed uvicorn is
0.28.1 (pinned `>=0.27,<0.29` in `pyproject.toml:45`); its source reads:

```python
def get_trusted_client_host(self, x_forwarded_for_hosts):
    if self.always_trust:
        return x_forwarded_for_hosts[0]
    for host in reversed(x_forwarded_for_hosts):
        if host not in self.trusted_hosts:
            return host
```

`always_trust` is `True` because `trusted_hosts == "*"`, so uvicorn returns
`x_forwarded_for_hosts[0]` — the **leftmost** entry, which after nginx's append is exactly the
string the client chose. A request carrying `X-Forwarded-For: <random>` therefore lands in a fresh
rate-limit bucket every time.

### Impact

`src/api/routers/auth.py:24` applies `@limiter.limit("5/minute")` as the brute-force throttle on
the credential check. That control is void: an attacker rotates the header and gets unlimited
attempts. The global `600/minute` default (`src/api/config.py:45`) is bypassed the same way. The
only surviving control is nginx's `limit_req zone=api_rl rate=30r/s burst=60`, keyed on
`$binary_remote_addr` (not spoofable) — i.e. ~1800 password guesses per minute from a single
source IP against the single credential that authorises trade submission, position closing and
config changes. The comment in the compose file asserting `"*"` "is safe here" reasons only about
*who can connect* to `api:8000`; the risk is the *content of the header*, which is end-user data
that nginx forwards verbatim.

Secondary effects: access-log `path`/client attribution and any future IP-based logic are equally
poisonable.

### Recommended fix (not applied)

1. In `infrastructure/nginx/nginx.conf`, replace the append with an overwrite in both proxy blocks:
   `proxy_set_header X-Forwarded-For $remote_addr;` (the stack has exactly one proxy hop, so no
   upstream chain is lost). The same change is needed in `nginx-dev.conf` for parity.
2. Change `FORWARDED_ALLOW_IPS` from `"*"` to the nginx container's address/subnet
   (`172.20.0.0/24`), so uvicorn walks the header from the right and cannot be told to trust the
   leftmost value.
3. Add a unit/integration test asserting that two requests differing only by `X-Forwarded-For`
   share one limiter bucket.
4. Independently of the header fix, consider keying the login limiter on the *username* as well and
   adding a short lockout after N failures — the pbkdf2 cost (200k rounds) also makes the login
   endpoint an amplification target.

---

## F-02 — Redis password written to the api's startup log (High)

### Evidence

`src/api/main.py:107`:

```python
log.info("api_startup", redis_url=settings.redis_url)
```

`settings.redis_url` is `REDIS_URL` verbatim (`src/api/config.py:97-99`). Its value in every
deployed environment embeds the credential:

- `docker-compose.yml:70` — `REDIS_URL: redis://:${REDIS_PASSWORD:-fxvol-dev}@redis:6379/0`
- `scripts/aws/load_secrets.sh` — `echo "REDIS_URL=redis://:${redis_password}@redis:6379/0"`

The structlog chain (`src/api/middleware/logging.py:24-35`) has no redaction processor, so the
value is rendered as-is into the JSON log line on stdout.

### Impact

The Redis credential is emitted in cleartext on every container start, into: `docker logs
fxvol-api`, the promtail → Loki pipeline when the `obs` profile is armed, and any downstream log
export. It is then retrievable through `/api/v1/dev/logs/query` (auth-gated, but a
lower-privilege surface than the secret store) and by anyone with host or docker access. This
directly violates the audit's "no secret ever reaches logs" criterion. Redis holds the live vol
surface, greeks, heartbeats and the trade-preview locks; with `CONFIG`/`FLUSHALL` renamed away
(`infrastructure/redis/redis.conf`) the blast radius is data tampering rather than full takeover,
but tampering with the preview lock or the greeks cache has a direct path to bad trades.

### Recommended fix (not applied)

- Log a redacted form only — e.g. host + db index, or `settings.redis_url.split("@")[-1]` — or drop
  the field entirely (the value is static config, not runtime state).
- Add a structlog processor that scrubs `scheme://user:pass@` patterns from every event value, so
  the next `DATABASE_URL`/`REDIS_URL` that reaches a log line is neutralised by default.
- Because the value has already been written to logs, rotate `/fxvol/prod/REDIS_PASSWORD` (and
  refresh the Loki retention window) once the fix lands.

---

## F-03 — Unbounded Prometheus label cardinality → remote memory exhaustion (High)

### Evidence

`src/api/middleware/timing.py:20-29`:

```python
REQUEST_DURATION.labels(
    method=request.method,
    path=request.url.path,      # concrete URL, not the route template
    status=response.status_code,
).observe(...)
```

The comment acknowledges it: *"`request.url.path` is the concrete URL, not the template — good
enough for low-cardinality ops"*. It is a `BaseHTTPMiddleware`, so it runs for **every** request
including ones that match no route (404s). Each distinct `(method, path, status)` triple creates a
permanent `Histogram` child (≈14 buckets + sum + count) held for the process lifetime; there is no
eviction.

The path is reachable unauthenticated from the internet: `nginx.conf:73-80` proxies everything
under `/fx-volatility-trading-system/api/` to the api, and the per-IP cap is `30r/s burst=60`.

### Impact

A single client can mint ~100k unique label sets per hour from one IP (more from several), each
retained forever. The api container is capped at `mem_limit: 512m` (`docker-compose.yml:66`), so the
process is OOM-killed and — with `restart: unless-stopped` — enters a restart loop for as long as
the traffic continues. That removes the operator's ability to view the book, close positions or
submit trades through the UI while positions are open, which is why this is rated High rather than
Medium despite being an availability issue. The path parameters of legitimate routes
(`/positions/{position_id}`, `/vol/smile/{tenor}`, `/vol/surface/at/{ts}`,
`/admin/config/revert/{version}`) also inflate cardinality in normal operation.

### Recommended fix (not applied)

Use the matched route template instead of the concrete path:

```python
route = request.scope.get("route")
path = getattr(route, "path", "__unmatched__")
```

That collapses `/positions/1`, `/positions/2`, … into `/api/v1/positions/{position_id}` and folds
every 404 into one series, making the label set bounded by the route table. Optionally also
`observe()` only for `path != "__unmatched__"`.

---

## F-04 — `api` and `execution-engine` containers run as root (Medium)

### Evidence

`infrastructure/docker/api.Dockerfile` and `infrastructure/docker/execution.Dockerfile` contain no
`USER` directive; both end on a `CMD ["uvicorn", …]` executed as uid 0. Every engine image does the
opposite — `src/engines/{analytics,db_writer,market_data,risk,vol}/Dockerfile` all declare
`USER fxvol` before their `CMD`.

So the two containers that terminate HTTP — including the only one reachable from the internet —
are the only ones running privileged inside their namespace.

In local dev this compounds: `docker-compose.override.yml:40` bind-mounts
`/var/run/docker.sock:ro` into the api container, and the unauthenticated
`/api/v1/dev/containers/metrics` endpoint reads it (`src/api/routers/dev.py:1536-1569`). Root plus a
Docker socket, even read-only, is a large local-dev surface.

### Recommended fix (not applied)

Add a non-root user to both Dockerfiles, mirroring the engine images:

```dockerfile
RUN useradd --create-home --uid 10001 fxvol && chown -R fxvol:fxvol /app
USER fxvol
```

Ports 8000/8001 are above 1024, so no capability is needed. Verify the healthcheck's `curl` and the
alembic invocation in `remote-deploy.sh` (`docker compose exec -T api python -m alembic …`) still
work under the new uid.

---

## F-05 — Traceback returned to the client on `/trade/submit` (Medium)

### Evidence

`src/api/routers/trade.py:745-762`:

```python
except Exception as exc:
    tb = traceback.format_exc()
    ...
    raise HTTPException(
        500,
        detail={
            "error": "trade_submit_unhandled_exception",
            "exception_type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback_tail": tb.strip().splitlines()[-5:],
        },
    ) from exc
```

### Impact

The route is behind `require_write`, so exposure is limited to the authenticated operator — that is
why this is Medium and not High. But the payload discloses absolute file paths, module layout and
the raw exception message, and the class of exceptions most likely to surface on this path
(SQLAlchemy / asyncpg / redis connection errors) routinely embeds the connection string. A
`sqlalchemy.exc.ArgumentError` on a malformed `DATABASE_URL`, for example, quotes the URL including
its password. That makes this a latent secret-leak channel into an HTTP response body, and the same
tail is written to the log (`:749-752`).

### Recommended fix (not applied)

Return an opaque error identifier (the existing `trace_id` from `AccessLogMiddleware` is ideal) and
keep type/message/traceback server-side only:

```python
raise HTTPException(500, detail={"error": "trade_submit_unhandled_exception",
                                 "trace_id": get_trace_id()})
```

The operator then correlates in Loki with `{container="fxvol-api"} |= "<trace_id>"`, which is the
workflow the docstring already describes. Apply the same treatment to the log line: log
`exc_info=True` through structlog rather than interpolating `str(exc)`.

---

## F-06 — Fail-open weak credential defaults in prod (Medium)

### Evidence

`docker-compose.yml`:

```yaml
command: ["redis-server", …, "--requirepass", "${REDIS_PASSWORD:-fxvol-dev}"]   # :47-48
REDISCLI_AUTH: ${REDIS_PASSWORD:-fxvol-dev}                                      # :51
REDIS_URL: redis://:${REDIS_PASSWORD:-fxvol-dev}@redis:6379/0                    # :70 (and every engine)
GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}                     # :756
```

`infrastructure/ec2/remote-deploy.sh:41-43` fetches the parameter *tolerantly* and then only writes
it when non-empty (`:102`):

```bash
# Absent from SSM -> compose falls back to the weak dev default; provision it before
# go-live (go/no-go checklist).
REDIS_PASSWORD="$(ssm /fxvol/prod/REDIS_PASSWORD 2>/dev/null || echo "")"
```

Contrast with `AUTH_SECRET`, which **is** guarded: `src/api/config.py:58-70` raises at boot when
`ENV=prod` and the secret is the repo default or shorter than 32 chars, so an unprovisioned host
crash-loops instead of serving with a forgeable key.

### Impact

The Redis credential has no equivalent guard. If `/fxvol/prod/REDIS_PASSWORD` is ever absent,
mis-typed, or removed, the prod stack comes up cleanly with the publicly documented password
`fxvol-dev` and nothing signals it. Redis is not published to the host in the prod compose, so
exploitation needs a foothold on `fxvol-internal` — but the whole point of the credential is
defence in depth after such a foothold. The same pattern applies to Grafana's `admin` default
whenever the `obs` profile runs.

### Recommended fix (not applied)

Make the failure mode match `AUTH_SECRET`'s:

- In `remote-deploy.sh`, fail the deploy when `ENV=prod` and `REDIS_PASSWORD` is empty (drop the
  `|| echo ""` for this parameter, as is already done for `DB_PASSWORD`).
- Or extend the `_prod_boot_guard` in `src/api/config.py` to reject a `REDIS_URL` whose password
  component is empty or equals `fxvol-dev` when `ENV=prod`.
- Remove the `:-admin` Grafana default, or bind Grafana's provisioning to a required variable.

---

## F-07 — Order-placement API unauthenticated; api forwards untyped bodies (Medium)

### Evidence

`src/engines/execution/main.py:268` mounts the mutation router with no auth dependency:

```python
router = APIRouter(prefix="/internal", tags=["execution"])
```

Its routes (`POST /internal/orders`, `DELETE /internal/orders/{id}`,
`POST /internal/positions/{id}/close`, `/internal/structure/submit`, `/internal/hedge`) place and
cancel real IB orders. The only protection is topology: port 8001 is not published
(`docker-compose.yml:376-439` declares only `expose: 9104`), and the service sits on
`fxvol-internal`.

On the api side the bodies are untyped pass-throughs:

- `src/api/routers/orders.py:52` — `async def place_order(body: dict, _: Request)`
- `src/api/routers/trade.py:716` — `async def submit_preview(body: dict[str, Any], db: DbDep)`

Validation exists only at the execution tier (`PlaceOrderBody`, `main.py:282-310`, with
`qty ≤ 1000` contracts / `≤ 5,000,000` notional and a mandatory limit price for FUT/FOP) — that
part is well done.

### Impact

Anything that can reach `fxvol-internal` can submit orders with no credential: a compromised
sidecar, a future service added to the network, or a request-forgery bug in the api (which already
holds an httpx client pointed at that host). The api's `require_write` gate is the *only* auth in
front of live order flow, and it is enforced one hop away from the component that executes.
Untyped bodies also mean malformed payloads are only rejected after crossing a service boundary,
and the api cannot enforce its own limits.

### Recommended fix (not applied)

- Add a shared-secret header (e.g. `X-Internal-Token`, sourced from SSM, compared with
  `hmac.compare_digest`) as a router-level dependency on `/internal/*`, and set it on the api's
  httpx calls. mTLS is the stronger option if the operational cost is acceptable.
- Mirror `PlaceOrderBody` (and a submit-body model) in `src/api/schemas/` so the edge rejects bad
  payloads before they reach the execution tier, and so the OpenAPI schema documents the real
  contract instead of `{}`.

---

## F-08 — Privileged cAdvisor in a profile intended for the public host (Medium)

### Evidence

`docker-compose.yml:626-654`:

```yaml
cadvisor:
  profiles: ["obs", "metrics"]
  privileged: true
  volumes:
    - /:/rootfs:ro
    - /var/run:/var/run:ro
    - /sys:/sys:ro
    - /var/lib/docker/:/var/lib/docker:ro
    - /dev/disk/:/dev/disk:ro
```

And the design note at `:585-591` states the `metrics` profile *"exists so prod can have those
graphs WITHOUT the parts that must not run on a public host — Grafana … and promtail (read-write
docker.sock)"*. So `metrics` — hence a privileged container with the host root filesystem and
`/var/run` mounted — is explicitly earmarked for prod.

promtail (`obs` only) mounts `/var/run/docker.sock` **read-write** (`:695`).

Which profiles prod actually runs is a GitHub repo variable (`COMPOSE_PROFILES`, passed through
`deploy.yml:105` into `remote-deploy.sh`), so the guard against `obs` reaching prod is a variable
value, not a mechanism.

### Impact

`privileged: true` disables namespace/capability confinement; combined with `/:/rootfs` and
`/var/run` it is a container-to-host escalation path on the internet-facing box. cAdvisor is
reputable and the mounts are read-only, so this is exposure rather than an active vulnerability —
but it removes the isolation that everything else in the compose file is carefully preserving.

### Recommended fix (not applied)

- Drop `privileged: true` and grant only what cAdvisor needs on this kernel (typically
  `devices: /dev/kmsg` plus `cap_add: [SYS_PTRACE]`; test on the EC2 kernel — the compose comments
  already document the overlay2/containerd constraint).
- Pin `COMPOSE_PROFILES` for prod in `remote-deploy.sh` to an allowlist that rejects `obs`, instead
  of trusting the repo variable, so promtail's read-write docker socket can never land on the box.

---

## F-09 — CSRF protection rests solely on `SameSite=Lax` (Medium)

### Evidence

`src/api/routers/auth.py:38-46` sets the session cookie:

```python
response.set_cookie(COOKIE_NAME, token, max_age=…, httponly=True,
                    secure=settings.auth_cookie_secure, samesite="lax", path="/")
```

`src/api/auth.py:72-87` (`require_write`) validates the cookie and nothing else — no CSRF token, no
`Origin`/`Referer` check. The frontend sends `credentials: "include"` on every call
(`frontend/src/api/client.ts:48,60`). CORS is configured without `allow_credentials`
(`src/api/main.py:182-187`), which correctly blocks cross-origin *reads*, but CORS never blocked
cross-site *writes* — only `SameSite` does.

### Impact

`SameSite=Lax` suppresses the cookie on cross-**site** POSTs, which covers the classic attacker
domain. It does **not** scope to origin: the registrable domain `valeriandarmente.dev` is the
site, and its apex serves separately-managed content (the CV, via S3/CloudFront per
`nginx.conf:105-108`). Any content injection there, on any future subdomain, or on any other path
of the same site can issue an authenticated `POST /trade/submit` or `POST /positions/{id}/close`
with the cookie attached. A single-layer CSRF defence on a trade-submission boundary is thin.

### Recommended fix (not applied)

- Add an `Origin` allowlist check inside `require_write` (reject when `Origin` is present and not
  `https://valeriandarmente.dev`; the frontend always sends it on same-origin XHR/fetch).
- Consider `samesite="strict"` — the app is a single-page dashboard with no inbound deep links that
  need the cookie on first navigation.
- A double-submit CSRF token is the belt-and-braces option if the Origin check proves brittle.

---

## F-10 — Public Swagger UI and OpenAPI schema on the prod host (Medium)

### Evidence

`src/api/main.py:166-173`:

```python
app = FastAPI(title="FXVol API", version="0.4.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url="/api/redoc",
              openapi_url="/api/openapi.json")
```

nginx proxies the whole `/api/` prefix (`nginx.conf:73-80`), so all three are reachable
unauthenticated at `https://valeriandarmente.dev/fx-volatility-trading-system/api/docs`.

### Impact

No secret is exposed — the schema carries no credential and no example values were found. But it
publishes the complete write surface (every `require_write` route, path and body shape) and hands an
attacker a ready-made interactive console pointed at the production API, removing all reconnaissance
cost from probing the auth boundary. This may be a deliberate portfolio choice; if so it should be
an explicit, recorded waiver rather than a default.

### Recommended fix (not applied)

Gate on the existing `ENV` field:

```python
is_prod = settings.ENV == "prod"
app = FastAPI(..., docs_url=None if is_prod else "/api/docs",
                   redoc_url=None if is_prod else "/api/redoc",
                   openapi_url=None if is_prod else "/api/openapi.json")
```

Note that `frontend/npm run gen:api:check` (OpenAPI drift) consumes a dumped schema at build time,
not the live endpoint, so this does not break CI. If the docs are wanted publicly as a showcase,
waive explicitly and consider serving a read-only-filtered schema.

---

## F-11 — WebSocket exposure and unbounded channel registry (Medium)

### Evidence

`src/api/routers/ws.py` declares six sockets, none with any dependency (confirmed by the route dump:
all six show `deps=[-]`): `/ws/ticks`, `/ws/vol`, `/ws/risk`, `/ws/positions`, `/ws/exit_alerts`,
`/ws/orders/{structure_id}`.

An unauthenticated client can therefore subscribe to portfolio greeks, per-position MTM snapshots,
exit alerts, and — by iterating `structure_id` — the order-status / fill / rejection / unwind stream
of any structure. Clients may send frames; they are read and discarded (`ws.py:25-26`).

`src/api/ws/connection_manager.py:24,31`:

```python
self._conns: dict[str, set[WebSocket]] = defaultdict(set)
...
self._conns[channel].add(ws)          # creates a key for every distinct channel
```

`disconnect()` calls `discard()` but never removes the now-empty key, so each probed
`structure_id` leaves a permanent dict entry.

`SlowAPIMiddleware` is HTTP-only, so the app-level rate limits do not apply to WebSockets at all;
only nginx's `limit_req zone=ws_rl rate=5r/s` + `limit_conn ws_conn 10` do (`nginx.conf:82-84`) —
and those are keyed on the real IP, so they hold. WebSockets are not subject to CORS, so any site
can open one.

### Impact

The book data itself is already public over REST (`/api/v1/positions/open`, `/portfolio/*` are all
unauthenticated by design — see the appendix), so the confidentiality delta is small **if** that is
the intended posture. The order/fill stream is the one surface that feels different in kind from
"public read-only showcase" and deserves an explicit decision. The registry growth is a slow,
low-cost memory leak driven by an unauthenticated client.

### Recommended fix (not applied)

- Prune empty sets in `ConnectionManager.disconnect()` (`if not self._conns[channel]: del …`).
- Add an `Origin` check in `_serve()` before `manager.connect()`.
- Decide explicitly whether `/ws/orders/{structure_id}` belongs to the public read surface; if not,
  gate it with the cookie (WebSocket handshakes do carry cookies, so `require_write` can be adapted).

---

## F-12 — Username validity leaks through login timing (Low)

`src/api/routers/auth.py:29-31`:

```python
ok = body.username == settings.auth_username and verify_password(
    body.password, settings.auth_salt, settings.auth_password_hash
)
```

Python's `and` short-circuits, so a wrong username skips the 200k-round pbkdf2 entirely and the
response returns in ~1 ms instead of ~50-100 ms. That is a trivially measurable oracle for the
account name. With one account the practical value is low, and the username is also guessable from
the compose default (`trader`) — hence Low.

**Fix:** always compute the KDF, and combine both comparisons with `hmac.compare_digest` on the
username too, e.g. compute `pw_ok = verify_password(...)` first, then
`ok = hmac.compare_digest(body.username, settings.auth_username) and pw_ok`.

---

## F-13 — `LoginBody` has no length bounds (Low)

`src/api/schemas/auth.py:7-9` declares bare `str` fields. nginx's default `client_max_body_size 1m`
caps the request, and pbkdf2's cost is dominated by iteration count rather than input length, so
impact is minimal — but there is no reason for an unbounded credential field on an unauthenticated
endpoint.

**Fix:** `username: str = Field(max_length=64)`, `password: str = Field(max_length=256)`, and set
`client_max_body_size` explicitly in `nginx.conf` rather than relying on the default.

---

## F-14 — Host telemetry exposed unauthenticated on `/dev` (Low)

`src/api/routers/dev.py:1468-1482` (`/dev/hardware`) returns host CPU percentage per core, load
average, total/used RAM, total/used disk and GPU model/utilisation/temperature — read from the
container's `/proc`, which Docker leaves host-wide, so these are the EC2 box's figures.
`/dev/containers/metrics` (`:1572`) is likewise unauthenticated and in local dev reaches the mounted
Docker socket (`_sample_docker`, `:1536`).

The split is deliberate and documented (`dev.py:33-41`): "PUBLIC … safe read-only showcase". This is
recorded so the decision is explicit rather than incidental — host capacity and utilisation is
infrastructure intelligence (it tells an attacker exactly how much headroom exists before an F-03
style exhaustion succeeds), not application data.

**Fix:** if the showcase value outweighs it, waive explicitly. Otherwise move `/dev/hardware` and
`/dev/containers/metrics` into the `_TRADER` list alongside `/dev/tables`, `/dev/logs/*` and
`/dev/redis/*`.

---

## F-15 — Internal hostnames leaked in error details (Low)

`src/api/routers/orders.py:35-36`:

```python
except httpx.HTTPError as e:
    raise HTTPException(status_code=503, detail=f"execution-engine unreachable: {e}") from e
```

httpx error strings embed the full target URL (`http://execution-engine:8001/internal/orders`). The
same pattern is at `src/api/routers/positions.py:1247-1249`. Both are reachable through the
**unauthenticated** `GET /api/v1/orders` and `GET /api/v1/exec/positions`, so an anonymous visitor
learns the internal service name, port and route prefix — useful reconnaissance for F-07.

**Fix:** return a static detail (`"execution service unavailable"`) and log the exception
server-side with the trace id.

---

## F-16 — Deploy role can read any SSM command output in the account (Low)

`infrastructure/aws/deploy-perms.json:8-10`:

```json
{"Sid":"ReadCommandResult","Effect":"Allow",
 "Action":["ssm:GetCommandInvocation","ssm:ListCommandInvocations","ssm:ListCommands"],
 "Resource":"*"}
```

The GitHub-federated deploy role can therefore read the stdout/stderr of **any** SSM command run in
account 552269855056, not only the invocations it created — including any operator-run command whose
output contained a secret.

The rest of the IAM set is tight and worth recording as verified-good: the trust policy pins the
OIDC subject to `repo:valerian-drmt/fx-volatility-trading-system:environment:production`
(`trust.json:7`); `SendCommand` is scoped to one instance and one document; the instance role reads
only `parameter/fxvol/prod/*` and decrypts with one KMS key (`host-additions.json`). No static AWS
keys exist anywhere in the pipeline.

**Fix:** AWS does not support resource-level scoping on `GetCommandInvocation` by command id, so the
practical options are to accept and document the residual, or to move result retrieval to a Lambda
with a narrower role. Also ensure operators never run secret-printing commands through SSM
RunShellScript (already the standing rule in `CLAUDE.md`).

---

## F-17 — `ci.yml` has no top-level `permissions:` block (Low)

`.github/workflows/ci.yml:1-18` defines `on:` and `concurrency:` but no `permissions:`. It is the
only workflow without one — `build.yml:31`, `deploy.yml:39`, `codeql.yml:21` and
`security-scan.yml:15` all declare least-privilege scopes.

Without an explicit block the job inherits the repository's default workflow-token permission. If
that default is "read and write", every CI run on `push: main` carries a token that can push
commits, create releases and modify issues — a large blast radius for a linting job. (Fork PRs get
a read-only token regardless, so the `pull_request` path is not the concern.)

**Verified good in the same review:** no workflow anywhere in the repo uses `pull_request_target`,
so there is no fork-PR-with-secrets path. `deploy.yml` gates on
`vars.DEPLOY_ENABLED == 'true'` plus `workflow_run.conclusion == 'success'`, uses `environment:
production` for OIDC pinning, and passes only non-secret config into `SendCommand` — secrets are
read on-host from SSM by `remote-deploy.sh` and never appear in workflow logs. The workflow echoes
the SSM `StandardOutputContent`, and `remote-deploy.sh` was reviewed line by line: it prints no
secret value and does not enable `set -x`.

**Fix:** add `permissions: {contents: read}` at the top of `ci.yml`, and set the repository default
workflow permission to read-only.

---

## F-18 — Trade-preview inputs unbounded at the api tier (Low)

`src/api/routers/trade.py:99-116`:

```python
class PreviewRequest(BaseModel):
    qty: int | None = None
    override_qty: int | None = None
    strike_override: float | None = None
```

No `gt=0`, no upper bound. Negative and absurd values are accepted by the schema; the real limits
live downstream in the risk checks and in the execution tier's `PlaceOrderBody`
(`qty ≤ 1000` contracts / `≤ 5,000,000` notional, `strike > 0`). Defence in depth is intact, so this
is Low — but the audit's checklist asks that write-endpoint schemas validate ranges, and here they
do not.

**Fix:** add `Field(gt=0, le=1000)` (and the matching bound for `future_contract_size` paths) to
`PreviewRequest`, mirroring the execution-tier constraints. Correctness of the numeric bounds
themselves is Audit 4's scope.

---

## F-19 — Stale and contradictory security comments (Low)

Two places describe the auth boundary incorrectly, which matters because a reader auditing by eye
would draw the wrong conclusion (the programmatic dump is what caught it):

- `src/api/routers/admin.py:10-12`: *"No auth for now (solo-trader cockpit …). When the cockpit
  opens to multiple users, wrap the PUT / POST routes in a `Depends(require_admin)`."* — In fact
  `PUT /config`, `POST /config/revert/{version}` and `PUT /settings/{domain}` all already carry
  `dependencies=[Depends(require_write)]` (`:59, :90, :147`).
- `src/api/routers/dev.py:5-7` (module docstring): *"the whole router requires a valid write-auth
  cookie (`Depends(require_write)` at router level)"* — contradicted by the accurate comment 30
  lines below at `:40`: *"the router is NOT gated at the router level; write-gating is per
  endpoint."* The second is correct; six `/dev` routes are public.

The nginx comment at `nginx.conf:66-71` correctly describes the current per-endpoint split.

**Fix:** delete the `admin.py` paragraph, and rewrite the `dev.py` docstring to state the two-audience
split that `:33-41` already documents correctly.

---

## F-20 — nginx hardening gaps (Low)

`infrastructure/nginx/nginx.conf`:

- `:53` — `connect-src 'self' wss:` permits a WebSocket connection to **any** host, weakening the
  CSP's exfiltration containment. Should be `connect-src 'self' wss://valeriandarmente.dev`.
- No `Permissions-Policy` header (worth `geolocation=(), microphone=(), camera=()` at minimum).
- `:44` — `server_tokens off` is set only inside the 443 server block; the port-80 block
  (`:20-27`) still advertises the nginx version on its redirects and errors. Move it to `http`
  level (or repeat it).
- No explicit `client_max_body_size`; the 1 MB default applies, which is fine but implicit.
- TLS is reasonable (`TLSv1.2 TLSv1.3`, `ssl_prefer_server_ciphers on`, HSTS with
  `includeSubDomains`); consider `ssl_session_tickets off` and OCSP stapling.

**Verified good:** the `add_header` inheritance trap is called out in a comment at `:46-49` and no
location currently declares its own `add_header`, so the security headers do apply everywhere.
`location / { return 404; }` correctly closes off everything outside the app prefix, and `/metrics`
on the api is **not** proxied (only `/api/` and `/ws/` are), so the Prometheus endpoint is not
publicly reachable.

---

## Checklist items verified clean (no finding)

Recorded so the exit criteria can be evidenced.

**Auth boundary.** All 115 routes enumerated programmatically (appendix). Every POST/PUT/PATCH/DELETE
carries `require_write` except: `POST /auth/login` (the auth endpoint itself, rate-limited),
`POST /auth/logout` (clears a cookie, no state), and `POST /price` `/iv` `/greeks` (stateless
Black-Scholes calculators, no persistence, no IB). All `/admin/*` config mutators are gated. All
`/dev/*` data-access routes (`tables`, `tables/{name}`, `logs/*`, `redis/*`) are gated per-endpoint.

**Auth implementation.** HMAC-SHA256 over a JSON payload with an `exp` claim; signature compared
with `hmac.compare_digest` (`src/api/auth.py:51`); expiry enforced (`:54`); the token is minted from
the *server-side* username, never from client bytes (`routers/auth.py:34-37`); pbkdf2-hmac-sha256 at
200k rounds with `compare_digest` (`auth.py:61-69`); an empty `AUTH_PASSWORD_HASH` is fail-closed
(`auth.py:67-68`), so an unprovisioned host cannot be logged into; the cookie is `httponly` and
`secure` by default; `ENV=prod` refuses a default/short `AUTH_SECRET` and an insecure cookie
(`config.py:58-70`).

**SQL injection.** Zero f-string or `%`-formatted SQL in `src/` — `grep` for `text(f`, `execute(f`,
`sql = f"`, `sql +=` returns nothing. The `/dev/tables/{name}` explorer, the one endpoint that takes
identifiers from the request, validates table name against `Base.metadata.tables`, `order_by` and
filter columns against the table's own column set, and binds every filter *value* through
SQLAlchemy Core expressions (`dev.py:488-580`). The raw `text()` blocks in `portfolio_panel.py` are
static strings with named bind parameters.

**SSRF.** All outbound HTTP is to constant bases with ids in `params=`: FRED
(`sources/fred.py:51,71` — `BASE_URL` constant, api key and dates via `params`), Loki
(`dev.py:632,641,721` — fixed base, LogQL passed as a parameter with quotes/backslashes escaped),
Prometheus (`dev.py:1489-1501` — fixed queries, explicitly noted "no user-controlled URL/expr"),
execution-engine (`orders.py:26,31` — base from env, path from a typed int path parameter). ECB /
BoE / FOMC / Eurostat / ONS sources compute schedules locally and make no network call at all. No
user-controlled URL reaches any client.

**Secrets.** No credential material in the tracked tree (`AKIA…`, PEM headers, `ghp_`, `xox…` all
return nothing); no `.env` file has ever been committed (`git log --all -- '.env*'` is empty; `.env`
and `.env.local` are gitignored); `scripts/local/load_secrets.ps1` and `scripts/aws/load_secrets.sh`
were read and print only parameter *names*, counts and placeholder warnings — never a value;
`/api/v1/health/extended` returns component statuses only, no connection strings; the frontend bundle
reads only `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` / `VITE_WRITE_ENABLED` (no secret), and stores no
token in `localStorage`/`sessionStorage` — auth is the httpOnly cookie only. The one violation is
F-02.

**Network & containers.** The prod compose publishes host ports for **nginx only** (`80`, `443`);
postgres, redis, api, frontend and execution-engine publish nothing; IB Gateway (`4002`, `5900`) and
Grafana (`3000`) are bound to `127.0.0.1`. Postgres/Redis host exposure lives only in
`docker-compose.override.yml`, which the deploy payload deliberately excludes (`deploy.yml:94`
tars `docker-compose.yml infrastructure obs` — the comment states "The dev override stays local").
Redis is hardened: `requirepass`, `protected-mode yes`, and `FLUSHDB`/`FLUSHALL`/`CONFIG` renamed
away (`infrastructure/redis/redis.conf`). No `docker.sock` mount in the prod compose (only in the
dev override and in promtail, `obs` profile).

**CORS.** `allow_origins` is an explicit two-entry localhost allowlist with **no**
`allow_credentials` (`main.py:182-187`), so the wildcard-with-credentials anti-pattern is absent;
prod is same-origin and does not rely on CORS.

**CI supply chain.** No `pull_request_target` anywhere. Deploy uses OIDC federation (no stored AWS
keys), no inbound SSH/port 22, and passes only non-secret config through `SendCommand`. Weekly
Trivy scanning of all seven production images with `exit-code: 1` on HIGH/CRITICAL, plus CodeQL on
push/PR/weekly for both languages.

---

## Appendix — route-by-route auth matrix

Generated by importing `api.main:app` with `PYTHONPATH=src` and walking `app.routes`, resolving the
full (recursive) dependency tree of each route. `require_write` in the deps column is the auth gate;
`get_db_session` / `get_redis` / `get_settings` are infrastructure dependencies, not auth.

Legend: **AUTH** = requires a valid write cookie · **public** = no authentication.

### Mutating routes (POST / PUT / PATCH / DELETE) — 21 routes

| Method | Path | Auth | Router |
|---|---|---|---|
| PUT | `/api/v1/admin/config` | **AUTH** | admin |
| POST | `/api/v1/admin/config/revert/{version}` | **AUTH** | admin |
| PUT | `/api/v1/admin/settings/{domain}` | **AUTH** | admin |
| POST | `/api/v1/admin/pca/refit` | **AUTH** | signals |
| POST | `/api/v1/exec/positions/{con_id}/close` | **AUTH** | orders |
| POST | `/api/v1/orders` | **AUTH** | orders |
| DELETE | `/api/v1/orders/{order_id}` | **AUTH** | orders |
| POST | `/api/v1/positions/monitor/run-once` | **AUTH** | positions |
| POST | `/api/v1/positions/{position_id}/close` | **AUTH** | positions |
| POST | `/api/v1/positions/{position_id}/close-manual` | **AUTH** | positions |
| POST | `/api/v1/regime/events` | **AUTH** | regime |
| POST | `/api/v1/regime/events/sync` | **AUTH** | regime |
| POST | `/api/v1/trade/preview` | **AUTH** | trade |
| POST | `/api/v1/trade/preview/{preview_id}/cancel` | **AUTH** | trade |
| POST | `/api/v1/trade/submit` | **AUTH** | trade |
| POST | `/api/v1/trades/{trade_id}/cancel` | **AUTH** | trades |
| POST | `/api/v1/trades/{trade_id}/close` | **AUTH** | trades |
| POST | `/api/v1/vol/trade-preview` | **AUTH** | cockpit |
| POST | `/api/v1/auth/login` | public — *by design*, `@limiter.limit("5/minute")`, see F-01 | auth |
| POST | `/api/v1/auth/logout` | public — *by design*, only clears the cookie | auth |
| POST | `/api/v1/price`, `/api/v1/iv`, `/api/v1/greeks` | public — *by design*, stateless BS calculators (3 routes) | pricing |

**Result: every state-changing route is gated.** The three pricing POSTs are non-mutating
(pure computation, no DB/Redis/IB write) — verified by reading `src/api/routers/pricing.py`.

### `/dev/*` routes — 12 routes (two-audience split, `dev.py:33-41`)

| Method | Path | Auth |
|---|---|---|
| GET | `/api/v1/dev/logs/containers` | **AUTH** |
| GET | `/api/v1/dev/logs/query` | **AUTH** |
| GET | `/api/v1/dev/redis/keys` | **AUTH** |
| GET | `/api/v1/dev/redis/value` | **AUTH** |
| GET | `/api/v1/dev/tables` | **AUTH** |
| GET | `/api/v1/dev/tables/{name}` | **AUTH** |
| GET | `/api/v1/dev/containers/metrics` | public (F-14) |
| GET | `/api/v1/dev/cycle-progress` | public |
| GET | `/api/v1/dev/db-schema` | public |
| GET | `/api/v1/dev/engines` | public |
| GET | `/api/v1/dev/hardware` | public (F-14) |
| GET | `/api/v1/dev/migrations`, `/api/v1/dev/migrations/{rev_id}` | public (2 routes) |

Note: `nginx.conf:66-71` no longer 404s the `/dev` prefix at the edge; gating is entirely
in-application. `/dev/migrations/{rev_id}` resolves the file by parsing every migration in a fixed
directory and matching the revision id — **no path traversal** (`dev.py:921-931`).

### `/admin/*` and config routes — 7 routes

| Method | Path | Auth |
|---|---|---|
| PUT | `/api/v1/admin/config` | **AUTH** |
| POST | `/api/v1/admin/config/revert/{version}` | **AUTH** |
| PUT | `/api/v1/admin/settings/{domain}` | **AUTH** |
| POST | `/api/v1/admin/pca/refit` | **AUTH** |
| GET | `/api/v1/admin/config` | public (read) |
| GET | `/api/v1/admin/config/history` | public (read) |
| GET | `/api/v1/admin/config/schema` | public (read) |
| GET | `/api/v1/admin/settings/{domain}` | public (read) |

### WebSocket endpoints — 6 routes

| Path | Auth | Streams |
|---|---|---|
| `/ws/ticks` | public | spot ticks (~5/s) |
| `/ws/vol` | public | vol surface updates |
| `/ws/risk` | public | portfolio greeks / risk cycle |
| `/ws/positions` | public | per-position MTM snapshots |
| `/ws/exit_alerts` | public | exit-rule alerts |
| `/ws/orders/{structure_id}` | public | order status / fills / rejections / unwinds (F-11) |

No WebSocket carries a dependency; `SlowAPIMiddleware` does not apply to them (HTTP-only). Only
nginx's `ws_rl 5r/s` + `limit_conn ws_conn 10` throttle them.

### Public read routes — 69 routes (all GET, no auth, by design)

`/api/v1/health`, `/api/v1/health/extended`, `/api/v1/auth/me`, `/api/v1/bars`,
`/api/v1/system-stats`, `/api/v1/vol-history`, `/api/v1/orders`, `/api/v1/exec/positions`,
`/api/v1/vol/{surface, surface/at/{ts}, term-structure, smile/{tenor}, model-health, regime}`,
`/api/v1/signals/pca/{state, model, history}`,
`/api/v1/regime/{state, history, transitions, features, events, gmm/shadow}`,
`/api/v1/trade/{structures, limits, book, submitted, preview/{preview_id}}`,
`/api/v1/positions/*` (14 GET routes: open, active, book, aggregate, breaks, ledger,
reconciliation, structured, delta-hedge-config, exit-rules-config, `{id}`, `{id}/alerts`,
`{id}/hedges`, `{id}/mtm-history`, `{id}/signal-tracking`),
`/api/v1/portfolio/*` (24 GET routes: header, account, cash, stats, var, marginal-var, var-factors,
aggregate-greeks, greeks-history, greeks-ladder, greek-limits, greek-pnl-history, equity-curve,
daily-pnl, pnl-attribution, pnl-attribution-pivot, valuation-history, trade-markers, hedge-summary,
pin-risk, risk-per-tenor, vega-per-tenor, vega-pca, scenarios, stress-grid).

This matches the documented posture (public read-only backend, auth-gated writes). `/metrics`
(`main.py:191`, `include_in_schema=False`) is served by the app but **not** proxied by nginx, so it
is unreachable from the internet.

---

*No source or configuration file was modified by this audit. All findings are reported for separate
remediation per the process in [`../README.md`](../README.md).*
