# Ops runbook — DRAFT (Audit 8 deliverable)

Reboot · Rollback · Restore-from-backup · IB-session recovery.

> **Status: DRAFT.** Produced by Audit 8 by reading configuration only. **No step below was
> executed** — the audit did not touch the EC2 host, prod, or a running stack. Every procedure
> is marked **UNVERIFIED** unless stated otherwise, meaning it is derived from the committed
> scripts and has never been walked end-to-end. Walk each one once, correct what is wrong,
> drop the UNVERIFIED markers, and move the file to `docs/ops/runbook.md`.
>
> **Secrets rule applies throughout.** No command below prints a secret. Never
> `docker inspect` a fxvol container's `Config.Env`, never `cat /opt/fxvol/.env`, never
> `aws ssm get-parameter` without a `--query` that excludes `Parameter.Value`.

---

## 0. Orientation — run this first, every time

Before any procedure, establish what is actually running. Several ops docs disagree with each
other (findings OPS-25), so trust the box.

```powershell
.\scripts\aws\ec2.ps1 instance-status      # running? public IP?
.\scripts\aws\ec2.ps1 stack                # containers + status + RAM/CPU + host mem/swap + heartbeats
.\scripts\aws\ec2.ps1 health               # public smoke from the laptop
```

Then, in an SSM shell (`.\scripts\aws\ec2.ps1 connect`):

```bash
# Which profiles are armed? (grep only the profile line — never cat the whole .env)
grep '^COMPOSE_PROFILES=' /opt/fxvol/.env

# What box is this, and how much headroom is there?
cat /opt/fxvol/cmd/all/stats >/dev/null 2>&1 && /opt/fxvol/cmd/all/stats | head -3
free -m ; df -h / ; swapon --show

# Is the host-side ops layer actually installed? (finding OPS-08)
ls -l /etc/docker/daemon.json \
      /etc/cron.daily/fxvol-postgres-backup \
      /etc/cron.daily/fxvol-certbot-renew \
      /etc/cron.weekly/fxvol-docker-prune \
      /etc/cron.d/fxvol-ib-watchdog \
      /etc/systemd/system/fxvol-compose.service
```

**UNVERIFIED:** whether any of those six files exist on the current host. `setup.sh` is a
manual one-shot and the deploy pipeline never re-runs it, so a host provisioned before a cron
was added does not have it. If any are missing, the fix is to re-run the idempotent installer:

```bash
sudo bash /opt/fxvol/infrastructure/ec2/setup.sh    # idempotent by design — UNVERIFIED in practice
```

---

## 1. Reboot

### 1.1 What is supposed to happen

Two paths start the stack at boot, concurrently (finding OPS-20):

1. `dockerd` restarts every `restart: unless-stopped` container. **This path ignores
   `depends_on` entirely** — neither the ordering nor the `condition: service_healthy` gates
   apply. Expect `api` to start before postgres is ready and `nginx` to exit and crash-restart
   until `api` and `frontend` resolve (nginx resolves upstream hostnames once at config load).
2. `fxvol-compose.service` (`Type=oneshot`, `RemainAfterExit=yes`) runs
   `docker compose up -d --remove-orphans`, which *does* honour the conditions and reconciles.

Convergence is expected but has never been measured.

### 1.2 Planned reboot

```powershell
.\scripts\aws\ec2.ps1 connect
```
```bash
# 1. Take a manual dump first — a reboot is a good moment to have one (§3).
sudo /etc/cron.daily/fxvol-postgres-backup      # UNVERIFIED: may not exist

# 2. Stop cleanly so db-writer drains and execution-engine disconnects from IB.
#    NOTE: stop_grace_period is unset everywhere, so this gives each container 10s
#    then SIGKILL (finding OPS-07). Watch for a truncated drain.
cd /opt/fxvol && sudo docker compose stop db-writer execution-engine
sleep 15
sudo systemctl stop fxvol-compose.service

# 3. Reboot.
sudo reboot
```

### 1.3 After the reboot — acceptance checks

```bash
systemctl status fxvol-compose.service          # active (exited), no failure
cd /opt/fxvol && sudo docker compose ps         # every container Up, none Restarting
sudo docker compose ps --format '{{.Name}} {{.Status}}' | grep -i unhealthy   # expect empty
curl -s localhost/healthz                        # nginx alive
```
```powershell
.\scripts\aws\ec2.ps1 health                     # public path end-to-end
```

