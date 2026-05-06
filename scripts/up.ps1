# Bring the full v2 stack up on Windows PowerShell. Mirrors scripts/up.sh.
param(
    [switch]$Pull
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') {
            $name = $matches[1]
            $value = $matches[2].Trim('"').Trim("'")
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

if (-not $env:DB_PASSWORD) { throw "DB_PASSWORD is required (copy .env.example to .env)" }
if (-not $env:VNC_PASSWORD) { throw "VNC_PASSWORD is required (see .env.example)" }

if ($Pull) { docker compose pull }

Write-Host "[up.ps1] Starting compose stack ..." -ForegroundColor Cyan
docker compose up -d --build

Write-Host "[up.ps1] Waiting for postgres to become healthy ..." -ForegroundColor Cyan
for ($i = 0; $i -lt 30; $i++) {
    $state = docker inspect -f '{{.State.Health.Status}}' fxvol-postgres 2>$null
    if ($state -eq "healthy") { break }
    Start-Sleep -Seconds 2
}

Write-Host "[up.ps1] Applying Alembic migrations ..." -ForegroundColor Cyan
docker compose exec -T api python -m alembic -c persistence/alembic.ini upgrade head

Write-Host "[up.ps1] Stack is up — http://localhost/" -ForegroundColor Green
docker compose ps
