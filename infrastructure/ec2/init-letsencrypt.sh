#!/usr/bin/env bash
# One-shot Let's Encrypt bootstrap for the containerised Nginx.
#
# The prod nginx.conf listens on :443 with ssl_certificate pointing at
# /etc/letsencrypt/live/<domain>/ — so Nginx can't start before a cert
# exists, but certbot --webroot needs Nginx serving the ACME challenge on
# :80. We break the deadlock with a throwaway self-signed cert, bring the
# stack up, then replace it with the real cert via webroot.
#
# Run ONCE on the EC2 host (as the fxvol user, port 80/443 reachable):
#   DOMAIN=valeriandarmente.dev EMAIL=valeriandarmente@gmail.com \
#     bash infrastructure/ec2/init-letsencrypt.sh
# Renewals afterwards are handled by the cron installed by setup.sh.

set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN, e.g. valeriandarmente.dev}"
EMAIL="${EMAIL:?set EMAIL for ACME / certificate expiry notices}"
APP_DIR="${APP_DIR:-/opt/fxvol}"
LE_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"
WWW_DIR="${CERTBOT_WWW_DIR:-/var/www/certbot}"
LIVE="$LE_DIR/live/$DOMAIN"

sudo mkdir -p "$LIVE" "$WWW_DIR"

if [ ! -f "$LIVE/fullchain.pem" ]; then
  echo "[init-le] no cert yet → writing a 1-day self-signed placeholder so Nginx can boot"
  sudo openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "$LIVE/privkey.pem" -out "$LIVE/fullchain.pem" -subj "/CN=$DOMAIN"
fi

echo "[init-le] starting the stack (Nginx now serves :80 ACME challenge + :443 placeholder)"
( cd "$APP_DIR" && docker compose up -d )

echo "[init-le] requesting the real certificate via webroot"
sudo certbot certonly --webroot -w "$WWW_DIR" -d "$DOMAIN" \
  --email "$EMAIL" --agree-tos --no-eff-email --force-renewal --non-interactive

echo "[init-le] reloading Nginx with the real cert"
( cd "$APP_DIR" && docker compose exec nginx nginx -s reload )

echo "[init-le] done — https://$DOMAIN should now serve a valid certificate."
echo "          Renewals run via the daily cron installed by setup.sh."
