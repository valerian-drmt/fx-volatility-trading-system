#!/usr/bin/env bash
# Host-side deploy, invoked over SSM by .github/workflows/deploy.yml AFTER the
# config payload has been extracted to /opt/fxvol.
#
# Secrets are read here, on the host, from SSM Parameter Store via the instance
# role (fxvol-ec2-secrets-role) — they never pass through GitHub or the SSM
# command parameters. Non-secret config is passed in as env by the workflow.
#
# Required env (set by the SendCommand in deploy.yml):
#   IMAGE_TAG         e.g. sha-<gitsha>
#   OWNER             GHCR owner (github user/org)
#   AWS_REGION        e.g. eu-west-1
#   COMPOSE_PROFILES  optional; empty = core stack (api+frontend+nginx+pg+redis)
#
# Rollback: re-run the previous SHA (.\scripts\ops\ec2.ps1 deploy -Sha <prev>);
# the pre-migration dump uploaded below is the DB restore point (see
# infrastructure/ec2/RESTORE.md).
# AUTH_SALT is deliberately not rendered: the api falls back to its code
# default. Provision /fxvol/prod/AUTH_SALT in SSM only when rotating it.
set -euo pipefail

cd /opt/fxvol

REGION="${AWS_REGION:-eu-west-1}"
ssm() { aws ssm get-parameter --name "$1" --with-decryption --query Parameter.Value --output text --region "$REGION"; }

# --- secrets, straight from SSM (never logged, never in GitHub) -------------
DB_PASSWORD="$(ssm /fxvol/prod/DB_PASSWORD)"
# Optional params : tolerate absence (set -e) so a missing SSM entry never
# aborts the deploy. FRED_API_KEY is unused by the current compose; GHCR_TOKEN
# is only needed if the GHCR packages are private (login skipped below if empty).
FRED_API_KEY="$(ssm /fxvol/prod/FRED_API_KEY 2>/dev/null || echo "")"
GHCR_TOKEN="$(ssm /fxvol/prod/GHCR_TOKEN 2>/dev/null || echo "")"
# Auth (single-trader write boundary). Optional in SSM, but the api's ENV=prod
# boot guard refuses the repo-default AUTH_SECRET: an unprovisioned host
# crash-loops the api instead of serving with a forgeable key. Provision both
# params in SSM before the first deploy.
AUTH_SECRET="$(ssm /fxvol/prod/AUTH_SECRET 2>/dev/null || echo "")"
AUTH_PASSWORD_HASH="$(ssm /fxvol/prod/AUTH_PASSWORD_HASH 2>/dev/null || echo "")"
# Redis auth (URL-safe chars only: embedded un-encoded in REDIS_URL). Absent
# from SSM -> compose falls back to the weak dev default; provision it before
# go-live (go/no-go checklist).
REDIS_PASSWORD="$(ssm /fxvol/prod/REDIS_PASSWORD 2>/dev/null || echo "")"

reg="ghcr.io/${OWNER}"

# --- render /opt/fxvol/.env (0600) ------------------------------------------
umask 077
cat > /opt/fxvol/.env <<ENVEOF
DB_PASSWORD=${DB_PASSWORD}
FRED_API_KEY=${FRED_API_KEY}
ENV=prod
TRADING_MODE=paper
READ_ONLY_API=yes
COMPOSE_PROFILES=${COMPOSE_PROFILES:-}
NGINX_CONF_FILE=./infrastructure/nginx/nginx.conf
LETSENCRYPT_DIR=/etc/letsencrypt
CERTBOT_WWW_DIR=/var/www/certbot
API_IMAGE=${reg}/fx-options-api:${IMAGE_TAG}
FRONTEND_IMAGE=${reg}/fx-options-frontend:${IMAGE_TAG}
MARKET_DATA_IMAGE=${reg}/fx-options-market-data:${IMAGE_TAG}
VOL_ENGINE_IMAGE=${reg}/fx-options-vol-engine:${IMAGE_TAG}
RISK_ENGINE_IMAGE=${reg}/fx-options-risk-engine:${IMAGE_TAG}
DB_WRITER_IMAGE=${reg}/fx-options-db-writer:${IMAGE_TAG}
EXECUTION_IMAGE=${reg}/fx-options-execution:${IMAGE_TAG}
IB_GATEWAY_IMAGE=ghcr.io/gnzsnz/ib-gateway:latest
ENVEOF

# Broker credentials: fetched + rendered ONLY when the ib profile is armed, so
# a public core-only box never holds IB creds on disk (compose tolerates the
# absent vars via ${IB_USERID:-}).
case ",${COMPOSE_PROFILES:-}," in
  *,ib,*)
    IB_USERID="$(ssm /fxvol/prod/IB_USERID)"
    IB_PASSWORD="$(ssm /fxvol/prod/IB_PASSWORD)"
    VNC_PASSWORD="$(ssm /fxvol/prod/VNC_PASSWORD)"
    {
      echo "IB_USERID=${IB_USERID}"
      echo "IB_PASSWORD=${IB_PASSWORD}"
      echo "VNC_PASSWORD=${VNC_PASSWORD}"
    } >> /opt/fxvol/.env
    ;;
