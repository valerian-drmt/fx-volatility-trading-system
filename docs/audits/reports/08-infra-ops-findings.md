# Audit 8 — Infrastructure & Operations — Findings (2026-08-01)

Scope per [`../08-infra-ops.md`](../08-infra-ops.md). **Findings only** — no compose file,
Dockerfile, workflow or script was modified; no git state changed. **No live host, no prod,
no stack start, no chaos drill, no secret read.** Every check that needs the running host or
stack is listed in [`08-runbook-draft.md`](08-runbook-draft.md) with exact commands.

Builds on Wave 1 (`nginx:alpine` unpinned, F-03 api OOM, root containers) and Wave 2
(EX-02 orders stuck across the ~02:00 gateway restart, DI db-writer idempotency) — those are
referenced, not re-reported.

## Totals

| Severity | Count |
|---|---|
| Blocker | 2 |
| High | 11 |
| Medium | 16 |
| Low | 6 |
| **Total** | **35** |

## The headline answer

> **Is there a tested Postgres backup? NO.**
> A backup is *specified and scripted* — a nightly `pg_dump -Fc` → `s3://fxvol-backups/postgres/`
> installed by `setup.sh`, plus a pre-migration dump on every deploy, plus a real S3 lifecycle
> (30 d → Glacier IR, 365 d expiry, versioning on). That is a genuinely good design.
> But `infrastructure/ec2/RESTORE.md` ends with a restore-test log whose only row reads
> *"none yet — run once before go-live"*, `EC2_DEPLOYMENT.md §6` still lists *"Verify the
> Postgres→S3 backup cron runs + test a restore"* as an open item, and the cron is installed
> only by a manual one-shot script that the deploy pipeline never re-runs. So the honest
> statement is: **nobody knows whether the backup is running, and nobody has ever restored one.**

---

# Blockers

## OPS-01 (Blocker) — Nothing alerts anyone, ever, and db-writer downtime is silent permanent data loss

**Where:** `obs/prometheus.yml` (no `alerting:`, no `rule_files:`), no Alertmanager service in
`docker-compose.yml`, `obs/grafana/provisioning/` (datasources + dashboards only, no
`alerting/`), `infrastructure/aws/STATE.md:205-215`, `infrastructure/aws/EC2_DEPLOYMENT.md:132`.

An SNS topic `fxvol-alarms` exists with a **confirmed** email subscription to
valeriandarmente@gmail.com. Zero alarms publish to it. `STATE.md §4.2` says verbatim:
*"To be used as the SNS target of the CloudWatch alarms still to create (EC2 CPU, disk full,
healthcheck KO)"*. A repo-wide grep for `sns`, `put-metric-alarm`, `alertmanager`,
`fxvol-alarms` outside those two doc lines returns nothing. The delivery channel is wired and
the producer side is empty.

So today, **nobody is told** when:

| Event | Detected by | Notifies |
|---|---|---|
| Any container crash-loops | nothing | nobody |
| Root disk passes 80 % | nothing (`df -h` only inside a manual `ec2.ps1 stack`) | nobody |
| IB session drops outside the nightly window | `ib_watchdog.sh` acts, silently | nobody |
| db-writer down / lagging | nothing | nobody |
| Nightly `pg_dump` fails | cron mail, no MTA installed | nobody |
| TLS renewal fails | nothing | nobody |
| Deploy fails | red GitHub Actions run | GitHub email, if noticed |