**Record for the drill:** time from `reboot` to all-healthy; how many times nginx restarted;
whether any container needed a manual `up -d`.

**UNVERIFIED — this drill has never been run.** Until it is, treat an unattended reboot
(e.g. an AWS-scheduled instance retirement) as an event requiring a human to confirm recovery.

### 1.4 If nginx will not come up

Almost always a stale or unresolvable upstream (finding OPS-15):

```bash
cd /opt/fxvol
sudo docker compose ps api frontend             # must both be Up first
sudo docker compose exec -T nginx nginx -t      # config valid?
sudo docker compose exec -T nginx nginx -s reload
# Last resort — recreates nginx and re-resolves both upstreams:
sudo docker compose up -d --force-recreate nginx
```

Also remember: **any time `frontend` is recreated outside a deploy, nginx must be reloaded**, or
it proxies to an address nobody holds and the desk 502s with no other symptom.

---

## 2. Rollback

### 2.1 The supported path

```powershell
.\scripts\aws\ec2.ps1 deploy -Sha <previous-good-sha>
# equivalent to: gh workflow run deploy-prod -f deploy_sha=<sha>
```

This re-runs the whole `remote-deploy.sh` against the older image tag. The `sha-<commit>` images
live in **GHCR**, not on the host — `remote-deploy.sh` runs `docker image prune -af` at the end
of every deploy, so the previous SHA is removed from the box within seconds (finding OPS-23,
the `setup.sh` and `docs/ops/deployment.md` comments claiming a 7-day local retention are
wrong). **Rollback therefore requires GHCR to be reachable.**

### 2.2 The part that is NOT handled — read before rolling back

`remote-deploy.sh` unconditionally runs `alembic upgrade head` from whichever image it is
deploying. Rolling back to an older image runs *its* `upgrade head`, which is a **no-op**
because the DB is already ahead. Result: **old code against the new schema** (finding OPS-04).

Decide which case you are in **before** triggering the rollback:

| Did the bad deploy include a migration? | Action |
|---|---|
| No | Plain `deploy -Sha <prev>` is safe. This is the common case. |
| Yes, additive only (new table/column, nullable) | Plain rollback is usually safe — old code ignores the new objects. Confirm by reading the revision. |
| Yes, destructive (drop/rename column, NOT NULL, type change) | **Do not roll back code alone.** Either fix forward, or do a full restore (§3.4) to the pre-deploy dump and accept the loss of everything written since. |

Identify what the deploy migrated:

```bash
cd /opt/fxvol
sudo docker compose exec -T api python -m alembic -c src/persistence/alembic.ini current
sudo docker compose exec -T api python -m alembic -c src/persistence/alembic.ini history -r-5:current
```

There is **no tested `alembic downgrade` path**. The 55 revisions have not been reviewed for
downgrade safety.

### 2.3 Verifying a rollback landed

The deploy's own smoke check asserts only that `/api/v1/health` contains `"status"`, which is a
hardcoded constant returned regardless of the version, the DB or the engines (finding OPS-12).
It **cannot** tell you the rollback took effect. Verify by image digest instead:

```bash
cd /opt/fxvol
sudo docker compose ps --format '{{.Name}}\t{{.Image}}'      # tags must read sha-<previous>
curl -s localhost:8000/api/v1/health/extended 2>/dev/null || \
  sudo docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/health/extended
```

`/health/extended` is the endpoint that actually probes Redis, Postgres and the engine
heartbeats. Use it for every post-deploy judgement until the smoke check is fixed.

### 2.4 If the deploy itself failed

- **SSM reports failure, red workflow.** Check whether the host actually finished — the workflow
  gives up after 5 minutes while `remote-deploy.sh` keeps going (finding OPS-24). Re-run §0
  before concluding anything.
- **`nginx -t` failed.** The deploy aborted *before* reloading, so the previous working nginx
  config is still live. Fix the conf, redeploy.
