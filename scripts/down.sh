#!/usr/bin/env bash
# Graceful stop of the v2 stack. Keeps volumes by default so the DB data
# survives — pass `--volumes` to drop everything.
#
# Usage:
#   ./scripts/down.sh               # stop + remove containers, keep data
#   ./scripts/down.sh --volumes     # also drop postgres_data, redis_data
#
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--volumes" ]; then
    echo "[down.sh] Stopping stack AND dropping volumes …"
    docker compose down --volumes --remove-orphans
else
    echo "[down.sh] Stopping stack (volumes preserved) …"
    docker compose down --remove-orphans
fi