The reason this is a Blocker rather than a High is the **db-writer** row. The transport is
Redis **pub/sub**, not a stream or a list — `src/bus/publisher.py` uses `redis.publish(...)`
for ticks, vol updates, risk updates, order events and position updates, and
`src/engines/db_writer/service.py:137-167` consumes with `pubsub.subscribe()` + `listen()`.
Pub/sub is fire-and-forget with **no backlog and no replay**: everything published while the
subscriber is down is gone, irrecoverably, with no gap marker in Postgres and no error
anywhere. `restart: unless-stopped` covers an *exit*, but a container that is `unhealthy`
(the exact failure mode `ib_watchdog.sh`'s own header documents) is never restarted by Docker.

That is silent, permanent loss of the system's primary time series in normal operation, with
no detection path. It meets the Blocker definition directly.

**Minimum to close:** three CloudWatch alarms → `fxvol-alarms` (instance StatusCheckFailed,
disk-used % via the CloudWatch agent, and a custom metric or heartbeat-age check for the
engines), plus a backup-freshness alarm (RESTORE.md §5 already proposes it: newest object
age > 26 h → SNS; *"Not armed yet"*). Everything else can wait; these cannot.

## OPS-02 (Blocker) — The Postgres backup has never been restored, and its existence on the host is unverified

**Where:** `infrastructure/ec2/setup.sh:122-136`, `infrastructure/ec2/RESTORE.md:74-78`,
`infrastructure/ec2/remote-deploy.sh:117-122`, `infrastructure/aws/EC2_DEPLOYMENT.md:135-136`.

The design is sound and I want to record that: `pg_dump -Fc --if-exists --clean`, a
`> 10240` byte sanity gate before upload, SSE-S3, instance role with `s3:PutObject`/`GetObject`
and **no Delete** (`STATE.md §1.2`), bucket versioning on, lifecycle `ArchiveOldBackups`
(30 d → Glacier IR, 365 d expiry, noncurrent 90 d). Plus a pre-migration dump to
`postgres/pre-deploy/` on every schema change. Better than most solo projects.

What is missing is the only thing that makes a backup a backup:

1. **Never restored.** `RESTORE.md` §3 is a correct, runnable test-restore procedure into a
   scratch container. Its own log table says `| _none yet — run once before go-live_ | | |`.
2. **Never verified to run.** The cron is written by `setup.sh`, a manual one-shot. The
   deploy pipeline ships `infrastructure/` into `/opt/fxvol` but **never re-runs `setup.sh`**
   (see OPS-08), so a host provisioned before this cron was added simply does not have it.
   `EC2_DEPLOYMENT.md §6` lists verifying it as still-open. Nothing in the repo proves it ever
   fired.
3. **Silent on failure.** `set -eu` plus the size gate means a bad dump aborts — correctly —
   but the abort goes to cron's stdout, and no MTA is installed on the host. A backup that has
   been failing for a month looks exactly like one that has been working for a month.
4. **Restore is untimed.** No RTO is stated anywhere. §4 "Real restore" restores *over* the
   live database with `--clean`, which is the highest-risk variant (a failure mid-restore
   leaves neither the old nor the new dataset).

There is also a second, unmitigated loss class: **only Postgres is backed up.** See OPS-10.

**Minimum to close:** run RESTORE.md §3 once, record the date in its log table, and confirm
the cron exists on the host (`ls -l /etc/cron.daily/fxvol-postgres-backup` +
`aws s3 ls s3://fxvol-backups/postgres/ | tail`). Commands are in the runbook §3.

---

# High

## OPS-03 (High) — Prod deploys do not require CI to be green

**Where:** `.github/workflows/ci.yml:3-6`, `.github/workflows/build.yml:9-29`,
`.github/workflows/deploy.yml:28-31,53`.

Three independent triggers on a push to `main`:

- `ci.yml` — ruff, import-linter, mypy, unit tests, frontend, e2e, live-integration.
- `build.yml` — builds and pushes 7 images.
- `deploy.yml` — chains off `workflow_run: workflows: ["build-and-push"]` with
  `github.event.workflow_run.conclusion == 'success'`.

`deploy.yml` gates on the **build**, never on `ci.yml`. A commit whose tests are red still
produces images (a Docker build does not run pytest) and therefore still deploys to prod,
automatically, with `DEPLOY_ENABLED=true`. The `docs/ops/deployment.md:90` claim *"a half-built
image set never ships"* is true and is a different guarantee from *"a broken commit never
ships"*, which does not hold. Given Wave 2's 10 Blockers, this is the pipeline that would have
shipped them.

## OPS-04 (High) — Rollback restores the code but not the schema, and has never been exercised

**Where:** `docs/ops/deployment.md:125-130`, `infrastructure/ec2/remote-deploy.sh:157-160,189`.

Rollback is documented as `gh workflow run deploy-prod -f deploy_sha=<old-sha>`. That path
runs the *same* `remote-deploy.sh`, which unconditionally executes
`alembic ... upgrade head` — from the **old** image. Since the old image's head is an ancestor
of the current DB revision, `upgrade head` is a no-op. Result: **old code, new schema.**

There is no `alembic downgrade` anywhere in the deploy path and no rule saying which
migrations are downgrade-safe. So the actual recovery from a bad deploy that included a
destructive migration is a full restore of the pre-deploy dump — which loses every write
since the deploy and which (OPS-02) has never been performed.

Rollback has never been executed even in the benign no-migration case; nothing in the repo
records a rollback drill.

## OPS-05 (High) — Migrate-before-swap runs the new schema against the still-serving old code, with no N-1 compatibility rule

**Where:** `infrastructure/ec2/remote-deploy.sh:115-161` then `:169`.

The ordering is deliberate and documented: dump → `alembic upgrade head` (new image, one-off
`docker run`) → `docker compose up -d`. Between those two steps — tens of seconds, longer if
the `up -d` retry loop fires — the **old** api/engines are serving live traffic against the
**new** schema.

That is the correct "expand" half of expand/contract, but nothing enforces the contract half.
There are 55 revisions in `src/persistence/migrations/versions/`, and Wave 2 / prior history
record rename-and-drop migrations with cascade effects. A single `op.drop_column` or
`op.alter_column(nullable=False)` in a deploy takes the live api down for the swap window and,
worse, can fail *writes* that the old code is mid-flight on. No CI check, no review checklist,
and no `docs/` rule states that every migration must be readable by the previous release.

**Verified clean and worth recording:** the migration itself is atomic. `env.py` uses Alembic's
default single wrapping transaction (no `transaction_per_migration`), and a grep over all 55
revisions finds **zero** `CONCURRENTLY` / `AUTOCOMMIT` / `isolation_level` escapes. So a
mid-migration failure rolls the whole upgrade back on Postgres's transactional DDL and
`set -e` aborts the deploy *before* the swap, leaving the previous working stack running. That
failure mode is handled well. The gap is the *successful* migration against old code.

## OPS-06 (High) — Declared memory limits over-commit the host by ~2.8× before the engines are counted twice

**Where:** `docker-compose.yml` (`mem_limit` on 12 services), `infrastructure/aws/STATE.md:11`,
`infrastructure/ec2/setup.sh:51-55`.

| Group | Services | Sum of `mem_limit` |
|---|---|---|
| Core | postgres 512 + redis 320 + api 512 + frontend 64 + nginx 64 | **1 472 MB** |
| `engines` profile | market-data 384 + vol 512 + risk 384 + db-writer 384 + analytics 512 + execution 384 | **2 560 MB** |
| `ib` profile | ib-gateway 1 500 | **1 500 MB** |
| **Total (engines,ib armed)** | | **5 532 MB** |

`STATE.md:11` records the instance as **t3.small = 2 GiB**, and the cadvisor comment in
`docker-compose.yml:631` agrees (*"the prod box is a t3.small (2 GB) running core-only"*) while
the analytics comment at `:330-335` speaks of *"BOTH t3.medium cores"* and the tempo comment at
`:590` of *"the t3.medium has the RAM headroom"*. The compose file contradicts itself about the
size of the box it runs on. Even on t3.medium (4 GiB) + the 2 GiB swapfile, 5.5 GB of limits
does not fit; on t3.small it is 2.8× the RAM.

Consequences, in order of likelihood:

- `mem_limit` without `memswap_limit` lets each container use its limit again in **swap**, so
  the 2 GB swapfile is consumed by whichever container leaks first, and the whole box goes to
  disk-latency before any single container is capped.
- There are **no `mem_reservation`** values, so the kernel has no notion of who matters. Under
  pressure the OOM killer picks by badness score, and the fattest RSS is usually `vol-engine`
  or `ib-gateway` — killing the gateway takes every engine's IB session with it.
- **5 of the 7 `obs` services have no `mem_limit` at all**: `loki`, `promtail`, `tempo`,
  `otel-collector`, `grafana`. Only `prometheus` (256m) and `cadvisor` (128m) are capped. If
  `obs` is ever armed on prod, Loki's ingester is unbounded on a 2 GB box.
- Wave 1's **F-03** (unbounded Prometheus label cardinality OOM-ing the 512 MB api) lands on a
  host with negative headroom, so the blast radius is the box, not the container.

**No aggregate check exists** — nothing in CI or the deploy validates that the sum of limits
fits the instance.

## OPS-07 (High) — db-writer's graceful drain is cut off by the default 10 s stop grace, on every deploy

**Where:** `docker-compose.yml` (no `stop_grace_period` on any service),
`src/persistence/writer.py:159-190`, `src/engines/db_writer/main.py:4`.

`AsyncDatabaseWriter.dispose()` implements exactly the right shutdown: stop the loop, drain the
remaining queue, chunk it by `BATCH_SIZE_MAX = 100`, insert each chunk, dispose the engine. The
code comment even sizes the tail at *"say 8000 events"* — 80 round-trips to Postgres.

`stop_grace_period` is set on **no service in the file**, so Docker's default applies: SIGTERM,
**10 seconds**, then SIGKILL. An 80-chunk drain against a 512 MB Postgres on a swapping t3.small
will not reliably finish in 10 s. What is lost is silent — SIGKILL produces no log line, and
(OPS-01) the transport has no backlog to replay from.

This fires on **every deploy**, because every deploy changes the image tag and therefore
recreates db-writer. With continuous deploy armed, that is once per merge to `main`.

`execution-engine` has the same 10 s ceiling while its lifespan handler marks orders
disconnected and calls `executor.disconnect()` against IB — same class, order-state impact
(cf. Wave 2 EX-02).

## OPS-08 (High) — The entire host-side ops layer is installed by a manual one-shot the pipeline never runs

**Where:** `infrastructure/ec2/setup.sh` (whole file), `.github/workflows/deploy.yml:94`,
`infrastructure/ec2/remote-deploy.sh`.

`setup.sh` is the sole installer of:

- `/etc/docker/daemon.json` — **the only log rotation in the system** (`max-size 10m`,
  `max-file 3`) and the overlay2 pin cAdvisor needs;
- the 2 GB swapfile;
- `/etc/cron.weekly/fxvol-docker-prune`;
- `/etc/cron.daily/fxvol-certbot-renew`;
- `/etc/cron.daily/fxvol-postgres-backup` — **the backup**;
- `/etc/cron.d/fxvol-ib-watchdog` — **the auto-heal**;
- `/etc/systemd/system/fxvol-compose.service` — **boot survival**;
- the ufw rules.

The deploy payload is `tar czf payload.tar.gz docker-compose.yml infrastructure obs`, extracted
to `/opt/fxvol`. So a change to `setup.sh` **lands on the host as a file and is never applied**.
There is no drift detection, no "is the cron installed" assertion, and no idempotent re-run in
`remote-deploy.sh` (which would be safe — `setup.sh` is explicitly written to be idempotent).

This is precisely the shape of the past incident where the ec2 scripts shipped as git 0644 and
the watchdog cron never ran: a host-side ops control that the repo believes is armed and that
the host does not have. Everything in OPS-01/02 that depends on "the cron runs" inherits this
uncertainty.

Note the one place it was consciously hardened — the watchdog cron invokes
`/bin/sh $APP_DIR/.../ib_watchdog.sh` rather than the script directly, *"so a lost execute bit
… cannot silently break the auto-heal"* (`setup.sh:146-149`). That fix is correct and is the
proof that the general problem was understood but only patched at one site.

## OPS-09 (High) — The IB watchdog covers 2 of 6 engines, ignores the execution engine, and reports to nobody

**Where:** `infrastructure/ec2/ib_watchdog.sh:23,33-41,54-58`, `setup.sh:138-150`.

```sh
ENGINES="fxvol-market-data fxvol-vol-engine"
```

The trigger is `unhealthy` on market-data **or** vol-engine; the recovery is
`docker restart fxvol-ib-gateway`, sleep 75, `docker restart` those same two.

Not covered, in either the trigger or the recovery:

- **`execution-engine`** (IB clientId 5) — the order path. This is the direct operational
  counterpart of Wave 2 **EX-02**: orders working across the ~02:00 Paris gateway restart never
  reach a terminal state. The watchdog restarts the gateway *underneath* the execution engine
  and leaves the execution engine holding a dead IB socket, which is the state that strands
  those orders. Restarting it is one word in `ENGINES` and would close the operational half of
  EX-02.
- **`risk-engine`** (clientId 3) — also IB-connected, also stalls on a dead upstream.
- **`db-writer`**, **`analytics-engine`** — no IB, but equally never auto-healed from
  `unhealthy` (and OPS-01 makes db-writer's case data loss).

Its own failure visibility is nil: output goes to `/var/log/fxvol-ib-watchdog.log`, which
nothing reads, nothing rotates (OPS-27) and nothing alerts on. A watchdog that fires 40 times a
night and a watchdog that has been dead for a month are indistinguishable from outside the box.
The 15-minute cooldown is a good design; nobody learns that it was hit.

Two smaller correctness notes on the script itself: `health()` returns `missing` for an absent
container but the `2>/dev/null || echo missing` is unreachable because `docker inspect -f` on a
missing container exits non-zero *after* printing nothing, and `set -eu` at line 19 means the
`||` catches it — fine in practice, but a container that has been `docker rm`'d reads as
`missing`, i.e. **no action**. A permanently deleted engine never triggers the watchdog.

## OPS-10 (High) — Only Postgres is backed up; host loss is an untested manual rebuild

**Where:** `docker-compose.yml:813-823` (8 named volumes), `infrastructure/aws/STATE.md §2`
(S3 only, no EBS snapshot / AMI / DLM policy anywhere in the repo).

Backed up: `postgres_data`, logically, via `pg_dump`.

Not backed up, at all:

| Volume | Contents | Rebuild cost |
|---|---|---|
| `ib_gateway_jts` | The Gateway install **and** the `.ibgzenc` API settings — the Trusted-IPs list configured **by hand over VNC** (`docker-compose.yml:512-533`) | Manual GUI session over VNC, one entry at a time; the GUI rejects CIDRs |
| `/etc/letsencrypt` (host path) | TLS private key + lineage | `init-letsencrypt.sh` re-run, subject to LE rate limits |
| `redis_data` | RDB of live state (see OPS-18) | not rebuildable |
| `prom_data`, `loki_data`, `tempo_data`, `grafana_data` | all history | acceptable loss, but say so |

The `ib_gateway_jts` row is the sharp one. That volume is the reason the version-drift
crash-loop happened, and its contents include an irreproducible-by-script manual configuration
step. There is no EBS snapshot schedule, no AMI, no DLM lifecycle policy, and no documented
"rebuild the host from zero" time estimate. `setup.sh` + `init-letsencrypt.sh` + a deploy +
a VNC session + a restore is the real DR path, and it has never been walked.

## OPS-11 (High) — A missing SSM parameter silently downgrades prod to a committed default password

**Where:** `infrastructure/ec2/remote-deploy.sh:30-43,99-105`, `docker-compose.yml:47-51,70`,
`src/api/config.py:59-69`.

`remote-deploy.sh` fetches optional parameters with
`"$(ssm /fxvol/prod/X 2>/dev/null || echo "")"` and appends them to `.env` **only if non-empty**.
For `AUTH_SECRET` that is correct and deliberate: the `ENV=prod` boot guard
(`config.py:62-69`) refuses the repo default, so an unprovisioned host crash-loops the api
instead of serving with a forgeable key. Good design.

`REDIS_PASSWORD` has the same optional treatment and **no equivalent guard**. If it is absent
from SSM, `.env` omits it, and `docker-compose.yml:48` falls back to
`--requirepass ${REDIS_PASSWORD:-fxvol-dev}` — a value committed to a **public** repository. The
same default flows into every `REDIS_URL`. The comment at `:44-46` explains the dev default's
purpose (exercise the auth path) without noticing that prod inherits it on a missing parameter.
Redis is internal-network-only, so this is not directly reachable — but it is a
publicly-known credential protecting the bus that carries order events, and it fails **open and
silently**.

The blast radius is amplified by the SSM inventory being genuinely inconsistent across five
sources, so a rebuild would plausibly miss it:

| Source | Parameters listed |
|---|---|
| `STATE.md §1.4` | 5 (bootstrap) |
| `STATE.md §0` | + AUTH_SECRET, AUTH_PASSWORD_HASH, FRED_API_KEY, REDIS_PASSWORD = 9 |
| `EC2_DEPLOYMENT.md:89-90` | 7 — **omits FRED_API_KEY and REDIS_PASSWORD** |
| `scripts/aws/load_secrets.sh:28-35` | 8 — omits FRED_API_KEY |
| `scripts/local/load_secrets.ps1:49-62` | 9 |
| `remote-deploy.sh` | 9 + **GHCR_TOKEN**, and notes AUTH_SALT is deliberately absent |
| `provision-deploy-oidc.ps1:106` | instructs creating GHCR_TOKEN |

The true set is 10 and **no single document lists it**. The audit asked for a documented
inventory sufficient to rebuild an environment; today the closest thing is
`load_secrets.ps1`'s array, and it is missing GHCR_TOKEN.

## OPS-12 (High) — The post-deploy smoke check and the api healthcheck both assert a constant

**Where:** `.github/workflows/deploy.yml:158-176`, `src/api/routers/health.py:27-30`,
`docker-compose.yml:111-116`, `infrastructure/docker/api.Dockerfile:43-44`.

```python
@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — always 200 as long as the FastAPI process answers."""
    return {"status": "OK"}
```

The deploy smoke polls that endpoint and asserts the body contains `"status"`. The past gotcha
it was written to fix — nginx's SPA fallback returning HTML 200 — **is genuinely fixed**, and
that is worth crediting. But the assertion has been strengthened to the point of tautology: the
literal string `"status"` is present in a hardcoded dict returned by a function that touches
nothing. The smoke check passes when:

- Postgres is down or the migration left the schema wrong,
- Redis is down,
- every engine is dead,
- the deploy did not actually swap and prod is still running the **previous** image.

That last one matters most: there is no version, commit or build identifier in the response, so
the smoke check cannot distinguish "new code is live and healthy" from "nothing changed".

The identical endpoint is the **container healthcheck** in both the Dockerfile and compose. So
an api container whose DB pool is exhausted stays `healthy`, `nginx` keeps routing to it, and
`depends_on: api: condition: service_healthy` is satisfied by a process that can answer one
route.

`/api/v1/health/extended` (`health.py:33-66`) does all the right things — bounded Redis probe
(2 s, with a written rationale), `SELECT 1`, per-engine heartbeat age with OK/STALE/DOWN, and a
`DEGRADED` aggregate. **It is used by nothing.** It is exactly the readiness probe both call
sites need.

## OPS-13 (High) — Every engine image ships a HEALTHCHECK that can only ever fail

**Where:** all six engine Dockerfiles, e.g. `src/engines/vol/Dockerfile:32-33`,
`src/engines/market_data/Dockerfile:32-33`, `src/engines/risk/Dockerfile:28-29`,
`src/engines/db_writer/Dockerfile:28-29`, `src/engines/analytics/Dockerfile:28-29`.

```dockerfile
CMD python -c "... ts = r.get('heartbeat:vol_engine'); sys.exit(0 if (ts and time.time() - float(ts) < 300) else 1)"
```

The heartbeat is written in **ISO-8601** by `bus.publisher.set_heartbeat`. `float("2026-08-01T…")`
raises `ValueError`, so the probe exits non-zero **unconditionally** whenever a heartbeat
exists. `docker-compose.yml` knows this — it says so explicitly at `:159-163`
(*"float(ts) on an ISO string crashes — we parse via datetime.fromisoformat"*) — and the compose
`healthcheck:` block overrides the image one for all six services. **The fix was applied to
compose and never back-ported to the Dockerfiles.**

Why this is High rather than Medium: `ib_watchdog.sh` triggers on `unhealthy`. Any path that
runs these images without this exact compose file — `docker run` for a one-off, a future
Kubernetes/ECS manifest, `compose run`, a debugging session, or simply someone removing the
"redundant" compose healthcheck — produces containers that are permanently `unhealthy`, which
on the prod host means the watchdog restarts the gateway and the engines **every 15 minutes
forever**. A latent trap that converts a cosmetic cleanup into a production restart loop.

---

# Medium

## OPS-14 (Medium) — Healthchecks prove a heartbeat loop is alive, not that the engine works

The audit asked what each check *should* assert. Current state and proposal:

| Service | Asserts today | Should also assert |
|---|---|---|
| `market-data` | `heartbeat:market_data` < 60 s | IB socket connected **and** last tick for `MARKET_SYMBOL` younger than N s during market hours (weekend-aware, cf. FE-03) |
| `vol-engine` | heartbeat < 300 s | last `latest_vol_surface` write age; last successful chain fetch; calibration not in permanent fallback |
| `risk-engine` | heartbeat < 30 s | book snapshot age (Wave 2 R3-03: `refresh_book_state` never runs live, so the heartbeat ticks while the state is stale forever) |
| `db-writer` | heartbeat < 30 s — **best of the six**, `service.py:121-130` suppresses the heartbeat when the subscriber is down | queue depth below a ceiling; last successful commit age |
| `analytics-engine` | heartbeat < 300 s, ticked by a decoupled 60 s loop that is independent of the hourly batch | last completed batch age (the current check passes while the batch has failed every hour for a week) |
| `execution-engine` | HTTP 200 from `/health`, which **returns** `ib_connected` in the body and does not act on it | `ib_connected` false ⇒ non-200 |
| `api` | see OPS-12 | `/health/extended` `status != DEGRADED` |
| `ib-gateway` | `socat` TCP probe to 4002 — deliberately chosen to catch "dead socket, live process", with a written rationale | cannot assert upstream IBKR connectivity from inside the container; this is why the watchdog exists |

`execution-engine` is the cheapest fix: the value is already computed and returned.
`db-writer`'s heartbeat-suppression pattern is the right template for the others.

## OPS-15 (Medium) — nginx resolves upstreams once at startup; a container recreated outside a deploy strands it on a dead IP

`infrastructure/nginx/nginx.conf:10-11` declares `upstream frontend_upstream { server frontend:8080; }`
and `upstream api_upstream { server api:8000; }` with **no `resolver` directive**. nginx resolves
those names at config load and caches the address for the process lifetime.

`api` is pinned to `172.20.0.13`, so it is safe. `frontend` gets a dynamic address from the
`.128/25` pool (`:798-807`). The deploy path is safe because `remote-deploy.sh:184-185` reloads
nginx after `up -d`. Anything else is not: `docker compose up -d --no-deps frontend`, a manual
recreate, an `--remove-orphans` that re-creates it, or a future watchdog that recreates
containers, all leave nginx proxying to an address nobody holds — **502 on the whole desk until
someone reloads nginx**, with no alert (OPS-01) and no obvious cause.

Fix is one line: a `resolver 127.0.0.11 valid=10s;` plus variable-based `proxy_pass`, or a
documented rule that any frontend recreate is followed by `nginx -s reload`.

## OPS-16 (Medium) — Floating base images and no scheduled rebuild: CVE remediation depends on someone pushing code

Wave 1 flagged `nginx:alpine` unpinned. The pattern is broader:

| Image | Pin | Where |
|---|---|---|
| `nginx:alpine` | floating | `docker-compose.yml:547`, `infrastructure/docker/web.Dockerfile:10` |
| `python:3.11-slim` | floating | all 6 Python Dockerfiles |
| `node:20-alpine` | floating | `web.Dockerfile:9` |
| `postgres:16-alpine`, `redis:7-alpine` | minor-pinned | compose |
| `ghcr.io/gnzsnz/ib-gateway:10.49.1c` | **exact** | compose + remote-deploy — the incident produced the right lesson here |
| obs images | exact versions | compose |

No image is pinned by **digest**, and combined with Wave 1 **DEP-01** (no Python lockfile) two
builds of the same commit are not the same artifact — which is what the ib-gateway incident was
about, at a different layer.

The policy gap is the other half: `security-scan.yml` runs Trivy weekly and fails on
HIGH/CRITICAL, but **nothing rebuilds**. `build.yml` triggers only on a push to `main`. If no
code is pushed for six weeks, prod runs a six-week-old `python:3.11-slim` and the weekly scan
reds every Monday against an image nobody will rebuild. A scheduled `workflow_dispatch` of
`build.yml` (weekly) would turn the scan's signal into an actual patch path.

## OPS-17 (Medium) — The CVE scan misses the analytics engine and scans a tag prod does not run

`.github/workflows/security-scan.yml:34-41` lists 7 images: api, frontend, market-data,
vol-engine, risk-engine, db-writer, execution. `build.yml:128-140` builds **8**:
`fx-options-analytics-engine` is built, pushed and deployed, and never scanned. Same omission
class as Audit 6's finding that analytics-engine is undocumented (docs say 11 containers /
5 engines; reality is 12 / 6).

Secondly, the scan targets `:latest`. Prod runs `sha-<commit>`. When the deploy is behind main
(or a rollback is active) the scanned artifact is not the running one.

## OPS-18 (Medium) — The Redis acceptable-loss statement exists, is buried, and is now partly false

`infrastructure/redis/redis.conf:17-23` does state it — *"RDB snapshots are enough … AOF
disabled: we do not need transaction-level durability, Postgres is the source of truth … Redis
is a cache + pub/sub bus."* That satisfies the letter of the audit item; it should be lifted
into `docs/ops/` rather than living in a config comment nobody reads.

The substance has drifted, though. `maxmemory 256mb` + `maxmemory-policy allkeys-lru` will evict
**any** key under pressure, and Redis now holds state that is not a cache and that Postgres
cannot regenerate:

- the persisted `_fair_q` fair-vol term structure served when markets are closed (commits
  `0f4be379`, `f301a560`);
- the last full IV grid served when markets are closed;
- book snapshots read by the risk gate (Wave 2 EX-08: absent ⇒ **all-zeros book**, fail-open);
- every engine heartbeat, which is the liveness signal for `/health/extended`, the watchdog and
  the compose healthchecks.

Under `allkeys-lru`, memory pressure silently degrades correctness rather than just cache hit
rate — and EX-08 makes one of those evictions a *fail-open risk check*. Either move the durable
keys out of the LRU pool (separate db + `volatile-lru`, or persist them), or restate the
acceptable-loss claim honestly.

Also note `rename-command CONFIG ""` (`:38`) — a deliberate and good hardening — means
`redis-cli CONFIG GET maxmemory` will not work during an incident. Worth a line in the runbook.

## OPS-19 (Medium) — `/opt/fxvol/.env` is parsed by two parsers with different quoting rules

`remote-deploy.sh:49-105` renders one file. It is then read by:

1. **systemd**, `EnvironmentFile=/opt/fxvol/.env` (`fxvol-compose.service:10`) — at every boot;
2. **docker compose** dotenv parsing — at every deploy and every `ec2.ps1` command.

The two implementations disagree on quoting, on embedded `#`, on `$` (compose interpolates,
systemd does not), and on whitespace. Secrets come straight from SSM with no character
constraint — `remote-deploy.sh:41-42` documents a URL-safe constraint for `REDIS_PASSWORD`
only, and only because it is embedded in a URL. A `DB_PASSWORD` containing a space, a `$` or a
quote can therefore:

- boot fine via compose at deploy time, and
- fail or mangle at **boot** via systemd, i.e. the failure surfaces only after a reboot,
  which is the moment nobody is watching.

`EnvironmentFile` is also unnecessary here — compose reads the same file from its project
directory on its own.

## OPS-20 (Medium) — Reboot survival relies on two competing startup paths and has never been drilled

On boot the stack is brought up **twice, concurrently**:

1. `dockerd` starts every `restart: unless-stopped` container as soon as it is up. Docker's
   restart policy **does not honour `depends_on`** — not the ordering and certainly not the
   `condition: service_healthy` gates that compose enforces at `up` time.
2. `fxvol-compose.service` (`Type=oneshot`, `After=docker.service`) runs
   `docker compose up -d --remove-orphans`, which does honour them.

So at boot, `api` can start before postgres is healthy, `nginx` before `api` and `frontend`
exist. `nginx` in particular **exits** when an upstream hostname does not resolve at config
load (OPS-15) — it will crash-restart with backoff until the others appear, which converges but
is neither instant nor silent, and the `depends_on: condition: service_healthy` that the compose
file relies on for correctness is simply not in effect on the path that actually runs at boot.

The unit also declares `After=network-online.target` without `Wants=network-online.target`, so
that target may never be pulled into the transaction.

Nothing in the repo records a reboot drill or a "clean after N seconds" expectation, and the
compose stack has grown to 12 core containers since the unit was written. Drill in runbook §1.

## OPS-21 (Medium) — Prod observability is `docker logs` and nothing else, and the `obs` profile's prod status is unknown

`obs` is opt-in and the compose file argues correctly (`:580-591`) that it must **not** run on
the public host: Grafana host-maps `:3000` with anonymous viewer enabled by default
(`GF_AUTH_ANONYMOUS_ENABLED: ${…:-true}`), `promtail` mounts `/var/run/docker.sock`
**read-write**, `cadvisor` is `privileged: true`. The `metrics` subset (prometheus + cadvisor)
exists precisely as the prod-safe carve-out. That reasoning is sound.

The consequence is not written down anywhere: **if `obs` is not running on prod, then on prod
there are no metrics, no traces, and no log aggregation.** The only history is `docker logs`,
capped by `daemon.json` at 10 MB × 3 per container — and that cap only exists if `setup.sh` ran
(OPS-08). Post-mortem after a crash-loop is limited to whatever fits in 30 MB, and the
three CloudWatch log groups provisioned for this (`/fxvol/{api,engines,nginx}`, 14-day
retention) receive nothing because the `awslogs` driver was never wired
(`EC2_DEPLOYMENT.md:134` still lists it as open).

Concretely: Wave 1's **F-03** — an unauthenticated visitor minting unbounded Prometheus
histogram children until the 512 MB api OOMs — produces a metric that is **never scraped in
prod**, in a container that then restart-loops with **no alert** (OPS-01) and **no retained
logs**. The detection story for F-03 is currently: a human notices the site is slow.

Which profiles are actually armed is the repo variable `COMPOSE_PROFILES`, recorded in no
file. `EC2_DEPLOYMENT.md:60-61` asserts core-only; the existence of the IB watchdog, the
nightly-gateway-restart history and the analytics engine all imply `engines,ib`. Runbook §0
resolves it.

## OPS-22 (Medium) — TLS renewal has no failure visibility and no expiry monitoring

`/etc/cron.daily/fxvol-certbot-renew` (`setup.sh:112-120`) is well built — the `exec -T` note
(*"without it docker compose fails with 'the input device is not a TTY'. That failure is silent
from the outside — the cert renews on disk while the running container keeps serving the old
one until it expires"*) shows the failure mode was already met once and fixed.

But the residual is identical in shape: certbot output goes to cron, no MTA, no alert. A
renewal that starts failing produces zero signal for up to 90 days, at which point the `.dev`
domain — **HSTS-preloaded**, so browsers will not offer a click-through — goes hard-down for
every visitor. Let's Encrypt's own expiry emails were deprecated in 2025 and cannot be relied on.

`nginx.conf:37-38` also hardcodes the `live/valeriandarmente.dev/` lineage path; if certbot ever
side-steps to `-0001` (the exact scenario `init-letsencrypt.sh:31-38` was written to prevent)
nginx fails its config test and the deploy aborts at `nginx -t`.

## OPS-23 (Medium) — The prune comments contradict each other and rollback silently depends on GHCR

`setup.sh:57` installs a weekly `docker system prune -af --filter "until=168h"` with the comment
*"keeps <7d images: previous SHA survives for rollback"*, and `docs/ops/deployment.md:128`
repeats it (*"kept <7 days by the weekly host prune"*).

`remote-deploy.sh:199` runs `docker image prune -af` — **unfiltered** — at the end of **every**
deploy. `-a` removes every image not referenced by a container, and after `up -d` recreates
everything, the previous `sha-` images are referenced by nothing. So the previous SHA is gone
from the host within seconds of each deploy, not after 7 days.

The disk-fill guard is correct and necessary (it is the fix for the 111-image incident) — the
**documentation is wrong**, and it hides a real dependency: rollback is not a local operation,
it always re-pulls from GHCR. If GHCR is degraded, or the packages' retention policy prunes old
SHAs, or the network is the thing that is broken, rollback is unavailable. That should be a
stated dependency, and the weekly `until=168h` prune should be recognised as doing nothing
useful for rollback.

## OPS-24 (Medium) — The deploy job gives up on SSM after 5 minutes while the host keeps deploying

`.github/workflows/deploy.yml:135-151` polls 60 × 5 s = **300 s**, then `exit 1`. The job's own
`timeout-minutes` is 20. A deploy that legitimately exceeds 5 minutes — a cold GHCR pull of 8
images on a t3.small, a slow migration, the `up -d` retry loop at `remote-deploy.sh:169-177`,
or a busy Postgres readiness wait (up to 60 s at `:133-144`) — fails the workflow **while the
host continues**. The result is a red pipeline, no smoke check, and a host in an unknown
(possibly perfectly fine) state. The 20-minute job timeout suggests the 5-minute poll was not
the intended budget.

## OPS-25 (Medium) — The ops docs describe a smaller system than the one that runs

`EC2_DEPLOYMENT.md:10,60-61` states the deployed profile is **core-only** (nginx, frontend, api,
postgres, redis) and that *"the engines / ib / obs profiles are opt-in and not part of the v1
footprint"*. `STATE.md:9-29` is dated 2026-07-17 and says the site is **not serving**
(a CloudFront 504) — while `STATE.md:13` also says the v1 topology has **no CloudFront**.

Meanwhile the repo contains an IB watchdog cron predicated on a live gateway, a pinned
gateway image with a nightly IBC restart, an analytics engine, and `deploy.yml` with
`DEPLOY_ENABLED` armed. `docs/ops/deployment.md` (accurate, well written) says 7 images;
`build.yml` builds 8.

For an ops audit the specific harm is that **the runbook a person reaches for at 02:00
describes a different stack than the one that is broken**. Reconciling `STATE.md §0` and
`EC2_DEPLOYMENT.md §1` against the live host is Audit 11 territory, but the ops-critical subset
(which profiles are armed, what instance type, is `obs` on) belongs in a single place.

## OPS-26 (Medium) — One CPU limit in the whole stack

`cpus: 0.5` on `analytics-engine` only, with an excellent rationale (`docker-compose.yml:330-335`)
— the hourly GMM refit + PCA snapshot + PC3 recompute used to spike both cores from inside
`vol-engine`, so it was extracted and capped. That is exactly the right response to the incident.

No other service has a CPU limit. The same cascade can originate from `vol-engine`'s SVI/GARCH
calibration, from `risk-engine`'s VaR/stress grid, from a Postgres autovacuum on the growing
history tables, or from the ib-gateway JVM's nightly restart (already observed as a 1-2 min
spike). On 2 vCPUs with no `cpu_shares` differentiation, any of those starves the real-time
engines, whose healthchecks then flip `unhealthy`, which trips the watchdog (OPS-09), which
restarts the gateway — turning a CPU spike into an IB session bounce.

## OPS-27 (Medium) — `/var/log/fxvol-ib-watchdog.log` is unrotated and unread

`setup.sh:149` appends every watchdog invocation's output to `/var/log/fxvol-ib-watchdog.log`
with no logrotate entry. The script is quiet on the healthy path, so growth is bounded in normal
operation — but during a sustained IB outage (weekend, IB maintenance, an expired session) it
logs a cooldown line every 2 minutes, ~720 lines/day, indefinitely. On a 30 GB root already
identified as the system's tightest resource, an unbounded root-owned log outside Docker's
`json-file` rotation is a gap in the "log rotation on every container" requirement — this one
is not in a container at all.

## OPS-28 (Medium) — Nothing watches disk headroom, and the scheduled prune does not address what grows

The disk-fill incident (111 accumulated `sha-` images → stalled `compose pull` → opaque SSM
"ipc messaging received timeout") is guarded at exactly one point: `docker image prune -af`
after each deploy. That handles images. Nothing handles:

- `postgres_data` — growing continuously; Wave 2 DI-03 notes ~300 k rows/week/position of
  history, and DI-35 found a pruning job that stops permanently and silently on a FK error;
- `loki_data` / `prom_data` / `tempo_data` — capped by config (14 d, 7 d/512 MB) *if* `obs` runs;
- `redis_data` RDB;
- container json-file logs — 10 MB × 3 × ~12 containers ≈ 360 MB, only if `daemon.json` is
  present (OPS-08);
- `/var/log/fxvol-ib-watchdog.log` (OPS-27);
- `/tmp` dumps — `setup.sh:129-135` and `remote-deploy.sh:119-122` both write a full `pg_dump`
  to `/tmp` before uploading. Both `rm -f` afterwards, and both would leave the file behind if
  the `aws s3 cp` fails and `set -e` aborts. On a 30 GB root with a growing DB, a repeatedly
  failing backup fills the disk with the dumps it could not upload.

Visibility is a single `df -h /` inside `ec2.ps1 stack`, which is a human typing a command.
There is no threshold, no alarm (OPS-01), and the failure signature is opaque by precedent.

## OPS-29 (Medium) — The pre-deploy dump path has no lifecycle-verified bound

`remote-deploy.sh:120-121` uploads to `s3://fxvol-backups/postgres/pre-deploy/` on every deploy.
The `ArchiveOldBackups` lifecycle rule (`STATE.md §2.2`) filters on prefix `postgres/`, which
does include `postgres/pre-deploy/` — so it is covered. Recording it because it is easy to
break: continuous deploy means one full dump per merge, and if the rule's prefix is ever
narrowed to `postgres/fxvol-` (matching only the nightly naming), the pre-deploy path becomes
unbounded S3 growth. Worth an explicit note in RESTORE.md.

---

# Low

- **OPS-30** — `fxvol-compose.service:4` has `After=docker.service network-online.target`
  without `Wants=network-online.target`; the target may not be pulled into the boot transaction.
- **OPS-31** — `build-essential` + `libpq-dev` remain in the final `api`, `execution`,
  `vol-engine` and `analytics-engine` images (single-stage builds). A compiler in a
  network-facing production image, plus image size on the 30 GB root and on every deploy pull.
  `db-writer` gets this right (`libpq5` only).
- **OPS-32** — `obs/prometheus.yml:9-10` hardcodes `external_labels: environment: dev`, so
  metrics scraped on prod would be labelled `dev`.
- **OPS-33** — Three CloudWatch log groups exist, are paid for (at $0 while empty) and receive
  nothing; the `awslogs` driver they were created for was never wired. Either wire it or delete
  them so the inventory stops implying a capability that does not exist.
- **OPS-34** — `analytics-engine` is absent from `docker-compose.override.yml`, so it alone has
  no dev live-reload; a code change there needs a rebuild while the other five hot-reload.
- **OPS-35** — `gen-commands.sh` generates `/opt/fxvol/cmd/all/down` as
  `sudo docker compose down` with no confirmation prompt, sitting next to `./all/up` in an
  `ls`-discoverable folder on the production host.

---

# Verified clean (worth recording)

Not everything here is a gap, and several of these are better than the norm:

- **Migration atomicity.** `env.py` uses Alembic's default single wrapping transaction and none
  of the 55 revisions escape it (`CONCURRENTLY` / `AUTOCOMMIT` / `isolation_level`: zero hits).
  A failed `upgrade head` rolls back completely on Postgres's transactional DDL, and `set -e`
  aborts the deploy *before* the code swap, leaving the previous working stack serving.
- **The migrate-before-swap ordering itself**, with a pre-migration dump to S3, is the right
  shape; the `docker run` vs `compose run` workaround for the pinned-IP collision
  (`remote-deploy.sh:145-157`) is correctly reasoned and well documented.
- **The SPA-fallback smoke gotcha is genuinely fixed** — the check asserts the body, not the
  status code (the assertion is just too weak now, OPS-12).
- **The nginx bind-mount reload step is present** (`remote-deploy.sh:184-185`), with
  `nginx -t` first so an invalid config aborts the deploy instead of killing the edge.
- **The image-prune disk guard is present** and correctly placed after `up -d`.
- **ib-gateway is pinned by exact tag** in both compose and `remote-deploy.sh`, with the volume
  drift mechanism explained at both sites — the version-drift incident produced a durable fix.
- **Secrets never transit GitHub.** OIDC federation, no stored AWS key, SSM read on the host via
  the instance role, `.env` written under `umask 077`, IB credentials fetched *only* when the
  `ib` profile is armed so a core-only box never holds broker creds on disk.
- **The `ENV=prod` boot guard** on `AUTH_SECRET` + `AUTH_COOKIE_SECURE` fails closed correctly
  (the model OPS-11 should copy).
- **`db-writer` suppresses its own heartbeat when the subscriber is down** — the only
  healthcheck in the stack that reports a dependency rather than a loop.
- **The watchdog cron invokes `/bin/sh <script>`** specifically so a lost execute bit cannot
  silently disable it — the correct lesson from that incident, applied at that site.
- **ufw 80/443 only, port 22 closed, admin via SSM Session Manager**; S3 backups bucket has
  versioning, full public-access block, and an instance role with **no** `s3:Delete`.
- **The `obs`-vs-`metrics` profile split** is a deliberate, well-argued security carve-out
  (privileged cadvisor, rw docker.sock, anonymous Grafana kept off the public host).
- **Redis `rename-command FLUSHDB/FLUSHALL/CONFIG ""`** — a compromised service cannot wipe the
  bus.

---

# What could not be verified without the host

Everything below needs the live EC2 or a local stack and is Valérian's to run. Exact commands
are in [`08-runbook-draft.md`](08-runbook-draft.md). **None were run by this audit.**

| # | Question | Blocks |
|---|---|---|
| 1 | Is `/etc/cron.daily/fxvol-postgres-backup` present, and has it produced objects? | OPS-02 |
| 2 | Does a restore of the newest dump into a scratch container succeed? | OPS-02 |
| 3 | Is `/etc/docker/daemon.json` present with the 10m×3 rotation? | OPS-08, OPS-21 |
| 4 | Is `/etc/cron.d/fxvol-ib-watchdog` present, and what is in its log? | OPS-08, OPS-09 |
| 5 | What is the instance type and free RAM/swap under load? | OPS-06 |
| 6 | Which `COMPOSE_PROFILES` are armed; is `obs`/`metrics` running? | OPS-21, OPS-25 |
| 7 | Root disk usage now, and growth rate of `postgres_data` | OPS-28 |
| 8 | Does a full host reboot converge to all-healthy unattended, and in how long? | OPS-20 |
| 9 | Does a rollback deploy to the previous SHA succeed end-to-end? | OPS-04 |
| 10 | Does killing db-writer for 60 s produce a measurable gap in Postgres? | OPS-01, OPS-07 |
| 11 | Does `docker compose stop db-writer` complete its drain within 10 s? | OPS-07 |
| 12 | Do the engine containers report `healthy` from the **image** healthcheck when run without the compose override? | OPS-13 |
| 13 | Certificate expiry date and last successful renewal | OPS-22 |
| 14 | Full SSM parameter inventory (`describe-parameters`, names only) vs the 10 the code expects | OPS-11 |

---

# Recommended sequencing

1. **OPS-02 then OPS-01.** Verify the backup exists and restore one — it is the audit's stated
   single most important item and it is a one-hour task. Then arm the three alarms that make
   every other failure in this report visible. Nothing else is worth doing first.
2. **OPS-07, OPS-09, OPS-12.** Three small, high-value edits: a `stop_grace_period` on
   db-writer and execution-engine; `execution-engine` + `risk-engine` added to the watchdog's
   `ENGINES`; the api healthcheck and deploy smoke pointed at `/health/extended` with a
   deployed-SHA field. OPS-09 also closes the operational half of Wave 2's EX-02.
3. **OPS-03, OPS-04, OPS-05.** Deploy-pipeline correctness: require CI green, write down the
   rollback-with-migration procedure, add an N-1 compatibility rule for migrations.
4. **OPS-06, OPS-08.** Size the limits to the real instance; make `remote-deploy.sh` re-run the
   idempotent `setup.sh` so the host-side ops layer stops drifting from the repo.
5. **OPS-10, OPS-11.** DR beyond Postgres (EBS snapshot policy) and a single canonical SSM
   inventory with a fail-closed guard on `REDIS_PASSWORD`.

The Medium/Low set is a single grouped issue apart from OPS-13 (High, and a two-line fix per
Dockerfile that should ride along with whatever touches them next).
