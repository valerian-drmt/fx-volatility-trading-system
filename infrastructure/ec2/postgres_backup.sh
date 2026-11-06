#!/bin/sh
# Nightly Postgres dump to S3 + freshness stamp (#343, audit 08#9).
#
# Runs from /etc/cron.daily/fxvol-postgres-backup, a one-line wrapper that
# setup.sh installs once and remote-deploy.sh re-installs on EVERY deploy, so
# this script (shipped in the deploy payload under /opt/fxvol/infrastructure/
# ec2/) is always the version that matches the running stack.
#
# Credentials: the EC2 instance role (s3:PutObject on fxvol-backups/*). No env
# file, no static keys. Restore procedure and drill: infrastructure/ec2/RESTORE.md.
#
# On success it stamps STATE_FILE with one line:
#     ts=<epoch seconds> size=<bytes> key=<s3 key>
# The api mounts /opt/fxvol/state read-only and turns the stamp into
# fxvol_postgres_backup_age_seconds (Prometheus) + the "backup" component of
# /api/v1/health/extended (STALE after 26 h). A failed run leaves the previous
# stamp in place, so the age keeps growing until someone looks.
set -eu

APP_DIR="${APP_DIR:-/opt/fxvol}"
BUCKET="${BACKUP_BUCKET:-fxvol-backups}"
REGION="${AWS_REGION:-eu-west-1}"
PREFIX="${BACKUP_PREFIX:-postgres}"          # postgres/ (nightly) or postgres/pre-deploy/
STATE_DIR="${APP_DIR}/state"
STATE_FILE="${STATE_DIR}/postgres-backup.last"
MIN_BYTES=10240                               # schema-only is tens of KB; smaller = broken dump

ts="$(date -u +%Y%m%dT%H%M%SZ)"
tag="${1:-}"                                  # optional infix, e.g. pre-<image tag>
name="fxvol-${tag:+${tag}-}${ts}.dump"
tmp="/tmp/${name}"
key="${PREFIX%/}/${name}"

cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

docker compose -f "${APP_DIR}/docker-compose.yml" exec -T postgres \
    pg_dump -U fxvol -Fc --if-exists --clean fxvol > "$tmp"

size="$(wc -c < "$tmp")"
if [ "$size" -le "$MIN_BYTES" ]; then
  echo "postgres_backup: dump too small (${size} bytes), not uploaded" >&2
  exit 1
fi

aws s3 cp "$tmp" "s3://${BUCKET}/${key}" --sse AES256 --region "$REGION" --only-show-errors

# Stamp AFTER the upload succeeded. World-readable: the api container reads it
# through a read-only bind mount and does not run as root.
mkdir -p "$STATE_DIR"
chmod 0755 "$STATE_DIR"
printf 'ts=%s size=%s key=%s\n' "$(date -u +%s)" "$size" "$key" > "${STATE_FILE}.tmp"
chmod 0644 "${STATE_FILE}.tmp"
mv -f "${STATE_FILE}.tmp" "$STATE_FILE"
echo "postgres_backup: uploaded s3://${BUCKET}/${key} (${size} bytes)"