esac

# Auth vars appended conditionally: emit the secret/hash only when present in
# SSM, so an unprovisioned host keeps the api's fail-closed defaults. Cookie is
# Secure (prod serves HTTPS-only behind nginx).
{
  if [ -n "${AUTH_SECRET}" ];        then echo "AUTH_SECRET=${AUTH_SECRET}"; fi
  if [ -n "${AUTH_PASSWORD_HASH}" ]; then echo "AUTH_PASSWORD_HASH=${AUTH_PASSWORD_HASH}"; fi
  if [ -n "${REDIS_PASSWORD}" ];     then echo "REDIS_PASSWORD=${REDIS_PASSWORD}"; fi
  echo "AUTH_USERNAME=trader"
  echo "AUTH_COOKIE_SECURE=true"
} >> /opt/fxvol/.env

# --- pull + migrate + restart ------------------------------------------------
if [ -n "${GHCR_TOKEN}" ]; then
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${OWNER}" --password-stdin
fi
docker compose pull

# Pre-migration safety dump + migrate BEFORE swapping code (skipped on the
# first-ever deploy when postgres isn't running yet). Dump upload uses the
# instance role — no static keys. Migration runs the NEW image against the
# OLD still-serving stack; alembic upgrade head is idempotent.
if docker compose ps --status running postgres | grep -q postgres; then
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  docker compose exec -T postgres pg_dump -U fxvol -Fc fxvol > "/tmp/fxvol-pre-${IMAGE_TAG}-${ts}.dump"
  aws s3 cp "/tmp/fxvol-pre-${IMAGE_TAG}-${ts}.dump" \
    "s3://fxvol-backups/postgres/pre-deploy/" --sse AES256 --region "$REGION"
  rm -f "/tmp/fxvol-pre-${IMAGE_TAG}-${ts}.dump"
  # Reconcile postgres with the compose file we just extracted before migrating.
  # The migration container reaches the DB by service name over the compose
  # network, but a postgres container left over from an older compose revision
  # can still be running with stale network aliases — 'compose exec' finds it by
  # label while 'compose run' fails to resolve 'postgres' via DNS. Recreating it
  # first is a no-op when the definition is unchanged, and reattaches the same
  # named volume (fxvol_postgres_data) when it is not, so no data is lost.
  docker compose up -d --no-deps postgres
  # A recreated postgres needs a moment before it accepts connections; without
  # this the migration races the restart and fails on a refused connection.
  ready=0
  for _ in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U fxvol -d fxvol >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "remote-deploy: postgres not ready after 60s, aborting before migration" >&2
    exit 1
  fi
  # NOT 'docker compose run api': the api service pins ipv4_address 172.20.0.13
  # (the engines need stable IPs for the IB Gateway trust list), and a one-off
  # 'compose run' container inherits that pin. While the real fxvol-api holds
  # .13 the migration container cannot start — "failed to set up container
  # networking: Address already in use" — so migrate-before-swap could only ever
  # succeed when api happened to be down. Use plain 'docker run' instead: same
  # image, same network, but a dynamic address out of the .128/25 pool.
  net="$(docker network ls --filter name=fxvol-internal --format '{{.Name}}' | head -1)"
  if [ -z "$net" ]; then
    echo "remote-deploy: cannot find the fxvol-internal network" >&2
    exit 1
  fi
  docker run --rm --network "$net" \
    -e "DATABASE_URL=postgresql+asyncpg://fxvol:${DB_PASSWORD}@postgres:5432/fxvol" \
    "${reg}/fx-options-api:${IMAGE_TAG}" \
    python -m alembic -c src/persistence/alembic.ini upgrade head
fi

# Recreating nginx races docker-proxy releasing :80/:443 — the new container can
# bind before the outgoing one's proxy has let go, and compose aborts with
# "failed to set up container networking: Address already in use". The window is
# under a second, but it fails the whole deploy while leaving the stack half
# swapped. Retry a couple of times before giving up.
up_attempt=0
until docker compose up -d --remove-orphans; do
  up_attempt=$((up_attempt + 1))
  if [ "$up_attempt" -ge 3 ]; then
    echo "remote-deploy: 'compose up -d' failed ${up_attempt}x, giving up" >&2
    exit 1
  fi
  echo "remote-deploy: 'compose up -d' failed (attempt ${up_attempt}), retrying in 5s" >&2
  sleep 5
done

# The nginx config is a bind-mounted file. `compose up -d` only recreates a
# container when its image/spec changes, so a config-only change leaves the
# running nginx on the OLD config (the new file sits on disk, unused). Validate
# + hot-reload to actually apply it. set -e aborts the deploy if the new config
# is invalid, leaving the previous (working) nginx running.
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload

# Bootstrap path: on the first-ever deploy postgres only exists now — the
# idempotent re-run is a no-op on the normal path.
docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head

echo "remote-deploy: done (tag ${IMAGE_TAG}, profiles '${COMPOSE_PROFILES:-core}')"
