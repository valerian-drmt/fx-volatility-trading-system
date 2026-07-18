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

echo "[setup] docker daemon: json-file log rotation (unbounded logs fill the root disk)"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON

systemctl enable --now docker
systemctl restart docker

echo "[setup] 2G swapfile (OOM shock absorber for t3.small)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "[setup] weekly docker prune (keeps <7d images: previous SHA survives for rollback)"
install -m 0755 /dev/stdin /etc/cron.weekly/fxvol-docker-prune <<'CRON'
#!/bin/sh
docker system prune -af --filter "until=168h"
CRON

echo "[setup] create $APP_USER user and $APP_DIR"
id -u "$APP_USER" > /dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
usermod -aG docker "$APP_USER"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[setup] ufw : allow 80 + 443 only (admin is SSM-only, port 22 stays closed)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "[setup] systemd unit for docker compose stack"
# Prefer the versioned unit (present once a deploy payload has landed); the
# heredoc fallback below MUST stay byte-identical to
# infrastructure/ec2/fxvol-compose.service — EnvironmentFile included, or a
# boot-time compose up misses COMPOSE_PROFILES/NGINX_CONF_FILE and brings up
# the wrong stack shape.
if [ -f "$APP_DIR/infrastructure/ec2/fxvol-compose.service" ]; then
    install -m 0644 "$APP_DIR/infrastructure/ec2/fxvol-compose.service" \
        /etc/systemd/system/fxvol-compose.service
else
    cat > /etc/systemd/system/fxvol-compose.service <<'UNIT'
[Unit]
Description=FX Vol stack (docker compose)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/fxvol
EnvironmentFile=/opt/fxvol/.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down --remove-orphans

[Install]
WantedBy=multi-user.target
UNIT
fi
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

echo "[setup] nightly Postgres backup to S3 (instance role, SSE-S3)"
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-postgres-backup <<'CRON'
#!/bin/sh
set -eu
# Credentials: EC2 instance role (s3:PutObject on fxvol-backups/*). No env
# file, no static keys. Restore procedure: infrastructure/ec2/RESTORE.md.
ts=$(date -u +%Y%m%dT%H%M%SZ)
tmp="/tmp/fxvol-$ts.dump"
docker compose -f /opt/fxvol/docker-compose.yml exec -T postgres \
    pg_dump -U fxvol -Fc --if-exists --clean fxvol > "$tmp"
# Refuse to upload an implausibly small dump (schema-only is tens of KB).
[ "$(wc -c < "$tmp")" -gt 10240 ] || { echo "dump too small, aborting" >&2; rm -f "$tmp"; exit 1; }
aws s3 cp "$tmp" "s3://fxvol-backups/postgres/fxvol-$ts.dump" --sse AES256 --region eu-west-1
rm -f "$tmp"
CRON

echo "[setup] done. Next steps :"
echo "  1. Render $APP_DIR/.env (done by deploy.yml, or scp manually: DB_PASSWORD,"
echo "     IB creds, image tags, NGINX_CONF_FILE=./infrastructure/nginx/nginx.conf)."
echo "  2. Bootstrap the TLS cert (containerised Nginx, one-shot) :"
echo "       DOMAIN=valeriandarmente.dev EMAIL=you@example.com \\"
echo "         bash $APP_DIR/infrastructure/ec2/init-letsencrypt.sh"
echo "     (NOT 'certbot --nginx' — Nginx runs in a container, not on the host.)"
echo "  3. systemctl start fxvol-compose.service   # or: cd $APP_DIR && docker compose up -d"
