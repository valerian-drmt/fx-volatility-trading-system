#!/usr/bin/env bash
# Bring the full v2 stack up (postgres + redis + api + frontend + nginx + ib-gateway)
# and apply Alembic migrations once postgres is healthy.
#
# Usage:
#   ./scripts/up.sh           # pull/build + up -d + migrate
#   ./scripts/up.sh --pull    # force pull of remote images before up
#
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    # Export every line of .env that looks like KEY=value.
    set -a
    # shellcheck disable=SC1091
    . .env
    set +a
fi

: "${DB_PASSWORD:?DB_PASSWORD is required — copy .env.example to .env and fill it in}"
: "${VNC_PASSWORD:?VNC_PASSWORD is required — see .env.example}"

if [ "${1:-}" = "--pull" ]; then
    docker compose pull
fi

echo "[up.sh] Starting compose stack …"
docker compose up -d --build

echo "[up.sh] Waiting for postgres to become healthy …"
for _ in $(seq 1 30); do
    state=$(docker inspect -f '{{.State.Health.Status}}' fxvol-postgres 2>/dev/null || echo "starting")
    if [ "$state" = "healthy" ]; then
        break
    fi
    sleep 2
done

echo "[up.sh] Applying Alembic migrations …"
docker compose exec -T api python -m alembic -c persistence/alembic.ini upgrade head

echo "[up.sh] Stack is up — http://localhost/"
docker compose ps
