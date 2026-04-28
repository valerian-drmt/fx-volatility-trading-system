#!/usr/bin/env bash
# Launch the FastAPI backend for local development.
#   - auto-reload on code change
#   - 4 uvicorn workers
#   - expects PYTHONPATH=src (for persistence imports) and .env with
#     DATABASE_URL / REDIS_URL (defaults to localhost :5433 / :6380)
#
# Usage :
#   $env:PYTHONPATH = "src"
#   bash scripts/run_api.sh
set -euo pipefail
exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4 --reload
