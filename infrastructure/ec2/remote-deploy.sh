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
#   ALLOW_SCHEMA_DOWNGRADE  optional; "true" lets a rollback downgrade the
#                     database to the target image's alembic head (#345)
#
# Rollback: deploy-prod via workflow_dispatch with deploy_sha=<previous sha>
# (docs/ops/runbooks.md, "Rollback"). The previous release's images stay on the
# host (prune_images.sh keeps current + previous tag). When the database is
# ahead of the target image, the deploy stops unless ALLOW_SCHEMA_DOWNGRADE is
# true; then the RUNNING image (which knows both revisions) downgrades after
# the pre-deploy dump. The dump is the restore point (infrastructure/ec2/RESTORE.md).
# AUTH_SALT is rendered when /fxvol/prod/AUTH_SALT exists (#346); absent, the
# api keeps its code default and logs auth_salt_is_repo_default. Provision it
# together with a new AUTH_PASSWORD_HASH computed with that salt
# (docs/ops/secrets.md, "Inventory").
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
AUTH_SALT="$(ssm /fxvol/prod/AUTH_SALT 2>/dev/null || echo "")"
# Redis auth (URL-safe chars only: embedded un-encoded in REDIS_URL). Required
# on prod (#298): without it compose would fall back to the dev password.
REDIS_PASSWORD="$(ssm /fxvol/prod/REDIS_PASSWORD 2>/dev/null || echo "")"
if [ -z "${REDIS_PASSWORD}" ]; then
  echo "refusing to deploy: /fxvol/prod/REDIS_PASSWORD is not provisioned (the stack would run on the dev Redis password)" >&2
  exit 1
fi
# Grafana (obs profile only): no anonymous access and a provisioned admin
# password on prod, or the deploy stops before touching the stack (#298).
GRAFANA_ADMIN_PASSWORD=""
case ",${COMPOSE_PROFILES:-}," in
  *,obs,*)
    GRAFANA_ADMIN_PASSWORD="$(ssm /fxvol/prod/GRAFANA_ADMIN_PASSWORD 2>/dev/null || echo "")"
    if [ -z "${GRAFANA_ADMIN_PASSWORD}" ]; then
      echo "refusing to deploy: COMPOSE_PROFILES includes obs but /fxvol/prod/GRAFANA_ADMIN_PASSWORD is not provisioned" >&2
      exit 1
    fi
    ;;
esac
# api -> execution-engine shared secret (#300). SSM when provisioned; otherwise
# a host-generated token kept in /etc/fxvol (root 0600, outside every bind
# mount) so both containers agree across deploys and nothing ships unauthenticated.
EXECUTION_INTERNAL_TOKEN="$(ssm /fxvol/prod/EXECUTION_INTERNAL_TOKEN 2>/dev/null || echo "")"
if [ -z "${EXECUTION_INTERNAL_TOKEN}" ]; then
  install -d -m 0700 /etc/fxvol
  if [ ! -s /etc/fxvol/internal_token ]; then
    ( umask 077; openssl rand -hex 32 > /etc/fxvol/internal_token )
  fi
  EXECUTION_INTERNAL_TOKEN="$(cat /etc/fxvol/internal_token)"
fi

reg="ghcr.io/${OWNER}"

