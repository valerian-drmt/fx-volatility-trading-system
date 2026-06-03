<#
.SYNOPSIS
  Commande UNIQUE pour orchestrer la stack fx-vol locale (Windows / PowerShell).

.DESCRIPTION
  Une seule entree pour tout faire :
    1. Verifier les prereqs (docker, aws, python)
    2. git pull (sauf -NoPull)
    3. Creer le .venv si absent + pip install
    4. Charger les secrets depuis AWS SSM
    5. docker compose up -d --build (sauf -NoBuild) sur TOUS les profils
       (engines + ib + obs) pour batir tous les containers du projet
       (api, frontend, nginx, 5 engines, ib-gateway, prometheus, loki,
        promtail, tempo, otel-collector, grafana).
    6. Attendre Postgres healthy
    7. Alembic upgrade head
    8. Restart nginx (refresh DNS upstreams)

  Sous-mode -Down arrete tout (et droppe les volumes si -DropVolumes).

.PARAMETER Down
  Arrete la stack et sort (au lieu du pipeline up).

.PARAMETER NoPull
  Skip le git pull.

.PARAMETER NoBuild
  Skip le --build (plus rapide si aucune image n'a change).

.PARAMETER DropVolumes
  Avec -Down : docker compose down --volumes (drop postgres data + redis cache).

.PARAMETER RecreateVenv
  Force la recreation du .venv (sinon reutilise l'existant).

.EXAMPLE
  .\scripts\start_stack.ps1                 # full pipeline up
  .\scripts\start_stack.ps1 -NoPull -NoBuild  # demarrage rapide
  .\scripts\start_stack.ps1 -Down           # stop
  .\scripts\start_stack.ps1 -Down -DropVolumes  # stop + wipe data
#>
param(
    [switch]$Down,
    [switch]$NoPull,
    [switch]$NoBuild,
    [switch]$DropVolumes,
    [switch]$RecreateVenv
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectDir

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

try {
    # ---------- Sous-mode -Down : stop & exit ----------
    if ($Down) {
        Write-Step "Stopping stack$(if ($DropVolumes) { ' (DROPPING VOLUMES)' })"
        $args = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs', 'down', '--remove-orphans')
        if ($DropVolumes) { $args += '--volumes' }
        & docker @args
        Write-Ok "Stack stopped"
        exit 0
    }

    # ---------- 1. Prereqs ----------
    Write-Step "Checking prerequisites"
    foreach ($cmd in @('docker', 'aws', 'python', 'git')) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
            throw "Missing required tool : '$cmd'. Install it and re-run."
        }
    }
    if (-not (docker info 2>$null)) {
        throw "Docker daemon not reachable. Start Docker Desktop and re-run."
    }
    Write-Ok "docker, aws, python, git all available"

    # ---------- 2. git pull ----------
    if (-not $NoPull) {
        Write-Step "git pull"
        $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
        if ($branch -eq 'main') {
            & git pull --ff-only origin main
        } else {
            Write-Warn "Branch '$branch' (not main) -> skip auto-pull, use -NoPull to silence"
        }
    }

    # ---------- 3. .venv ----------
    $venvPath = Join-Path $projectDir '.venv'
    if ($RecreateVenv -and (Test-Path $venvPath)) {
        Write-Step "Removing existing .venv (-RecreateVenv)"
        Remove-Item -Recurse -Force $venvPath
    }
    if (-not (Test-Path $venvPath)) {
        Write-Step "Creating .venv"
        & python -m venv .venv
        Write-Ok ".venv created"
        Write-Step "pip install -r requirements.txt (one-shot, ~2 min)"
        & "$venvPath\Scripts\python.exe" -m pip install --upgrade pip --quiet
        & "$venvPath\Scripts\python.exe" -m pip install -r requirements.txt --quiet
        Write-Ok "Dependencies installed"
    } else {
        Write-Ok ".venv already present (use -RecreateVenv to rebuild)"
    }
    & "$venvPath\Scripts\Activate.ps1"

    # ---------- 4. Secrets from SSM ----------
    Write-Step "Loading secrets from AWS SSM"
    & "$PSScriptRoot\load_secrets.ps1"

    # ---------- 5. docker compose up (all profiles : engines + ib + obs) ----------
    Write-Step "docker compose up -d$(if (-not $NoBuild) { ' --build' }) (profiles: engines, ib, obs)"
    $upArgs = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs', 'up', '-d')
    if (-not $NoBuild) { $upArgs += '--build' }
    & docker @upArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed (exit $LASTEXITCODE). Run with --build manually to see the error : docker compose --profile engines --profile ib --profile obs up -d --build"
    }

    # ---------- 6. Wait postgres healthy ----------
    Write-Step "Waiting for Postgres healthy (max 60s)"
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        $state = (docker inspect -f '{{.State.Health.Status}}' fxvol-postgres 2>$null)
        if ($state -eq 'healthy') { $healthy = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $healthy) { throw "Postgres did not become healthy within 60s" }
    Write-Ok "Postgres healthy"

    # ---------- 7. Alembic ----------
    Write-Step "Alembic upgrade head"
    docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head

    # ---------- 8. Restart nginx ----------
    Write-Step "Restart nginx (refresh upstream DNS)"
    docker compose restart nginx | Out-Null

    Write-Ok "Stack up at http://localhost/"
    Write-Ok "Done. UI : http://localhost/    API : http://localhost/api/v1/health    Grafana : http://localhost:3000/"
} finally {
    Pop-Location
}
