#!/usr/bin/env bash
# Launch the FastAPI backend for local development.
#   - auto-reload on code change (single worker ; --workers is incompatible
#     with --reload, so we don't add it here)
#   - PYTHONPATH=src is set here so `from persistence...` imports resolve
#   - Reads DATABASE_URL / REDIS_URL from env (defaults localhost :5433 / :6380)
#
# Usage :
#   bash scripts/run_api.sh
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-src}"
exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
