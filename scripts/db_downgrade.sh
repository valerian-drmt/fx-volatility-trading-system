#!/usr/bin/env bash
# Roll back the last Alembic migration (dev only, do NOT run in prod).
#
# Usage:
#     export DATABASE_URL=postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol
#     bash scripts/db_downgrade.sh
#
# Equivalent to:
#     alembic -c persistence/alembic.ini downgrade -1
#
# To reset completely: alembic -c persistence/alembic.ini downgrade base
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. See .env.example." >&2
  exit 1
fi

if [[ "${DATABASE_URL}" != *localhost* ]] && [[ "${DATABASE_URL}" != *127.0.0.1* ]]; then
  echo "ERROR: refusing to downgrade a non-localhost database." >&2
  exit 1
fi

exec python -m alembic -c persistence/alembic.ini downgrade -1