- **The migration failed.** Verified by inspection: the upgrade runs in a single wrapping
  transaction with no `CONCURRENTLY`/`AUTOCOMMIT` escapes in any of the 55 revisions, so
  Postgres rolled the whole thing back and `set -e` aborted *before* the code swap. The old
  stack is still serving the old schema. Fix the migration and redeploy — no restore needed.
- **`compose up -d` failed 3× (port race).** Stack may be half-swapped. Re-run the deploy; if it
  still fails, `cd /opt/fxvol && sudo docker compose up -d --remove-orphans` by hand and check
  `docker compose ps` for containers on mixed image tags.

---

## 3. Restore from backup

> **This entire section is UNVERIFIED. No restore has ever been performed** — the log table in
> `infrastructure/ec2/RESTORE.md` reads *"none yet — run once before go-live"*, and it is not
> confirmed that the nightly backup cron is even installed on the host (finding OPS-02).
> **Do §3.1 and §3.2 now, in calm conditions, not during an incident.**

The authoritative procedure is `infrastructure/ec2/RESTORE.md`. This section is the incident-time
condensation plus the verification steps that file asks for and that nobody has done.

### 3.1 Does a backup exist at all? (do this first)

```bash
aws s3 ls s3://fxvol-backups/postgres/ --region eu-west-1 | tail -10
aws s3 ls s3://fxvol-backups/postgres/pre-deploy/ --region eu-west-1 | tail -5
ls -l /etc/cron.daily/fxvol-postgres-backup
```

Expect one object per day, each tens-to-hundreds of MB. If the newest object is older than
~26 hours, the backup is broken **and nothing has been telling you** (finding OPS-01) — the
cron writes to stdout and the host has no MTA.

Force one now and confirm it lands:

```bash
sudo /etc/cron.daily/fxvol-postgres-backup && \
  aws s3 ls s3://fxvol-backups/postgres/ --region eu-west-1 | tail -1
```

### 3.2 Test restore into a scratch container (never touches the live DB)

This is the step that turns "a backup exists" into "a backup works". Run it once now, then
monthly, and **append a row to the log table at the bottom of `RESTORE.md` each time.**

```bash
TS=<timestamp-of-newest-dump>
aws s3 cp s3://fxvol-backups/postgres/fxvol-$TS.dump /tmp/restore-test.dump --region eu-west-1

# Cheap integrity gate first — a readable TOC means the custom-format dump is not truncated.
docker run --rm -v /tmp:/t postgres:16-alpine pg_restore --list /t/restore-test.dump | head

docker run --rm -d --name pg-restore-test -e POSTGRES_PASSWORD=scratch postgres:16-alpine
docker cp /tmp/restore-test.dump pg-restore-test:/tmp/x.dump
docker exec pg-restore-test sh -c \
  'until pg_isready -U postgres; do sleep 1; done;
   createdb -U postgres fxvol;
   pg_restore -U postgres -d fxvol --if-exists --clean /tmp/x.dump'

# Acceptance: schema present, alembic head recorded, and real row counts.
docker exec pg-restore-test psql -U postgres -d fxvol -c '\dt' | head -20
docker exec pg-restore-test psql -U postgres -d fxvol -c 'SELECT version_num FROM alembic_version;'
docker exec pg-restore-test psql -U postgres -d fxvol -c 'SELECT count(*) FROM vol_config;'
docker exec pg-restore-test psql -U postgres -d fxvol -c 'SELECT count(*) FROM open_position;'

docker rm -f pg-restore-test && rm /tmp/restore-test.dump
```

**Record the wall-clock time of the restore step.** That number is the RTO for §3.4, and it is
currently unknown.

### 3.3 Choosing a dump

- `postgres/fxvol-<ts>.dump` — nightly, ~06:25 UTC (cron.daily/anacron).
- `postgres/pre-deploy/fxvol-pre-sha-<sha>-<ts>.dump` — taken immediately **before** each
  migration. This is the right choice when a bad migration is the reason you are here.

Lifecycle: Standard → Glacier Instant Retrieval at 30 days, expiry at 365 days, bucket
versioning on, instance role has no `s3:Delete`. Objects older than 30 days retrieve with a
small latency and cost.

### 3.4 Real restore over the live database

