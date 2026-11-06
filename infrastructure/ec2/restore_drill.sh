#!/bin/sh
# Restore drill: prove a Postgres dump restores (#343, audit 08#9).
#
# Restores the newest nightly dump (or the key given as $1) into a throwaway
# postgres:16-alpine container on the host, prints what came back, and removes
# the container. Never touches the live database or the live volume.
#
#   sh /opt/fxvol/infrastructure/ec2/restore_drill.sh
#   sh /opt/fxvol/infrastructure/ec2/restore_drill.sh postgres/pre-deploy/fxvol-pre-sha-abc-2026….dump
#
# Paste the last line (the "| date | dump | result |" row) into the restore test
# log at the bottom of infrastructure/ec2/RESTORE.md.
set -eu

BUCKET="${BACKUP_BUCKET:-fxvol-backups}"
REGION="${AWS_REGION:-eu-west-1}"
NAME="pg-restore-drill"
IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
# The prod host has 3.8 GB and already runs the stack: keep the scratch DB small.
MEM="${DRILL_MEM:-256m}"

key="${1:-}"
if [ -z "$key" ]; then
  key="$(aws s3 ls "s3://${BUCKET}/postgres/" --region "$REGION" \
        | awk '$4 ~ /\.dump$/ {print $4}' | sort | tail -1)"
  key="postgres/${key}"
fi
[ -n "$key" ] && [ "$key" != "postgres/" ] || { echo "restore_drill: no dump found" >&2; exit 1; }

tmp="/tmp/restore-drill.dump"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -f "$tmp"
}
trap cleanup EXIT

echo "restore_drill: fetching s3://${BUCKET}/${key}"
aws s3 cp "s3://${BUCKET}/${key}" "$tmp" --region "$REGION" --only-show-errors
size="$(wc -c < "$tmp")"
echo "restore_drill: dump ${size} bytes, TOC entries: $(pg_restore --list "$tmp" 2>/dev/null | grep -c '^[0-9]' || echo 'n/a (pg_restore not on host, checked in-container)')"

docker rm -f "$NAME" >/dev/null 2>&1 || true
# No network: the scratch DB must not be reachable by anything on the host.
docker run --rm -d --name "$NAME" --network none --memory "$MEM" \
  -e POSTGRES_PASSWORD=drill "$IMAGE" >/dev/null
docker cp "$tmp" "${NAME}:/tmp/x.dump"

docker exec "$NAME" sh -c '
  until pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
  createdb -U postgres fxvol
  # --clean/--if-exists against an empty DB prints harmless "does not exist"
  # notices; a real failure exits non-zero.
  pg_restore -U postgres -d fxvol --if-exists --clean --no-owner --no-privileges /tmp/x.dump 2>/tmp/restore.err || {
    echo "restore_drill: pg_restore failed"; tail -20 /tmp/restore.err; exit 1; }
  echo "restore_drill: pg_restore ok ($(grep -c . /tmp/restore.err) notices)"
'

q() { docker exec "$NAME" psql -U postgres -d fxvol -At -c "$1"; }
tables="$(q "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
alembic="$(q "SELECT version_num FROM alembic_version" 2>/dev/null || echo 'n/a')"
echo "restore_drill: ${tables} tables, alembic head ${alembic}"
echo "restore_drill: row counts"
for t in vol_config trade_structure trade_order open_position open_position_history \
         pca_surface_snapshot vol_surface_snapshot; do
  n="$(q "SELECT count(*) FROM ${t}" 2>/dev/null || echo 'absent')"
  printf '  %-26s %s\n' "$t" "$n"
done

echo
echo "| $(date -u +%Y-%m-%d) | ${key##*/} (${size} bytes) | OK: ${tables} tables, alembic ${alembic}, scratch container, host $(hostname) |"
