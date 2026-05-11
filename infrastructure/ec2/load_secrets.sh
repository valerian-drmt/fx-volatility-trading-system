#!/usr/bin/env bash
# Pulls runtime secrets from AWS Secrets Manager into /opt/fxvol/.env.
#
# Called by a one-off post-provisioning step : the CI deploy workflow
# also renders .env over SSH using repo secrets, so this script is only
# for the "no GitHub Actions" path (bare provisioning, manual rollback).
#
# Secret layout in AWS Secrets Manager :
#   fxvol/prod  → JSON map with DB_PASSWORD / VNC_PASSWORD / IB_USERID /
#                 IB_PASSWORD / image tag (key: RELEASE_SHA)

set -euo pipefail

: "${AWS_REGION:?AWS_REGION env var is required (eu-west-3 etc.)}"
SECRET_ID="${SECRET_ID:-fxvol/prod}"
TARGET="${TARGET:-/opt/fxvol/.env}"
TMP=$(mktemp)

trap 'rm -f "$TMP"' EXIT

aws secretsmanager get-secret-value \
    --region "$AWS_REGION" \
    --secret-id "$SECRET_ID" \
    --query SecretString \
    --output text > "$TMP"

# Flatten the JSON map to KEY=value lines. jq is assumed installed
# (setup.sh apt-installed jq? no — add to setup.sh if missing).
python3 - "$TMP" <<'PY' > "$TARGET.new"
import json, sys
with open(sys.argv[1]) as f:
    doc = json.load(f)
for key, value in doc.items():
    # Reject values with embedded newlines — they'd break .env parsing.
    if "\n" in str(value):
        sys.exit(f"secret {key} contains a newline ; refuse to write")
    print(f"{key}={value}")
PY

umask 077
mv "$TARGET.new" "$TARGET"
chmod 600 "$TARGET"

echo "[load_secrets] wrote $TARGET from AWS SM secret $SECRET_ID"