# --- render /opt/fxvol/.env (0600) ------------------------------------------
umask 077
cat > /opt/fxvol/.env <<ENVEOF
DB_PASSWORD=${DB_PASSWORD}
FRED_API_KEY=${FRED_API_KEY}
ENV=prod
TRADING_MODE=paper
# Gateway API mode: 'yes' rejects every order placement (IB error 321). Toggled
# by the repo variable READ_ONLY_API via deploy.yml; defaults to 'yes' (safe) when
# unset/empty. Set the repo var to 'no' to let the authed trader place paper orders.
READ_ONLY_API=${READ_ONLY_API:-yes}
COMPOSE_PROFILES=${COMPOSE_PROFILES:-}
NGINX_CONF_FILE=./infrastructure/nginx/nginx.conf
LETSENCRYPT_DIR=/etc/letsencrypt
CERTBOT_WWW_DIR=/var/www/certbot
# Backup freshness stamp written by infrastructure/ec2/postgres_backup.sh; the
# api reads it through the ./state:/state:ro mount (#343).
BACKUP_STATE_FILE=/state/postgres-backup.last
API_IMAGE=${reg}/fx-options-api:${IMAGE_TAG}
FRONTEND_IMAGE=${reg}/fx-options-frontend:${IMAGE_TAG}
MARKET_DATA_IMAGE=${reg}/fx-options-market-data:${IMAGE_TAG}
VOL_ENGINE_IMAGE=${reg}/fx-options-vol-engine:${IMAGE_TAG}
RISK_ENGINE_IMAGE=${reg}/fx-options-risk-engine:${IMAGE_TAG}
DB_WRITER_IMAGE=${reg}/fx-options-db-writer:${IMAGE_TAG}
EXECUTION_IMAGE=${reg}/fx-options-execution:${IMAGE_TAG}
ANALYTICS_ENGINE_IMAGE=${reg}/fx-options-analytics-engine:${IMAGE_TAG}
# Pinned, NOT :latest. gnzsnz installs the Gateway into /home/ibgateway/Jts,
# which we persist as a named volume (compose ib_gateway_jts). Docker only
# populates that volume from the image ONCE (when empty), so a floating :latest
# that bumps the Gateway build (e.g. 10.48->10.49) leaves the old jars in the
# volume: IBC then dies with "version X not installed: can't find jars folder"
# and the engines stall unhealthy. Bumping this pin is deliberate and MUST be
# paired with refreshing the volume's install: ensure_ib_gateway_build below does it (#415).
IB_GATEWAY_IMAGE=ghcr.io/gnzsnz/ib-gateway:10.51.1e@sha256:85975f493bcacc8341cb42e6946c460868d165e5f64d9924b1958c8f03e7bee4
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
  if [ -n "${AUTH_SALT}" ];          then echo "AUTH_SALT=${AUTH_SALT}"; fi
  echo "REDIS_PASSWORD=${REDIS_PASSWORD}"
  if [ -n "${GRAFANA_ADMIN_PASSWORD}" ]; then
    echo "GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}"
    echo "GF_AUTH_ANONYMOUS_ENABLED=false"
  fi
  echo "EXECUTION_INTERNAL_TOKEN=${EXECUTION_INTERNAL_TOKEN}"
  echo "AUTH_USERNAME=trader"
  echo "AUTH_COOKIE_SECURE=true"
} >> /opt/fxvol/.env

# --- host cron: keep the nightly backup wrapper current -----------------------
# setup.sh installs it once at first boot; re-installing here means a host set
# up before the wrapper existed, or a renamed script, still gets a working cron
# on the next deploy (#343, audit 08#9 "one-shot, never re-run by deploy").
mkdir -p /opt/fxvol/state
chmod 0755 /opt/fxvol/state
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-postgres-backup <<'CRON'
#!/bin/sh
# Managed by infrastructure/ec2/setup.sh + remote-deploy.sh — do not edit by hand.
exec /bin/sh /opt/fxvol/infrastructure/ec2/postgres_backup.sh >> /var/log/fxvol-postgres-backup.log 2>&1
CRON


