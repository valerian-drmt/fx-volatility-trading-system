# Postgres backup restore runbook

Backups: `/etc/cron.daily/fxvol-postgres-backup` (installed by `setup.sh`)
uploads a nightly `pg_dump -Fc` to `s3://fxvol-backups/postgres/` using the
EC2 instance role — no static keys anywhere. `remote-deploy.sh` additionally
uploads a pre-migration dump to `s3://fxvol-backups/postgres/pre-deploy/`
before every schema migration. S3 lifecycle: 30 d → Glacier IR, 365 d expiry.

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

## 5. Freshness alarm (follow-up, monitoring phase)

Cheap cron candidate: newest object age > 26 h → publish to the `fxvol-alarms`
SNS topic. Not armed yet — tracked in the monitoring follow-up.

---

**Restore test log** (append a line after each §3 run):

| Date | Dump | Result |
|---|---|---|
| _none yet — run once before go-live_ | | |
