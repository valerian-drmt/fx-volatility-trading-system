# Ops runbooks

Reboot, rollback, restore and IB-session recovery for the prod host
(`valeriandarmente.dev`, one EC2 instance, `COMPOSE_PROFILES=engines,ib,obs`).
Promoted from the audit-08 draft (`docs/audits/reports/08-runbook-draft.md`)
and rewritten against the scripts as they are now (#346).

> **Secrets rule.** No command here prints a secret. Never `docker inspect` a
> fxvol container's `Config.Env`, never `cat /opt/fxvol/.env`, never
> `aws ssm get-parameter` without a `--query` that excludes `Parameter.Value`
> ([`secrets.md`](secrets.md)).

Every procedure ends with a **Walked** line: the date it was last run end to
end and what it measured. A procedure without a date is written from the
scripts and has not been exercised yet.

| Procedure | Walked |
|---|---|
| [Orientation](#0-orientation) | not yet |
| [Reboot](#1-reboot) | not yet |
| [Rollback](#2-rollback) | not yet |
| [Restore](#3-restore-from-backup) | not yet (`restore_drill.sh` exists, the log in `RESTORE.md` is empty) |
| [IB session recovery](#4-ib-session-recovery) | partly: the gateway volume-drift recovery (2026-07-31) and the competing-session diagnosis (2026-10-10) were done live |

## 0. Orientation

Run first, every time. Trust the box over the docs.

```powershell
.\scripts\aws\ec2.ps1 health        # public: /health git_sha + /health/ready
.\scripts\aws\ec2.ps1 stack         # containers, RAM/CPU, host memory, engine heartbeats
.\scripts\aws\ec2.ps1 inventory     # crons + units present, backup stamp, rollback tag pair,
                                    # pending reboot, newest backups, watchdog state
```

`inventory` prints `MISSING` for any ops file the host lacks. The deploy
re-installs the backup, prune and disk crons on every run; the IB watchdog
cron, the systemd unit and the unattended-upgrades config come from
`infrastructure/ec2/setup.sh`, which is idempotent:

```bash
sudo bash /opt/fxvol/infrastructure/ec2/setup.sh
```

Alerts reach the SNS topic `fxvol-alarms` by email (Alertmanager rules in
`obs/alerts.yml`, the host disk guard, the backup-age metric). Each alert's
description names the command to run; [`alerting.md`](alerting.md) lists them.

**Walked:** not yet.

## 1. Reboot

Take it in the weekend window (Saturday, FX closed) unless the host is down.
`inventory` shows `REBOOT PENDING` when unattended-upgrades installed a kernel.

At boot two paths start the stack at once: dockerd restarts every
`restart: unless-stopped` container (no `depends_on` ordering), and
`fxvol-compose.service` runs `docker compose up -d --remove-orphans`, which
honours the health conditions and converges. Expect nginx to restart a few
times until `api` and `frontend` resolve.

```powershell
.\scripts\aws\ec2.ps1 connect
```
```bash
cd /opt/fxvol
sudo /etc/cron.daily/fxvol-postgres-backup                 # fresh restore point
sudo docker compose stop db-writer execution-engine        # drain the writer, leave IB cleanly
sleep 15
sudo systemctl stop fxvol-compose.service
sudo reboot
```

Acceptance, after about two minutes:

```bash
systemctl status fxvol-compose.service --no-pager         # active (exited)
cd /opt/fxvol && sudo docker compose ps --format '{{.Name}} {{.Status}}' | grep -v healthy   # expect empty
```
```powershell
.\scripts\aws\ec2.ps1 health
.\scripts\aws\ec2.ps1 inventory     # "no reboot pending"
```

If nginx stays down: `sudo docker compose up -d --force-recreate nginx`
(it resolves the upstreams once at config load).

**Record:** time from `reboot` to all healthy, nginx restarts, any manual step.
**Walked:** not yet.

## 2. Rollback

The pipeline side is in [`deployment.md`](deployment.md#rollback): the
previous release's images stay on the host, and a rollback past a migration
stops unless explicitly allowed.

![A rollback past a migration stops unless the downgrade is explicitly allowed, and the downgrade only runs after the pre-deploy dump](../diagrams/ops-rollback-flow.svg)

1. Find the previous good sha: `inventory` prints `previous_tag=sha-<sha>`, or
   `gh run list --workflow deploy.yml --limit 5`.
2. Did the bad release add migrations? Compare the heads:
   ```bash
   git diff --stat <previous>..<bad> -- src/persistence/migrations/versions
   ```
3. Roll back:
   ```powershell
   .\scripts\aws\ec2.ps1 deploy -Sha <previous-sha>                          # no migration in between
   .\scripts\aws\ec2.ps1 deploy -Sha <previous-sha> -AllowSchemaDowngrade    # past a migration
   ```
   Without the switch, a deploy whose database is ahead of the target image
   fails with "this is a rollback past a migration" and changes nothing.
   With it, the running image runs `alembic downgrade <target head>` after the
   pre-deploy dump. A downgrade drops what the newer migrations added (columns,
   tables, rows in them); the dump `s3://fxvol-backups/postgres/pre-deploy/fxvol-pre-sha-<bad>-*.dump`
   is the way back.
4. Verify:
   ```powershell
   .\scripts\aws\ec2.ps1 health      # git_sha = the previous sha, ready = 200
   ```
   ```bash
   cd /opt/fxvol && sudo docker compose ps --format '{{.Name}}\t{{.Image}}' | grep fx-options   # all sha-<previous>
   ```
5. Roll forward again once fixed: an ordinary deploy of the fix runs
   `upgrade head` and re-applies the dropped migrations (their data is gone
   unless restored from the dump).

If the deploy job fails on its 5-minute poll while the host keeps going
(heavy pulls), check `health` and the container images before re-running.

**Drill (exit criterion of iteration 14):** on a weekend, roll back one
release that has no migration, verify, roll forward. Then, on a release that
has one, run the gated path once against the same pair.
**Walked:** not yet.

## 3. Restore from backup

Authoritative procedure: [`infrastructure/ec2/RESTORE.md`](../../infrastructure/ec2/RESTORE.md).
Dumps: nightly `s3://fxvol-backups/postgres/fxvol-<ts>.dump`, pre-deploy
`postgres/pre-deploy/fxvol-pre-sha-<sha>-<ts>.dump`. Freshness: `/health/extended`
`components.backup`, metric `fxvol_postgres_backup_age_seconds`, alerts
`BackupStale` / `BackupSignalMissing`.

### 3.1 Test restore (never touches the live database)

Monthly, and before relying on a dump:

```bash
sudo sh /opt/fxvol/infrastructure/ec2/restore_drill.sh
```

It restores the newest dump into a scratch container, prints table count,
alembic head and row counts, then removes everything. Paste its last line into
the log table at the bottom of `RESTORE.md`; the restore duration it prints is
the RTO of 3.2.

### 3.2 Real restore over the live database

Destructive (`pg_restore --clean`). Follow `RESTORE.md` section 4: snapshot the
current state, stop every writer, restore, `alembic upgrade head` with the
running image if the dump is older than the code, start, verify with
`/health/extended`.

What a restore does not bring back: anything the db-writer had not written
(Redis pub/sub has no backlog), the `ib_gateway_jts` volume (Gateway install +
API Trusted IPs), `/etc/letsencrypt`, and the obs volumes. See
[`redis-durability.md`](redis-durability.md).

**Walked:** not yet.

## 4. IB session recovery

### 4.1 Normal events

- The gateway's IBC auto-restart at 23:59 UTC (01:59 Paris) costs a 1 to 2
  minute CPU spike and a brief disconnect. Benign.
- Weekend and holidays: no quotes, engines idle, the desk shows the
  closed-market banner. Not an incident.

### 4.2 Triage

![The failure signature picks the fix; only a dead Gateway app or a lost upstream session is cured by the restart sequence](../diagrams/ops-ib-recovery.svg)

```bash
cd /opt/fxvol
sudo docker compose ps ib-gateway market-data vol-engine risk-engine execution-engine
sudo docker compose exec -T execution-engine curl -fsS http://127.0.0.1:8001/health   # ib_connected
```

| Signature | Cause | Fix |
|---|---|---|
| gateway crash-loops, "version X not installed: can't find jars folder" | image pin bumped, the `ib_gateway_jts` volume still holds the old build | surgical copy of the new build into the volume: `infrastructure/ib-gateway/README.md`; a restart does not fix it |
| connect to 4002 refused inside the network | the Gateway app is not listening (JVM dead in a live container) | 4.3 |
| socket up, engines unhealthy, heartbeats stale | upstream IBKR session lost (error 1100) | 4.3; the watchdog does this every 2 min with a 15 min cooldown |
| bid/ask -1 on every contract, farms OK, no error code | another session holds the user (portal, mobile, TWS) | close the other session; restarts do not help |
| `alert IBSessionDown` | any of the above | triage |

### 4.3 Manual recovery

The same sequence as `infrastructure/ec2/ib_watchdog.sh`:

```bash
sudo docker restart fxvol-ib-gateway
sleep 75                                     # IBC login + farm reconnect
sudo docker restart fxvol-market-data fxvol-vol-engine fxvol-risk fxvol-execution
```

Then check working orders on the desk (Trade view, blotter): an order that was
working across the outage is reconciled by the execution engine at reconnect
(`order_reconcile_loop`), a book-vs-broker gap opens a break
(`ReconciliationBreakOpen` after 15 min).

`inventory` shows the watchdog's last recovery time; its log is
`/var/log/fxvol-ib-watchdog.log` (rotated weekly).

### 4.4 Trusted IPs after a volume or host rebuild

An empty `ib_gateway_jts` volume loses the API settings. Re-enter them over VNC
(`127.0.0.1:5900`, tunnel only): uncheck "Allow connections from localhost
only", add the engine addresses one per line (`127.0.0.1`, `172.20.0.10`
market-data, `.11` vol-engine, `.12` risk-engine, `.14` execution), Save Settings.

### 4.5 Session exclusivity

One paper account, one session, owned by prod. Stop `ib-gateway` before any
portal or mobile login, and never run a local stack with the `ib` profile
while prod holds the session.

**Walked:** partly (see the table at the top).

## 5. Read-only analysis access to the prod database

Postgres has no public port. Analysis queries go through an SSM tunnel to the
host's loopback, as a role that can only read, and only while the IB account
is a paper account.

| Piece | Where | What it guarantees |
|---|---|---|
| Role `fxvol_reader` | migration `064_reader_role` | SELECT only; every transaction read-only; 30 s statement timeout; 60 s idle-in-transaction timeout; 3 connections |
| Login | `remote-deploy.sh`, `set_reader_login` | LOGIN exists only while `/fxvol/prod/DB_READONLY_PASSWORD` does; sent as a SCRAM verifier, never in clear |
| Port | `docker-compose.yml`, `127.0.0.1:15432` on the host | loopback only, reachable through SSM (IAM-authenticated), never from the internet |
| Client | `scripts/local/prod_db.py` | reads the password from SSM into memory, opens the tunnel, runs one read-only transaction (200 rows by default), closes the tunnel |
| Manual tunnel | `.\scriptsws\ec2.ps1 db-tunnel` | `localhost:15433` on the PC for another client (psql, an IDE); open only while the window is |

**Paper account only.** Three independent gates, each fail-closed:

| Gate | When it runs | What it does |
|---|---|---|
| Deploy (`set_reader_login`) | every deploy | grants LOGIN only if the rendered `TRADING_MODE` is `paper` **and** `runtime_ib_session.account_type` is `paper`; otherwise sets NOLOGIN and clears the password |
| Execution engine (`close_reader_access`) | every heartbeat (10 s) | the gateway reports a live account id (`U...` / `F...`): `ALTER ROLE fxvol_reader NOLOGIN`, open sessions terminated, `reader_access_closed_live_account` logged at critical |
| Client (`prod_db.py`) | every call | reads `account_type` first and refuses anything that is not `paper` (exit code 4) |

Going back to paper does not reopen the access by itself: the next deploy does,
once the engine has reported a paper account again.

**Open the access (once):**

1. AWS console, Parameter Store: create `/fxvol/prod/DB_READONLY_PASSWORD`
   (SecureString, 32+ random URL-safe characters).
2. Deploy (any merge to `main`, or `.\scriptsws\ec2.ps1 deploy`). The log
   line `fxvol_reader login enabled` confirms it.

**Query (nothing to start first):**

```powershell
python scripts/local/prod_db.py --tables
python scripts/local/prod_db.py "SELECT status, count(*) FROM position GROUP BY 1"
python scripts/local/prod_db.py -f query.sql --csv > out.csv
```

Needs the AWS CLI, the Session Manager plugin and the `fxvol-dev` (parameter)
and `admin` (tunnel) profiles on the PC.

**Close the access:** delete the parameter in the console and redeploy; the
deploy sets the role back to NOLOGIN. **Rotate:** change the value, redeploy.

**Walked:** no (added 2026-11-12).

## 6. An order is stuck

For the alerts `OrderNeverPlaced`, `OrderOutlivedItsDay`, `OrderLoopStalled`,
`GateRefusingEverything` and `ReconciliationBreakOpen`, and for an order the
desk shows as waiting longer than it should.

**What "stuck" can mean.** An order row is `pending` (written by the api, not
yet at the broker), `submitted` or `partially_filled` (at the broker), or
ended (`filled`, `cancelled`, `rejected`, `expired`). Three loops of the
execution engine follow a row to its end, each once a minute:

| Loop | What it does | Without it |
|---|---|---|
| reaper | ends a `pending` row that never left after 5 min, expires a row IB no longer holds, adopts an order found at IB by its reference | rows wait for ever and keep their leg reserved |
| reconciler | books fills the handlers missed, from the recorded fills and from IB's executions; completes a trade whose entries all filled | a filled order reads `submitted` |
| watcher | raises a critical event on a row working for more than 10 min; publishes the waiting-order gauges | no alert |

**Step 1: what does the book say.** Read-only, from the PC:

```powershell
python scripts/local/prod_db.py "SELECT id, structure_id, order_role, state, side, qty, qty_filled, ib_order_id, ib_local_symbol, submitted_at, state_updated_at, cancel_requested_at, rejection_text FROM trade_order WHERE state IN ('pending','submitted','partially_filled') ORDER BY id"
python scripts/local/prod_db.py "SELECT created_at, event_type, severity, description FROM trade_event WHERE order_id = <id> ORDER BY id"
```

**Step 2: what does the broker say.** `GET /api/v1/orders` (the desk's Orders
blotter reads it) lists what IB holds open. The order's reference is
`fxvol:<structure_id>:<order_id>`.

**Step 3: read the pair.**

| Book | Broker | Meaning | What to do |
|---|---|---|---|
| `pending`, no `ib_order_id`, under 5 min | absent | being placed, or the engine was unreachable | wait; the reaper ends it at 5 min and frees the leg |
| `pending`, no `ib_order_id`, over 10 min | absent | the reaper is not running (`OrderNeverPlaced`, `OrderLoopStalled`) | step 4 |
| `pending` | present, with our reference | the engine died between the send and the commit | nothing: the reaper adopts it on its next pass |
| `submitted` | present, `Submitted` or `PreSubmitted` | a resting order | nothing; cancel it from the blotter if it is not wanted |
| `submitted` | present, `Inactive` | IB holds it (outside hours, a price cap); `order_held` event says why | nothing; it is working |
| `submitted`, over a day | absent | IB ended it and the handler missed it (`OrderOutlivedItsDay`) | the reaper expires it within 5 min of a pass; if not, step 4 |
| `submitted` | absent, and a position changed | a fill was missed | the reconciler books it from IB's executions within a minute; a break opens meanwhile |
| ended | present and open | the book ended an order IB still works | the session pass revives the row on the next reconnect; cancel at IB from the blotter if unwanted |

**Step 4: a loop is stalled.** On the host:

```bash
docker logs --since 30m fxvol-execution 2>&1 | grep -iE "reaper_loop_error|reconcile_loop_error|stuck_order_watcher_cycle_crashed"
curl -s localhost:8000/api/v1/health/extended | jq '.components'
docker compose restart execution-engine      # handlers are re-attached to open orders at startup
```

A restart is safe for orders at IB: they stay at IB, and the engine
re-attaches to each by its order id or its reference.

**The gate refuses everything** (`GateRefusingEverything`). The reasons are
counted: Grafana "Desk fluidity", panel "Submits refused, by reason", or
`GET /api/v1/trade/refusals`. The usual three: `surface_stale` (the vol
engine is behind: section 4), `trading_halted` (a halt left on: `GET
/api/v1/admin/halt` says who and why), `cap_*` (a limit the trader typed,
tighter than the book: the Risk settings).

**Never:** change a `trade_order.state` by hand to "unstick" it. The state is
written by one module (`persistence.order_state`), which also releases the
reservation and finalises the trade; a hand edit leaves both behind.

**Walked:** no (added 2026-11-14).