# --- host hygiene + alerts (#342): logrotate, journald cap, disk guard -------
# Re-installed on every deploy, like the backup wrapper, so a host set up
# before these existed converges on the next deploy.
install -m 0644 /dev/stdin /etc/logrotate.d/fxvol <<'ROT'
/var/log/fxvol-*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
ROT
mkdir -p /etc/systemd/journald.conf.d
install -m 0644 /dev/stdin /etc/systemd/journald.conf.d/fxvol.conf <<'JRN'
[Journal]
SystemMaxUse=200M
MaxRetentionSec=14day
JRN
systemctl restart systemd-journald 2>/dev/null || true
# Weekly prune through the same script as the deploy (the old cron's
# `system prune -af --filter until=168h` removed the previous release's
# images once they were a week old, i.e. the rollback target, #345).
install -m 0755 /dev/stdin /etc/cron.weekly/fxvol-docker-prune <<'CRON'
#!/bin/sh
# Managed by infrastructure/ec2/setup.sh + remote-deploy.sh — do not edit by hand.
exec /bin/bash /opt/fxvol/infrastructure/ec2/prune_images.sh >> /var/log/fxvol-prune.log 2>&1
CRON
install -m 0755 /dev/stdin /etc/cron.hourly/fxvol-disk-check <<'CRON'
#!/bin/sh
# Managed by infrastructure/ec2/setup.sh + remote-deploy.sh — do not edit by hand.
exec /bin/sh /opt/fxvol/infrastructure/ec2/disk_check.sh >> /var/log/fxvol-disk-check.log 2>&1
CRON

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
  # Same script as the nightly cron, under postgres/pre-deploy/. It also
  # stamps /opt/fxvol/state/postgres-backup.last, so the api's backup-age
  # signal is fresh right after a deploy (#343).
  BACKUP_PREFIX=postgres/pre-deploy AWS_REGION="$REGION" \
    /bin/sh infrastructure/ec2/postgres_backup.sh "pre-${IMAGE_TAG}"
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
  # The DB URL goes through a 0600 env file, never '-e KEY=VALUE': an inline
  # variable sits in the container's command line for the whole migration and
  # shows up in 'ps' / 'pgrep -a' / SSM command logs, password included (#287).
  # umask 077 (set above) makes the file private; it is removed even on failure.
  mig_env="$(mktemp /tmp/fxvol-migrate.XXXXXX)"
  printf 'DATABASE_URL=postgresql+asyncpg://fxvol:%s@postgres:5432/fxvol\n' \
    "${DB_PASSWORD}" > "$mig_env"

  # Schema-aware rollback (#345, audit 08#6/08#7). If the database sits on a
  # revision the target image does not know (a rollback past a migration),
  # `upgrade head` with that image would fail on an unknown revision. The
  # running api image knows both, so it performs the downgrade to the target
  # image's head; only on explicit opt-in, after the pre-deploy dump above.
  alembic_in() {  # alembic_in <image> <args...>
    local image="$1"; shift
    docker run --rm --network "$net" --env-file "$mig_env" "$image" \
      python -m alembic -c src/persistence/alembic.ini "$@"
  }
  target_head="$(docker run --rm "${reg}/fx-options-api:${IMAGE_TAG}" \
    python -m alembic -c src/persistence/alembic.ini heads 2>/dev/null | awk 'NR==1{print $1}')"
  running_image="$(docker inspect fxvol-api --format '{{.Config.Image}}' 2>/dev/null || true)"
  db_rev=""
  if [ -n "$running_image" ]; then
    db_rev="$(alembic_in "$running_image" current 2>/dev/null | awk '/^[0-9A-Za-z_]+/{print $1; exit}')"
  fi
  if [ -n "$db_rev" ] && [ -n "$target_head" ] && [ "$db_rev" != "$target_head" ] \
     && ! docker run --rm "${reg}/fx-options-api:${IMAGE_TAG}" \
          python -m alembic -c src/persistence/alembic.ini show "$db_rev" >/dev/null 2>&1; then
    if [ "${ALLOW_SCHEMA_DOWNGRADE:-false}" != "true" ]; then
      rm -f "$mig_env"
      echo "remote-deploy: the database is at ${db_rev}, which ${IMAGE_TAG} does not know (its head is ${target_head})." >&2
      echo "remote-deploy: this is a rollback past a migration; re-run with allow_schema_downgrade=true to downgrade with ${running_image}" >&2
      exit 1
    fi
    echo "remote-deploy: schema downgrade ${db_rev} -> ${target_head} with ${running_image}"
    down_rc=0
    alembic_in "$running_image" downgrade "$target_head" || down_rc=$?
    if [ "$down_rc" -ne 0 ]; then
      rm -f "$mig_env"
      echo "remote-deploy: downgrade failed (exit ${down_rc}); the stack is untouched, restore point = pre-${IMAGE_TAG} dump" >&2
      exit "$down_rc"
    fi
  fi
  mig_rc=0
  docker run --rm --network "$net" \
    --env-file "$mig_env" \
    "${reg}/fx-options-api:${IMAGE_TAG}" \
    python -m alembic -c src/persistence/alembic.ini upgrade head || mig_rc=$?
  rm -f "$mig_env"
  if [ "$mig_rc" -ne 0 ]; then
    echo "remote-deploy: migration failed (exit ${mig_rc}), aborting before swap" >&2
    exit "$mig_rc"
  fi
fi

