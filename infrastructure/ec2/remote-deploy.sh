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
set -euo pipefail

cd /opt/fxvol

REGION="${AWS_REGION:-eu-west-1}"
ssm() { aws ssm get-parameter --name "$1" --with-decryption --query Parameter.Value --output text --region "$REGION"; }

# --- secrets, straight from SSM (never logged, never in GitHub) -------------
DB_PASSWORD="$(ssm /fxvol/prod/DB_PASSWORD)"
VNC_PASSWORD="$(ssm /fxvol/prod/VNC_PASSWORD)"
IB_USERID="$(ssm /fxvol/prod/IB_USERID)"
IB_PASSWORD="$(ssm /fxvol/prod/IB_PASSWORD)"
# Optional params : tolerate absence (set -e) so a missing SSM entry never
# aborts the deploy. FRED_API_KEY is unused by the current compose; GHCR_TOKEN
# is only needed if the GHCR packages are private (login skipped below if empty).
FRED_API_KEY="$(ssm /fxvol/prod/FRED_API_KEY 2>/dev/null || echo "")"
GHCR_TOKEN="$(ssm /fxvol/prod/GHCR_TOKEN 2>/dev/null || echo "")"

reg="ghcr.io/${OWNER}"

# --- render /opt/fxvol/.env (0600) ------------------------------------------
umask 077
cat > /opt/fxvol/.env <<ENVEOF
DB_PASSWORD=${DB_PASSWORD}
VNC_PASSWORD=${VNC_PASSWORD}
IB_USERID=${IB_USERID}
IB_PASSWORD=${IB_PASSWORD}
FRED_API_KEY=${FRED_API_KEY}
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
IB_GATEWAY_IMAGE=ghcr.io/gnzsnz/ib-gateway:latest
ENVEOF

# --- pull + restart ---------------------------------------------------------
if [ -n "${GHCR_TOKEN}" ]; then
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${OWNER}" --password-stdin
fi
docker compose pull
docker compose up -d --remove-orphans
docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head

echo "remote-deploy: done (tag ${IMAGE_TAG}, profiles '${COMPOSE_PROFILES:-core}')"
