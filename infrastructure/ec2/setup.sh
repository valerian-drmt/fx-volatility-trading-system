#!/usr/bin/env bash
# One-shot provisioning of a fresh Ubuntu 22.04 EC2 host.
#
# Usage (on the EC2 host as root) :
#   curl -fsS https://raw.githubusercontent.com/<owner>/<repo>/main/infrastructure/ec2/setup.sh | bash
#
# Idempotent : re-running is safe, every step short-circuits if the
# target is already in the expected state.

set -euo pipefail

APP_DIR=/opt/fxvol
APP_USER=fxvol

echo "[setup] apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git ufw cron \
    python3 python3-pip awscli certbot

echo "[setup] install docker + compose v2 if missing"
if ! command -v docker > /dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $codename stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "[setup] create $APP_USER user and $APP_DIR"
id -u "$APP_USER" > /dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
usermod -aG docker "$APP_USER"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[setup] ufw : allow 22 + 80 + 443, drop the rest"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "[setup] systemd unit for docker compose stack"
cat > /etc/systemd/system/fxvol-compose.service <<'UNIT'
[Unit]
Description=FX Vol stack (docker compose)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/fxvol
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down --remove-orphans

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable fxvol-compose.service

echo "[setup] ACME webroot dir (host certbot writes challenges here; Nginx serves them)"
mkdir -p /var/www/certbot

echo "[setup] Let's Encrypt renewal cron (certbot renew --deploy-hook reloads Nginx)"
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-certbot-renew <<'CRON'
#!/bin/sh
# Webroot renewal (Nginx serves /var/www/certbot on :80); reload the container.
certbot renew --quiet --deploy-hook "docker compose -f /opt/fxvol/docker-compose.yml exec nginx nginx -s reload"
CRON

echo "[setup] nightly Postgres backup to S3 (SSE-S3)"
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-postgres-backup <<'CRON'
#!/bin/sh
set -eu
# Expect AWS_REGION / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / BACKUP_BUCKET
# to be sourced from /etc/fxvol/backup.env (provisioned out of band).
. /etc/fxvol/backup.env
ts=$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f /opt/fxvol/docker-compose.yml exec -T postgres \
    pg_dump -U fxvol -Fc fxvol > "/tmp/fxvol-$ts.dump"
aws s3 cp "/tmp/fxvol-$ts.dump" "s3://${BACKUP_BUCKET}/postgres/fxvol-$ts.dump" \
    --sse AES256
rm -f "/tmp/fxvol-$ts.dump"
CRON

echo "[setup] done. Next steps :"
echo "  1. Render $APP_DIR/.env (done by deploy.yml, or scp manually: DB_PASSWORD,"
echo "     IB creds, image tags, NGINX_CONF_FILE=./infrastructure/nginx/nginx.conf)."
echo "  2. Bootstrap the TLS cert (containerised Nginx, one-shot) :"
echo "       DOMAIN=valeriandarmente.dev EMAIL=you@example.com \\"
echo "         bash $APP_DIR/infrastructure/ec2/init-letsencrypt.sh"
echo "     (NOT 'certbot --nginx' — Nginx runs in a container, not on the host.)"
echo "  3. systemctl start fxvol-compose.service   # or: cd $APP_DIR && docker compose up -d"
