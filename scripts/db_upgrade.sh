#!/usr/bin/env bash
# Apply all pending Alembic migrations up to the latest revision.
#
# Usage:
#     export DATABASE_URL=postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol
#     bash scripts/db_upgrade.sh
#
# Equivalent to:
#     alembic -c persistence/alembic.ini upgrade head
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. See .env.example." >&2
  exit 1
fi

exec python -m alembic -c persistence/alembic.ini upgrade head
