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

echo "[setup] docker daemon: json-file log rotation + classic overlay2 image store"
mkdir -p /etc/docker
# containerd-snapshotter=false pins the CLASSIC overlay2 image store. Recent
# Docker defaults to the containerd image store (storage driver "overlayfs"),
# whose on-disk layout cAdvisor v0.49.x can't resolve per-container ("failed to
# identify the read-write layer ID …") — under --docker_only that fails container
# registration, so the dev Hardware tab gets no per-container CPU/RAM series.
# overlay2 keeps the layout cAdvisor understands.
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" }, "features": { "containerd-snapshotter": false } }
JSON

systemctl enable --now docker
systemctl restart docker

echo "[setup] 2G swapfile (OOM shock absorber for t3.small)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "[setup] weekly docker prune (keeps the current AND previous release's images, #345)"
install -m 0755 /dev/stdin /etc/cron.weekly/fxvol-docker-prune <<'CRON'
#!/bin/sh
exec /bin/bash /opt/fxvol/infrastructure/ec2/prune_images.sh >> /var/log/fxvol-prune.log 2>&1
CRON

echo "[setup] unattended security upgrades, no automatic reboot (#345, audit 08#16)"
# Security pocket only, Docker excluded (its upgrade restarts the stack). A
# pending kernel reboot shows as /var/run/reboot-required and is taken in the
# weekend window of docs/ops/runbooks.md ("Host reboot"), never at random.
apt-get install -y --no-install-recommends unattended-upgrades
install -m 0644 /dev/stdin /etc/apt/apt.conf.d/52fxvol-unattended <<'APT'
Unattended-Upgrade::Allowed-Origins {
        "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Package-Blacklist {
        "docker-ce";
        "docker-ce-cli";
        "containerd.io";
};
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
APT
install -m 0644 /dev/stdin /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT

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
# 'exec -T' is required: cron has no TTY, and without it docker compose fails
# with "the input device is not a TTY". That failure is silent from the outside
# — the cert renews on disk while the running container keeps serving the old
# one until it expires.
certbot renew --quiet --deploy-hook "docker compose -f /opt/fxvol/docker-compose.yml exec -T nginx nginx -s reload"
CRON

echo "[setup] nightly Postgres backup to S3 (instance role, SSE-S3)"
# Thin wrapper: the real script ships in the deploy payload
# ($APP_DIR/infrastructure/ec2/postgres_backup.sh) and remote-deploy.sh
# re-installs this wrapper on every deploy, so the backup logic is versioned
# with the stack instead of frozen at first boot (#343). /bin/sh, not exec:
# the tarball payload does not always preserve the execute bit.
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-postgres-backup <<CRON
#!/bin/sh
# Managed by infrastructure/ec2/setup.sh + remote-deploy.sh — do not edit by hand.
exec /bin/sh $APP_DIR/infrastructure/ec2/postgres_backup.sh >> /var/log/fxvol-postgres-backup.log 2>&1
CRON

echo "[setup] logrotate + journald cap + hourly disk guard (#342)"
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
install -m 0755 /dev/stdin /etc/cron.hourly/fxvol-disk-check <<'CRON'
#!/bin/sh
# Managed by infrastructure/ec2/setup.sh + remote-deploy.sh — do not edit by hand.
exec /bin/sh /opt/fxvol/infrastructure/ec2/disk_check.sh >> /var/log/fxvol-disk-check.log 2>&1
CRON

echo "[setup] IB gateway nightly-reset watchdog (cron.d, every 2 min)"
# The gateway's 23:59 IBC auto-restart can drop the IBKR upstream while the
# socat port stays up; the engines then hang unhealthy and Docker won't restart
# an *unhealthy* (only an exited) container. ib_watchdog.sh restarts gateway +
# engines when either engine is unhealthy. Ships in the deploy payload; the cron
# tolerates the script being briefly absent before the first deploy lands it.
install -m 0644 /dev/stdin /etc/cron.d/fxvol-ib-watchdog <<CRON
# Managed by infrastructure/ec2/setup.sh — do not edit by hand.
# Invoked via /bin/sh (not directly) so a lost execute bit on the deployed
# script — the tarball payload does not always preserve mode 0755 — cannot
# silently break the auto-heal with "flock: Permission denied".
*/2 * * * * root flock -n /run/fxvol-ib-watchdog.lock /bin/sh $APP_DIR/infrastructure/ec2/ib_watchdog.sh >> /var/log/fxvol-ib-watchdog.log 2>&1
CRON

echo "[setup] done. Next steps :"
echo "  1. Render $APP_DIR/.env (done by deploy.yml, or scp manually: DB_PASSWORD,"
echo "     IB creds, image tags, NGINX_CONF_FILE=./infrastructure/nginx/nginx.conf)."
echo "  2. Bootstrap the TLS cert (containerised Nginx, one-shot) :"
echo "       DOMAIN=valeriandarmente.dev EMAIL=you@example.com \\"
echo "         bash $APP_DIR/infrastructure/ec2/init-letsencrypt.sh"
echo "     (NOT 'certbot --nginx' — Nginx runs in a container, not on the host.)"
echo "  3. systemctl start fxvol-compose.service   # or: cd $APP_DIR && docker compose up -d"
