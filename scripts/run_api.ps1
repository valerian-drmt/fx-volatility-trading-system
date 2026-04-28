# PowerShell wrapper — launch the FastAPI backend for local dev on Windows.
#   - auto-reload on code change (single worker ; --workers is incompatible
#     with --reload)
#   - PYTHONPATH=src is set here so `from persistence...` imports resolve
#   - Reads DATABASE_URL / REDIS_URL from env (defaults localhost :5433 / :6380)
#
# Usage :
#   .\scripts\run_api.ps1
$env:PYTHONPATH = "src"
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
