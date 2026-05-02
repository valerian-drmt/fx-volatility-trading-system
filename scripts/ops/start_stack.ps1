<#
.SYNOPSIS
  Commande UNIQUE pour orchestrer la stack fx-vol locale (Windows / PowerShell).

.DESCRIPTION
  Une seule entree pour tout faire :
    1. Verifier les prereqs (docker, aws, python)
    2. git pull (sauf -NoPull)
    3. Creer le .venv si absent + pip install
    4. Charger les secrets depuis AWS SSM
    5. docker compose up -d --build (sauf -NoBuild)
    6. Attendre Postgres healthy
    7. Alembic upgrade head
    8. Restart nginx (refresh DNS upstreams)
    9. Ouvrir Windows Terminal : 1 tab par service + 1 tab healthcheck

  Sous-mode -Down arrete tout (et droppe les volumes si -DropVolumes).

.PARAMETER Down
  Arrete la stack et sort (au lieu du pipeline up).

.PARAMETER NoPull
  Skip le git pull.

.PARAMETER NoBuild
  Skip le --build (plus rapide si aucune image n'a change).

.PARAMETER NoTabs
  Skip l'ouverture des Windows Terminal tabs (utile pour CI / scripting).

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
    [switch]$NoTabs,
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
        $args = @('compose', '--profile', '*', 'down', '--remove-orphans')
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

    # ---------- 5. docker compose up ----------
    Write-Step "docker compose up -d$(if (-not $NoBuild) { ' --build' })"
    $upArgs = @('compose', '--profile', 'engines', '--profile', 'ib', 'up', '-d')
    if (-not $NoBuild) { $upArgs += '--build' }
    & docker @upArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed (exit $LASTEXITCODE). Run with --build manually to see the error : docker compose --profile engines --profile ib up -d --build"
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
} finally {
    Pop-Location
}

# ---------- 9. Windows Terminal tabs ----------
if ($NoTabs) { return }
if (-not (Get-Command wt -ErrorAction SilentlyContinue)) {
    Write-Warn "Windows Terminal (wt.exe) not found -> skip tabs. Install from Microsoft Store."
    return
}

$services = @(
    'postgres', 'redis', 'api', 'db-writer', 'frontend', 'nginx',
    'market-data', 'vol-engine', 'risk', 'ib-gateway'
)

function Get-TabInit($projectDir) {
    # Each tab is a fresh PS process (no env inheritance) -> re-load SSM so
    # docker compose can resolve ${VAR:?} when parsing compose.yml.
    return @"
Set-Location '$projectDir'
if (Test-Path .\.venv\Scripts\Activate.ps1) { .\.venv\Scripts\Activate.ps1 }
& .\scripts\ops\load_secrets.ps1 | Out-Null
"@
}

function New-LogsTab($svc, $projectDir) {
    $script = "$(Get-TabInit $projectDir)`nWrite-Host '==> logs -f $svc' -ForegroundColor Cyan`ndocker compose logs -f $svc"
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
}

function New-HealthcheckTab($projectDir) {
    $body = @"
$(Get-TabInit $projectDir)
Write-Host '==> Waiting 20s for containers to settle...' -ForegroundColor Yellow
Start-Sleep -Seconds 20
Write-Host '==> docker compose ps' -ForegroundColor Cyan ; docker compose ps
Write-Host '==> pg_isready' -ForegroundColor Cyan ; docker compose exec postgres pg_isready -U fxvol -d fxvol
Write-Host '==> redis PING' -ForegroundColor Cyan ; docker compose exec redis redis-cli PING
Write-Host '==> /api/v1/health' -ForegroundColor Cyan ; curl.exe http://localhost/api/v1/health
Write-Host ''
Write-Host '==> /api/v1/health/extended' -ForegroundColor Cyan ; curl.exe http://localhost/api/v1/health/extended
Write-Host ''
Write-Host '==> Engine heartbeats' -ForegroundColor Cyan
foreach (`$hb in 'market_data','vol_engine','risk_engine','db_writer') {
    `$v = docker compose exec redis redis-cli GET "heartbeat:`$hb"
    Write-Host ("    {0,-13} -> {1}" -f `$hb, `$v)
}
Write-Host '==> IB Gateway port 4002' -ForegroundColor Cyan
Test-NetConnection 127.0.0.1 -Port 4002 | Select-Object TcpTestSucceeded
Write-Host '==> Healthcheck done.' -ForegroundColor Green
"@
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
}

Write-Step "Opening Windows Terminal : $($services.Count) logs tabs + 1 healthcheck"
$wtArgs = @()
for ($i = 0; $i -lt $services.Count; $i++) {
    if ($i -gt 0) { $wtArgs += ';' }
    $wtArgs += @('new-tab', '--suppressApplicationTitle', '--title', $services[$i],
                 'powershell.exe', '-NoExit',
                 '-EncodedCommand', (New-LogsTab $services[$i] $projectDir))
}
$wtArgs += ';'
$wtArgs += @('new-tab', '--suppressApplicationTitle', '--title', 'healthcheck',
             'powershell.exe', '-NoExit',
             '-EncodedCommand', (New-HealthcheckTab $projectDir))
& wt @wtArgs

Write-Ok "Done. UI : http://localhost/    API : http://localhost/api/v1/health"
