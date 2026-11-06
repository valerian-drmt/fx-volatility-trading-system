# Postgres backup restore runbook

Backups: `/etc/cron.daily/fxvol-postgres-backup` (a wrapper installed by
`setup.sh` and re-installed by every deploy) runs
`infrastructure/ec2/postgres_backup.sh`, which uploads a nightly `pg_dump -Fc`
to `s3://fxvol-backups/postgres/` using the EC2 instance role — no static keys
anywhere. `remote-deploy.sh` runs the same script before every schema
migration, into `s3://fxvol-backups/postgres/pre-deploy/`. S3 lifecycle:
30 d → Glacier IR, 365 d expiry. Freshness: §5.

**An untested backup is not a backup.** Run §3 once after the first deploy and
record the date at the bottom of this file; re-run monthly.

## 1. List backups

```bash
aws s3 ls s3://fxvol-backups/postgres/ --region eu-west-1
aws s3 ls s3://fxvol-backups/postgres/pre-deploy/ --region eu-west-1
```

(Host shell via SSM, or any laptop session with the `fxvol-dev` profile.)

## 2. Integrity check (cheap, monthly)

```bash
aws s3 cp s3://fxvol-backups/postgres/fxvol-<ts>.dump /tmp/check.dump --region eu-west-1
pg_restore --list /tmp/check.dump | head    # TOC readable = dump not corrupt
rm /tmp/check.dump
```

## 3. Test restore into a scratch container (does not touch the live DB)

On the EC2 host:

```bash
aws s3 cp s3://fxvol-backups/postgres/fxvol-<ts>.dump /tmp/restore-test.dump --region eu-west-1
docker run --rm -d --name pg-restore-test -e POSTGRES_PASSWORD=scratch postgres:16-alpine
docker cp /tmp/restore-test.dump pg-restore-test:/tmp/x.dump
docker exec pg-restore-test sh -c \
  'until pg_isready -U postgres; do sleep 1; done;
   createdb -U postgres fxvol;
   pg_restore -U postgres -d fxvol --if-exists --clean /tmp/x.dump'
# Spot-check: tables present + one row count.
docker exec pg-restore-test psql -U postgres -d fxvol -c '\dt' | head
docker exec pg-restore-test psql -U postgres -d fxvol -c 'SELECT count(*) FROM vol_config;'
docker rm -f pg-restore-test
rm /tmp/restore-test.dump
```

## 4. Real restore (live incident)

1. Stop the app services, keep postgres up:
   `cd /opt/fxvol && docker compose stop api execution-engine db-writer` (+ any engines).
2. Fetch the chosen dump (see §1) to `/tmp/restore.dump`.
3. Restore over the live database:
   ```bash
   docker compose exec -T postgres pg_restore -U fxvol -d fxvol --if-exists --clean < /tmp/restore.dump
   ```
4. Verify the schema matches the running code:
   ```bash
   docker compose run --rm --no-deps api \
     python -m alembic -c src/persistence/alembic.ini current
   ```
   If the dump predates the current head, run `upgrade head`.
5. `docker compose up -d --remove-orphans` and smoke:
   `curl -s https://valeriandarmente.dev/fx-volatility-trading-system/api/v1/health`.
6. `rm /tmp/restore.dump`.

## 5. Freshness signal

`infrastructure/ec2/postgres_backup.sh` (the script behind the nightly cron
**and** the pre-deploy dump) stamps `/opt/fxvol/state/postgres-backup.last`
after every successful upload: `ts=<epoch> size=<bytes> key=<s3 key>`. The api
reads it through `./state:/state:ro` and exposes it twice:

- `GET /api/v1/health/extended` → `components.backup`: `OK (3.2h)`,
  `STALE (31h)` after 26 h, `UNKNOWN (no backup stamp)` when the cron never
  succeeded on this host. STALE and UNKNOWN make the aggregate `DEGRADED`.
- `/metrics` → `fxvol_postgres_backup_age_seconds` (scraped by the `api` job
  in `obs/prometheus.yml`). The alert rule (age > 26 h) is part of the
  alerting ticket (#342).

Cron health on the host: `cat /opt/fxvol/state/postgres-backup.last`,
`tail /var/log/fxvol-postgres-backup.log`. The wrapper in
`/etc/cron.daily/fxvol-postgres-backup` is re-installed by every deploy.

## 6. Restore drill (automated §3)

```bash
sh /opt/fxvol/infrastructure/ec2/restore_drill.sh                 # newest nightly dump
sh /opt/fxvol/infrastructure/ec2/restore_drill.sh postgres/pre-deploy/<name>.dump
```

Scratch `postgres:16-alpine` container, no network, 256 MB cap, removed on
exit. Prints table count, alembic head, a few row counts, and the log row to
paste below. Run it once after this lands, then monthly.

---

**Restore test log** (append the row `restore_drill.sh` prints):

| Date | Dump | Result |
|---|---|---|
| _none yet — run `restore_drill.sh` once before go-live_ | | |
