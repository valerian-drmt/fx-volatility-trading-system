# Graceful stop of the v2 stack. Mirrors scripts/down.sh.
param(
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Volumes) {
    Write-Host "[down.ps1] Stopping stack AND dropping volumes ..." -ForegroundColor Yellow
    docker compose down --volumes --remove-orphans
} else {
    Write-Host "[down.ps1] Stopping stack (volumes preserved) ..." -ForegroundColor Cyan
    docker compose down --remove-orphans
}
