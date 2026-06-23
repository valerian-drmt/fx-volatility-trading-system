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

.PARAMETER Service
  Mode CIBLE : rebuild + recreate uniquement le(s) service(s) nomme(s)
  (frontend, api, nginx, vol-engine, ...). Skip git pull / venv / alembic full /
  les autres containers. Le plus rapide pour iterer sur un seul container.
  Avec -Down : arrete juste ce(s) service(s). Avec -NoBuild : recreate sans build.
  Reutilise les secrets deja charges dans le shell (sinon les charge via SSM).
  Rebuild 'api' -> alembic upgrade ; rebuild 'frontend'/'api' -> restart nginx.

.PARAMETER Logs
  Avec -Service : tail les logs du/des service(s) apres le up.

.PARAMETER Core
  Demarre UNIQUEMENT le core (api + frontend + nginx + postgres + redis) :
  aucun profil engines / ib / obs. Les 5 engines (vol/risk/...) ont besoin d'une
  session IB pour devenir healthy -- sans IB logge ils restent 'unhealthy' (la
  connexion IB retry a l'infini avant le 1er heartbeat). Pour le dev front / api /
  nginx / securite, le core suffit et evite ce bruit. Combinable avec -NoBuild.

.PARAMETER Refresh
  Purge la RAM accumulee SANS perdre de donnees : down (volumes conserves) ->
  'wsl --shutdown' (la VM WSL2 de Docker rend sa RAM a Windows) -> attend le
  retour du moteur -> up -d (images existantes) + alembic + nginx.
  ATTENTION : 'wsl --shutdown' arrete TOUTES les distros WSL (ferme tes autres
  sessions WSL). DB/Redis (volumes) + images intactes.

.EXAMPLE
  .\scripts\ops\start_stack.ps1                      # full pipeline up (engines + ib + obs)
  .\scripts\ops\start_stack.ps1 -Core                # core seul (pas d'engines/ib/obs)
  .\scripts\ops\start_stack.ps1 -Core -NoBuild       # core seul, rapide
  .\scripts\ops\start_stack.ps1 -NoPull -NoBuild     # demarrage rapide
  .\scripts\ops\start_stack.ps1 -Down                # stop
  .\scripts\ops\start_stack.ps1 -Down -DropVolumes   # stop + wipe data
  .\scripts\ops\start_stack.ps1 -Service frontend            # rebuild + recreate le frontend (rapide)
  .\scripts\ops\start_stack.ps1 -Service frontend -Logs      # idem + tail logs
  .\scripts\ops\start_stack.ps1 -Service api,vol-engine      # rebuild 2 services
  .\scripts\ops\start_stack.ps1 -Service frontend -NoBuild   # recreate sans rebuild
  .\scripts\ops\start_stack.ps1 -Service vol-engine -Down    # stop juste vol-engine
#>
param(
    [switch]$Down,
    [switch]$NoPull,
    [switch]$NoBuild,
    [switch]$DropVolumes,
    [switch]$RecreateVenv,
    [string[]]$Service,
    [switch]$Logs,
    [switch]$Refresh,
    [switch]$Core
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectDir

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

try {
    # ---------- Mode REFRESH : purge la RAM (VM WSL2) sans perdre les donnees ----------
    # down (volumes gardes) -> wsl --shutdown (rend la RAM a Windows) -> attend le
    # moteur Docker -> retombe dans le pipeline up normal (NoPull + NoBuild).
    if ($Refresh) {
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets absents -> chargement depuis AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        }
        $profiles = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs')
        Write-Step "Arret de la stack (volumes conserves -> DB intacte)"
        $downArgs = $profiles + @('down', '--remove-orphans')
        & docker @downArgs
        Write-Step "wsl --shutdown (la VM WSL2 rend sa RAM a Windows)"
        & wsl --shutdown
        Write-Step "Attente du redemarrage du moteur Docker (max 90s)"
        $engineOk = $false
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep -Seconds 2
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { $engineOk = $true; break }
        }
        if (-not $engineOk) {
            throw "Moteur Docker indisponible apres 'wsl --shutdown'. Ouvre Docker Desktop, puis : .\scripts\ops\start_stack.ps1 -NoBuild"
        }
        Write-Ok "Moteur Docker pret -> recreation des containers (images existantes)"
        # Retombe dans le pipeline up : pas de pull, pas de build.
        $NoPull = $true
        $NoBuild = $true
    }

    # ---------- Mode CIBLE : un (ou plusieurs) service seulement ----------
    # Rebuild/recreate/stop d'un container precis sans relancer tout le pipeline.
    # Tous les profils sont passes pour que n'importe quel nom de service marche.
    if ($Service) {
        $profiles = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs')

        if ($Down) {
            Write-Step "Stopping service(s): $($Service -join ', ')"
            $stopArgs = $profiles + @('stop') + $Service
            & docker @stopArgs
            Write-Ok "Stopped"
            exit 0
        }

        # docker compose interpole ${DB_PASSWORD:?} au parse -> secrets requis en env.
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets absents du shell -> chargement depuis AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        } else {
            Write-Ok "Secrets deja charges dans ce shell (skip SSM)"
        }

        Write-Step "Rebuild + recreate: $($Service -join ', ')$(if ($NoBuild) { ' (no build)' })"
        $upArgs = $profiles + @('up', '-d')
        if (-not $NoBuild) { $upArgs += '--build' }
        $upArgs += $Service
        & docker @upArgs
        if ($LASTEXITCODE -ne 0) { throw "docker compose up failed (exit $LASTEXITCODE)" }

        # api rebuild -> migrations ; frontend/api rebuild -> refresh DNS upstream nginx.
        if ($Service -contains 'api') {
            Write-Step "Alembic upgrade head (api rebuilt)"
            docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head
        }
        if (($Service -contains 'frontend') -or ($Service -contains 'api')) {
            Write-Step "Restart nginx (refresh upstream DNS)"
            docker compose restart nginx | Out-Null
        }

        Write-Ok "Service(s) up: $($Service -join ', ')  -  UI http://localhost/"
        if ($Logs) {
            Write-Step "Tailing logs (Ctrl-C pour sortir)"
            $logArgs = $profiles + @('logs', '-f') + $Service
            & docker @logArgs
        }
        exit 0
    }

    # ---------- Sous-mode -Down : stop & exit ----------
    if ($Down) {
        # compose interpole ${DB_PASSWORD:?} au parse meme pour 'down' -> secrets requis.
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets absents du shell -> chargement depuis AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        }
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

    # ---------- 5. docker compose up ----------
    # Full = engines + ib + obs ; -Core = aucun profil (api/frontend/nginx/pg/redis).
    if ($Core) {
        $profileArgs = @()
        $scopeMsg = 'core only: api/frontend/nginx/postgres/redis'
    } else {
        $profileArgs = @('--profile', 'engines', '--profile', 'ib', '--profile', 'obs')
        $scopeMsg = 'profiles: engines, ib, obs'
    }
    Write-Step "docker compose up -d$(if (-not $NoBuild) { ' --build' }) ($scopeMsg)"
    $upArgs = @('compose') + $profileArgs + @('up', '-d')
    if (-not $NoBuild) { $upArgs += '--build' }
    & docker @upArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed (exit $LASTEXITCODE). Re-run with --build manually to see the error : docker compose $($profileArgs -join ' ') up -d --build"
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
    if ($Core) {
        Write-Ok "Done (core only). UI : http://localhost/    API : http://localhost/api/v1/health"
    } else {
        Write-Ok "Done. UI : http://localhost/    API : http://localhost/api/v1/health    Grafana : http://localhost:3000/"
    }
} finally {
    Pop-Location
}