**Destructive.** `--clean` drops before it recreates; a failure mid-restore leaves neither the
old nor the new dataset. Take a fresh dump of the current (broken) state first — it costs
minutes and is the only way back if the restore itself goes wrong.

```bash
cd /opt/fxvol

# 0. Snapshot the current state before destroying it.
sudo docker compose exec -T postgres pg_dump -U fxvol -Fc fxvol > /tmp/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).dump

# 1. Stop every writer. Keep postgres up.
sudo docker compose stop api execution-engine db-writer risk-engine vol-engine market-data analytics-engine

# 2. Fetch the chosen dump.
aws s3 cp s3://fxvol-backups/postgres/<chosen>.dump /tmp/restore.dump --region eu-west-1

# 3. Restore.
sudo docker compose exec -T postgres pg_restore -U fxvol -d fxvol --if-exists --clean < /tmp/restore.dump

# 4. Reconcile the schema with the running code.
sudo docker compose run --rm --no-deps api python -m alembic -c src/persistence/alembic.ini current
#    If the dump predates the deployed code's head:
sudo docker compose run --rm --no-deps api python -m alembic -c src/persistence/alembic.ini upgrade head

# 5. Bring everything back and verify properly.
sudo docker compose up -d --remove-orphans
sudo docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/health/extended
rm /tmp/restore.dump
```

```powershell
.\scripts\aws\ec2.ps1 health
```

### 3.5 What a Postgres restore does NOT recover

- **Anything the db-writer missed.** The bus is Redis **pub/sub**, fire-and-forget with no
  backlog. Everything published while db-writer was down or being killed mid-drain is gone and
  is not in any backup (findings OPS-01, OPS-07).
- **`ib_gateway_jts`.** Not backed up. Contains the Gateway install **and** the API Trusted-IPs
  list configured by hand over VNC. See §4.5.
- **`/etc/letsencrypt`.** Not backed up; re-issue with `init-letsencrypt.sh` (subject to Let's
  Encrypt rate limits).
- **`redis_data`, `prom_data`, `loki_data`, `tempo_data`, `grafana_data`.** Not backed up.

There is **no EBS snapshot or AMI policy** (finding OPS-10), so total host loss is a manual
rebuild: `setup.sh` → deploy → `init-letsencrypt.sh` → VNC Trusted-IPs → §3.4 restore. That path
is **UNVERIFIED** and its duration is unknown.

---

## 4. IB session recovery

### 4.1 Know the normal event first

The gateway runs an IBC auto-restart at `AUTO_RESTART_TIME: "11:59 PM"` **container time (UTC)**
= ~01:59 Paris. A 1–2 minute CPU spike and a brief IB disconnect at that hour is **expected and
benign** — JVM relaunch, re-login, re-subscribe. Do not treat it as an incident.

What is *not* benign: orders working across that window. Wave 2 **EX-02** found they never reach
a terminal state, and the watchdog does not restart `execution-engine` (finding OPS-09), so a
dead IB socket there survives the gateway bounce. **Check open orders after any gateway restart.**

### 4.2 Triage — which failure is this?

```bash
cd /opt/fxvol
sudo docker compose ps ib-gateway market-data vol-engine risk-engine execution-engine
sudo docker compose logs --tail=100 ib-gateway
sudo docker compose exec -T execution-engine curl -fsS http://127.0.0.1:8001/health   # ib_connected field
```

| Signature | Meaning | Go to |
|---|---|---|
| gateway container **exited / restarting**, logs say *"version X not installed: can't find jars folder"* | Image/volume version drift — the pinned tag was bumped but `ib_gateway_jts` still holds the old jars | §4.6 |
| internal connect to 4002 **refused** | The Gateway app is not listening — JVM dead inside a live container | §4.4 |
| socket **up** but engines `unhealthy` / heartbeats stale | Upstream IBKR session lost (IB error 1100) — the classic case the watchdog exists for | §4.3 |
| `execution-engine` `/health` shows `ib_connected: false` while the others are fine | Execution lost its session alone; the watchdog does not cover it | §4.3, restart execution too |
| Everything healthy but no data, weekend/holiday | Market closed. Not an incident. | — |

### 4.3 Manual recovery (what the watchdog does, plus what it forgets)

