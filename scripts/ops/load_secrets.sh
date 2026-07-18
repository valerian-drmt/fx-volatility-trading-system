#!/usr/bin/env bash
# Fetch fx-vol secrets from AWS SSM Parameter Store (/fxvol/prod/*) and
# render them as KEY=VALUE lines into an EnvironmentFile consumed by
# systemd. Runs on EC2 via the instance IAM role -- no static AWS keys.
#
# The output lives on tmpfs (/run) and is wiped on reboot + by
# ExecStopPost of the unit. File owner root:fxvol, mode 0640 (group-read
# so the fxvol user's docker compose can source it via systemd).
#
# Env var overrides (for tests) :
#   AWS_REGION          region AWS (default eu-west-1)
#   FXVOL_ENV_OUT       output path (default /run/fxvol.env)
#   FXVOL_SKIP_CHOWN    1 to skip chown/chmod (for non-root tests)

set -euo pipefail

REGION="${AWS_REGION:-eu-west-1}"
OUT="${FXVOL_ENV_OUT:-/run/fxvol.env}"
SKIP_CHOWN="${FXVOL_SKIP_CHOWN:-0}"
TMP="${OUT}.new"

umask 077

# IAM policy only grants access to leaves parameter/fxvol/prod/* (not the
# path itself), so list the params explicitly -- same reason as Windows
# commit db11448f.
names=(
    /fxvol/prod/IB_USERID
    /fxvol/prod/IB_PASSWORD
    /fxvol/prod/DB_PASSWORD
    /fxvol/prod/VNC_PASSWORD
    /fxvol/prod/TRADING_MODE
    /fxvol/prod/AUTH_SECRET
    /fxvol/prod/AUTH_PASSWORD_HASH
)

aws ssm get-parameters \
    --names "${names[@]}" \
    --with-decryption \
    --region "$REGION" \
    --output json \
  | jq -r '
      if (.InvalidParameters | length) > 0 then
        ("missing SSM params: " + (.InvalidParameters | join(","))) | halt_error(1)
      else empty end,
      .Parameters[] | "\(.Name | sub("^/fxvol/prod/"; ""))=\(.Value)"
    ' > "$TMP"

# Derived vars (non-secret, depend on DB_PASSWORD fetched above). In
# prod the containers talk to each other via docker networks, so the
# hosts are the service names (not localhost).
db_password=$(grep '^DB_PASSWORD=' "$TMP" | cut -d= -f2-)
{
    echo "DATABASE_URL=postgresql+asyncpg://fxvol:${db_password}@postgres:5432/fxvol"
    echo "REDIS_URL=redis://redis:6379/0"
    # Prod posture: arms the api boot guard (fail-fast on default AUTH_SECRET)
    # and forces the Secure flag on the session cookie.
    echo "ENV=prod"
    echo "AUTH_COOKIE_SECURE=true"
} >> "$TMP"

mv "$TMP" "$OUT"

if [[ "$SKIP_CHOWN" != "1" ]]; then
    chown root:fxvol "$OUT"
    chmod 0640 "$OUT"
fi

count=$(wc -l < "$OUT")
echo "[load_secrets] wrote $OUT ($count lines)"
