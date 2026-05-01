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
    ca-certificates curl gnupg git ufw cron jq \
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
ExecStartPre=/opt/fxvol/scripts/ops/load_secrets.sh
EnvironmentFile=/run/fxvol.env
EnvironmentFile=-/opt/fxvol/images.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down --remove-orphans
ExecStopPost=/bin/rm -f /run/fxvol.env

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable fxvol-compose.service

echo "[setup] Let's Encrypt renewal cron (certbot renew --deploy-hook reloads Nginx)"
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-certbot-renew <<'CRON'
#!/bin/sh
certbot renew --quiet --deploy-hook "docker compose -f /opt/fxvol/docker-compose.yml exec nginx nginx -s reload"
CRON

echo "[setup] nightly Postgres backup to S3 (SSE-S3)"
install -m 0755 /dev/stdin /etc/cron.daily/fxvol-postgres-backup <<'CRON'
#!/bin/sh
set -eu
# Auth via the EC2 instance profile (IAM role fxvol-ec2-secrets-role
# extended with s3:PutObject on the backup bucket). No static AWS keys
# on disk. BACKUP_BUCKET is the only config read from /etc/fxvol/backup.conf.
. /etc/fxvol/backup.conf
AWS_REGION=$(curl -fsS -m 2 http://169.254.169.254/latest/meta-data/placement/region)
export AWS_REGION
ts=$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f /opt/fxvol/docker-compose.yml exec -T postgres \
    pg_dump -U fxvol -Fc fxvol > "/tmp/fxvol-$ts.dump"
aws s3 cp "/tmp/fxvol-$ts.dump" "s3://${BACKUP_BUCKET}/postgres/fxvol-$ts.dump" \
    --sse AES256
rm -f "/tmp/fxvol-$ts.dump"
CRON

echo "[setup] done. Next steps :"
echo "  1. Attach the IAM instance profile 'fxvol-ec2-instance-profile' to this EC2"
echo "     (required: ssm:GetParameters + kms:Decrypt on /fxvol/prod/* and the CMK)."
echo "     Verify: aws sts get-caller-identity should return the role ARN, not a user."
echo "  2. git clone the repo into $APP_DIR and ensure scripts/ops/load_secrets.sh is +x."
echo "  3. echo 'BACKUP_BUCKET=<your-bucket>' > /etc/fxvol/backup.conf  (for the daily backup)."
echo "  4. certbot --nginx -d <your-domain>"
echo "  5. systemctl start fxvol-compose.service  (runs ExecStartPre=load_secrets.sh first)"