```bash
cd /opt/fxvol
sudo docker restart fxvol-ib-gateway
sleep 75                                     # IBC login + market-data farm reconnect
sudo docker restart fxvol-market-data fxvol-vol-engine
# NOT done by the watchdog today — do it by hand (finding OPS-09):
sudo docker restart fxvol-risk fxvol-execution
```

Then verify, and check for orphaned orders:

```bash
sudo docker compose exec -T execution-engine curl -fsS http://127.0.0.1:8001/health
sudo docker compose exec -T api curl -fsS http://127.0.0.1:8000/api/v1/health/extended
```

### 4.4 Check the watchdog's own state

Nothing alerts on it and nothing rotates its log (findings OPS-09, OPS-27):

```bash
ls -l /etc/cron.d/fxvol-ib-watchdog
tail -50 /var/log/fxvol-ib-watchdog.log
ls -l /run/fxvol-ib-watchdog.last          # last recovery timestamp; 15-min cooldown
wc -c /var/log/fxvol-ib-watchdog.log       # unrotated — watch it during a long IB outage
```

An empty log means either "never needed" or "never ran". Distinguish them by confirming the
cron file exists and that `docker` is callable as root from cron.

### 4.5 Trusted-IPs / settings loss (after a volume or host rebuild)

If `ib_gateway_jts` is empty or was recreated, the engines will connect and be dropped. The API
settings live in the encrypted `.ibgzenc` inside that volume and must be re-entered **by hand**
over VNC — there is no script:

1. `docker compose --profile ib up -d ib-gateway`
2. VNC to `127.0.0.1:5900` (port is loopback-bound; tunnel it, never expose it).
3. Configure → Settings → API → Settings:
   - uncheck *"Allow connections from localhost only"*;
   - add Trusted IPs one at a time (the GUI rejects CIDRs): `127.0.0.1`, `172.20.0.10`
     (market-data), `172.20.0.11` (vol-engine), `172.20.0.12` (risk-engine).
     **Note:** `execution-engine` is pinned to `172.20.0.14` and connects on the same socat
     relay — add it too if it is refused.
   - OK → File → Save Settings.

**UNVERIFIED:** whether `172.20.0.14` is required in the list. Confirm during the drill.

### 4.6 Version drift (the known crash-loop)

Symptom: `"version X not installed: can't find jars folder"`, gateway crash-looping after an
image tag change. Cause: the Gateway install lives in the persisted `ib_gateway_jts` volume,
which Docker populates from the image **only once**, so a new image's jars never reach it.

The tag is pinned in two places and both must match: `docker-compose.yml` (`IB_GATEWAY_IMAGE`
default) and `infrastructure/ec2/remote-deploy.sh` (`IB_GATEWAY_IMAGE=` in the rendered `.env`).
Recovery is the surgical copy of the new build into the existing volume — procedure in
`infrastructure/ib-gateway/README.md`. **A plain restart does not fix it.**

### 4.7 Session exclusivity

One IB paper account, one session. A web login on `client.interactivebrokers.com` kicks the
container's IBC session. **Stop `ib-gateway` before any portal access**, and never run a local
stack with the `ib` profile while prod holds the session.

---

## 5. Gaps this runbook cannot close

Listed so they are not mistaken for procedures that exist:

- **No alerting.** Every procedure here starts with a human noticing. The SNS topic
  `fxvol-alarms` exists with a confirmed subscription and **zero** alarms publish to it
  (finding OPS-01). Until that changes, the mean time to detection for every incident above is
  "next time Valérian looks".
- **No backup freshness check.** `RESTORE.md §5` proposes one (newest object age > 26 h → SNS)
  and marks it *"Not armed yet"*.
- **No restore has ever been performed** (finding OPS-02). §3.2 is the smallest possible step
  that changes this.
- **No reboot, rollback or DR drill has ever been run** (findings OPS-04, OPS-20, OPS-10).
- **No disk-headroom monitoring** (finding OPS-28). `df -h /` inside `ec2.ps1 stack` is the only
  visibility, and the historical failure signature of a full disk is an opaque SSM timeout with
  empty stderr.
- **No cert-expiry monitoring** (finding OPS-22). The `.dev` TLD is HSTS-preloaded, so an
  expired certificate is a hard outage with no click-through.