# The persisted ib_gateway_jts volume must hold the pinned Gateway build before
# the container starts (#415). Docker seeds a named volume from the image only
# once, so after a pin bump the volume still has the old jars and IBC dies with
# "version X not installed" (prod, 2026-07-31). Copy the new build in next to
# the kept settings, after a tarball backup; no-op when it is already there or
# when the volume does not exist yet (first boot seeds it from the image).
ensure_ib_gateway_build() {
  case ",${COMPOSE_PROFILES:-}," in *,ib,*) ;; *) return 0 ;; esac
  local img vol build
  img="$(sed -n 's/^IB_GATEWAY_IMAGE=//p' .env | tail -1)"
  vol="$(docker volume ls -q | grep -E '(^|_)ib_gateway_jts$' | head -1 || true)"
  [ -n "$img" ] && [ -n "$vol" ] || return 0
  docker pull -q "$img" >/dev/null
  build="$(docker run --rm --entrypoint sh "$img" -c 'ls /home/ibgateway/Jts/ibgateway' | tail -1)"
  [ -n "$build" ] || { echo "remote-deploy: no Gateway build found in ${img}" >&2; return 1; }
  if docker run --rm -u root -v "${vol}:/vol:ro" --entrypoint sh "$img" -c "test -d /vol/ibgateway/${build}/jars"; then
    return 0
  fi
  echo "remote-deploy: ib_gateway_jts lacks Gateway ${build}; backing up its settings and copying the build in"
  mkdir -p /var/backups/fxvol
  docker stop fxvol-ib-gateway >/dev/null 2>&1 || true
  # Settings only (API / Trusted-IPs config, jts.ini): the installs come from
  # images. Older builds stay in the volume (~520 MB each) so a rollback to the
  # previous pin still boots; prune them by hand once a bump has settled.
  docker run --rm -u root -v "${vol}:/vol:ro" -v /var/backups/fxvol:/b --entrypoint sh "$img" \
    -c "tar czf /b/ibgw_jts_settings_$(date -u +%Y%m%dT%H%M%SZ).tgz --exclude=./ibgateway -C /vol ."
  docker run --rm -u root -v "${vol}:/vol" --entrypoint sh "$img" \
    -c "mkdir -p /vol/ibgateway && tar -C /home/ibgateway/Jts/ibgateway -cf - ${build} | tar -C /vol/ibgateway -xpf -"
}
ensure_ib_gateway_build

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

# --- read-only analysis role (migration 064, docs/ops/runbooks.md section 5) --------
# LOGIN only while /fxvol/prod/DB_READONLY_PASSWORD exists; deleting the
# parameter and redeploying closes the access. The password goes to Postgres as
# a SCRAM verifier over stdin: never in clear on the server, never in argv.
# Best-effort: this must never fail an otherwise-good deploy.
set_reader_login() {
  local ro_pw verifier
  ro_pw="$(ssm /fxvol/prod/DB_READONLY_PASSWORD 2>/dev/null || echo "")"
  if [ -n "$ro_pw" ]; then
    verifier="$(printf '%s' "$ro_pw" | python3 infrastructure/ec2/scram_verifier.py)" || return 1
    printf "ALTER ROLE fxvol_reader WITH LOGIN PASSWORD '%s';
" "$verifier"       | docker compose exec -T postgres psql -U fxvol -d fxvol -q -v ON_ERROR_STOP=1 >/dev/null || return 1
    echo "remote-deploy: fxvol_reader login enabled"
  else
    echo "ALTER ROLE fxvol_reader WITH NOLOGIN PASSWORD NULL;"       | docker compose exec -T postgres psql -U fxvol -d fxvol -q -v ON_ERROR_STOP=1 >/dev/null || return 1
    echo "remote-deploy: fxvol_reader login disabled (no DB_READONLY_PASSWORD in SSM)"
  fi
}
set_reader_login || echo "remote-deploy: could not set the fxvol_reader login (non-fatal)" >&2

# Reclaim disk: drop the older deploys' sha-<commit> tags, but keep the
# previous release's images so a rollback needs no pull (#345). Each deploy pulls 7 fresh images,
# so without this the 29G root fills up after ~a dozen deploys; once it does,
# 'docker compose pull' stalls mid-transfer and the SSM document-worker times out
# ("ipc messaging received timeout signal") with an empty error — the whole
# deploy fails for a reason that looks nothing like "disk full". Runs only after
# the new stack is up (so its images are protected). Best-effort: a prune failure
# must never fail an otherwise-good deploy.
# Record the release pair first: the prune keeps both tags (#345).
prev_tag="$(cat /opt/fxvol/state/current_tag 2>/dev/null || true)"
if [ -n "$prev_tag" ] && [ "$prev_tag" != "$IMAGE_TAG" ]; then
  echo "$prev_tag" > /opt/fxvol/state/previous_tag
fi
echo "$IMAGE_TAG" > /opt/fxvol/state/current_tag
bash /opt/fxvol/infrastructure/ec2/prune_images.sh || echo "remote-deploy: image prune skipped"

# Regenerate the on-server command folder (/opt/fxvol/cmd) so a connected user
# can `cd /opt/fxvol/cmd; ls; ./all/ps; ./containers/api`. Best-effort.
bash /opt/fxvol/infrastructure/ec2/gen-commands.sh || echo "remote-deploy: gen-commands skipped"

echo "remote-deploy: done (tag ${IMAGE_TAG}, profiles '${COMPOSE_PROFILES:-core}')"
