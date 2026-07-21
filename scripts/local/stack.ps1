<#
.SYNOPSIS
  SINGLE command to orchestrate the local fx-vol stack (Windows / PowerShell).

.DESCRIPTION
  One entry point that does everything:
    1. Check the prereqs (docker, aws, python)
    2. git pull (unless -NoPull)
    3. Create the .venv if missing + pip install
    4. Load the secrets from AWS SSM
    5. docker compose up -d --build (unless -NoBuild) on ALL profiles
       (engines + ib + obs) to build every container of the project
       (api, frontend, nginx, 5 engines, ib-gateway, prometheus, loki,
        promtail, tempo, otel-collector, grafana).
    6. Wait for Postgres healthy
    7. Alembic upgrade head
    8. Restart nginx (refresh DNS upstreams)

  Sub-mode -Down stops everything (and drops the volumes with -DropVolumes).

.PARAMETER Down
  Stops the stack and exits (instead of the up pipeline).

.PARAMETER NoPull
  Skips the git pull.

.PARAMETER NoBuild
  Skips the --build (faster when no image has changed).

.PARAMETER DropVolumes
  With -Down : docker compose down --volumes (drop postgres data + redis cache).

.PARAMETER RecreateVenv
  Force the .venv recreation (otherwise reuses the existing one).

.PARAMETER Service
  TARGETED mode: rebuild + recreate only the named service(s)
  (frontend, api, nginx, vol-engine, ...). Skips git pull / venv / full alembic /
  the other containers. The fastest way to iterate on a single container.
  With -Down : stops just those service(s). With -NoBuild : recreate without build.
  Reuses the secrets already loaded in the shell (otherwise loads them via SSM).
  Rebuilding 'api' -> alembic upgrade ; rebuilding 'frontend'/'api' -> restart nginx.

.PARAMETER Logs
  With -Service : tail the service(s) logs after the up.

.PARAMETER Core
  Starts ONLY the core (api + frontend + nginx + postgres + redis):
  no engines / ib / obs profile. The 5 engines (vol/risk/...) need an IB
  session to become healthy -- without IB logged in they stay 'unhealthy' (the
  IB connection retries forever before the 1st heartbeat). For front / api /
  nginx / security dev, the core is enough and avoids that noise. Combinable
  with -NoBuild.

.PARAMETER Refresh
  Purges the accumulated RAM WITHOUT losing data: down (volumes kept) ->
  'wsl --shutdown' (Docker's WSL2 VM gives its RAM back to Windows) -> wait for
  the engine to come back -> up -d (existing images) + alembic + nginx.
  WARNING : 'wsl --shutdown' stops ALL WSL distros (closes your other
  WSL sessions). DB/Redis (volumes) + images stay intact.

.PARAMETER Build
  Container lifecycle, phase BUILD (up): builds all images, starts nothing
  (compose build). Add -NoCache to rebuild every layer from scratch.
.PARAMETER Create
  Phase CREATE (up): creates all containers without starting them (compose create).
.PARAMETER Start
  Phase START (up): starts already-created containers (compose start).
.PARAMETER Stop
  Phase START (down): stops running containers, keeps them (compose stop).
.PARAMETER Purge
  Phase BUILD (down): removes all containers AND images (compose down --rmi all).
  Add -DropVolumes to also erase the data.
.PARAMETER Status
  Container status + health for every service (compose ps).

.EXAMPLE
  .\scripts\local\stack.ps1                      # ALL up : build + create + start (engines+ib+obs)
  .\scripts\local\stack.ps1 -NoBuild             # ALL up (no rebuild, fast)
  .\scripts\local\stack.ps1 -Build               # ALL BUILD up : images only
  .\scripts\local\stack.ps1 -Create              # ALL CREATE up : containers, not started
  .\scripts\local\stack.ps1 -Start               # ALL START up : start the containers
  .\scripts\local\stack.ps1 -Stop                # ALL START down : stop, keep containers
  .\scripts\local\stack.ps1 -Down                # ALL CREATE down : remove containers, keep images
  .\scripts\local\stack.ps1 -Purge               # ALL BUILD down : remove containers + images
  .\scripts\local\stack.ps1 -Status              # container status + health
  .\scripts\local\stack.ps1 -Core                # core only (no engines/ib/obs)
  .\scripts\local\stack.ps1 -Service frontend            # rebuild + recreate the frontend
  .\scripts\local\stack.ps1 -Service api,vol-engine      # rebuild 2 services
  .\scripts\local\stack.ps1 -Service vol-engine -Down    # stop just vol-engine
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
    [switch]$Core,
    [switch]$Build,
    [switch]$NoCache,
    [switch]$Create,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Purge,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $projectDir

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

try {
    # ---------- REFRESH mode: purge the RAM (WSL2 VM) without losing data ----------
    # down (volumes kept) -> wsl --shutdown (gives the RAM back to Windows) -> wait
    # for the Docker engine -> fall back into the normal up pipeline (NoPull + NoBuild).
    if ($Refresh) {
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets missing -> loading from AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        }
        $profiles = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs')
        Write-Step "Stopping the stack (volumes kept -> DB intact)"
        $downArgs = $profiles + @('down', '--remove-orphans')
        & docker @downArgs
        Write-Step "wsl --shutdown (the WSL2 VM gives its RAM back to Windows)"
        & wsl --shutdown
        Write-Step "Waiting for the Docker engine to restart (max 90s)"
        $engineOk = $false
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep -Seconds 2
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { $engineOk = $true; break }
        }
        if (-not $engineOk) {
            throw "Docker engine unavailable after 'wsl --shutdown'. Open Docker Desktop, then : .\scripts\local\stack.ps1 -NoBuild"
        }
        Write-Ok "Docker engine ready -> recreating the containers (existing images)"
        # Fall back into the up pipeline : no pull, no build.
        $NoPull = $true
        $NoBuild = $true
    }

    # ---------- TARGETED mode: one (or several) service only ----------
    # Rebuild/recreate/stop a specific container without rerunning the whole pipeline.
    # All profiles are passed so that any service name works.
    if ($Service) {
        $profiles = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs')

        if ($Down) {
            Write-Step "Stopping service(s): $($Service -join ', ')"
            $stopArgs = $profiles + @('stop') + $Service
            & docker @stopArgs
            Write-Ok "Stopped"
            exit 0
        }

        # docker compose interpolates ${DB_PASSWORD:?} at parse time -> secrets required in env.
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets missing from the shell -> loading from AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        } else {
            Write-Ok "Secrets already loaded in this shell (skip SSM)"
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
            Write-Step "Tailing logs (Ctrl-C to exit)"
            $logArgs = $profiles + @('logs', '-f') + $Service
            & docker @logArgs
        }
        exit 0
    }

    # ---------- Sous-mode -Down : stop & exit ----------
    if ($Down) {
        # compose interpolates ${DB_PASSWORD:?} at parse time even for 'down' -> secrets required.
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets missing from the shell -> loading from AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        }
        Write-Step "Stopping stack$(if ($DropVolumes) { ' (DROPPING VOLUMES)' })"
        $args = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs', 'down', '--remove-orphans')
        if ($DropVolumes) { $args += '--volumes' }
        & docker @args
        Write-Ok "Stack stopped"
        exit 0
    }

    # ---------- Lifecycle phases: build / create / start / stop / purge / status ----------
    # The container lifecycle as 3 mirror pairs, each a single explicit docker
    # compose phase (early-exit). 'up' (the default pipeline below) fuses
    # create+start; these switches expose each phase on its own.
    #
    #   ALL BUILD  up : build images        -Build      -> compose build
    #   ALL BUILD  down: remove images      -Purge      -> compose down --rmi all
    #   ALL CREATE up : create containers   -Create     -> compose create
    #   ALL CREATE down: remove containers  -Down       -> compose down  (above)
    #   ALL START  up : start containers    -Start      -> compose start
    #   ALL START  down: stop containers    -Stop       -> compose stop
    #
    # All need the secrets: compose interpolates ${DB_PASSWORD:?} at parse time.
    if ($Build -or $Create -or $Start -or $Stop -or $Purge -or $Status) {
        if (-not $env:DB_PASSWORD) {
            Write-Step "Secrets missing from the shell -> loading from AWS SSM"
            & "$PSScriptRoot\load_secrets.ps1"
        }
        $prof = @('compose', '--profile', 'engines', '--profile', 'ib', '--profile', 'obs')

        if ($Status) {
            Write-Step "ALL container status + health"
            & docker @($prof + @('ps'))
            exit 0
        }
        if ($Build) {
            Write-Step "ALL BUILD up: building all images$(if ($NoCache) { ' (no cache)' }) -- no container started"
            $a = $prof + @('build')
            if ($NoCache) { $a += '--no-cache' }
            & docker @a
            if ($LASTEXITCODE -ne 0) { throw "docker compose build failed (exit $LASTEXITCODE)" }
            Write-Ok "Images built (use an 'up' or -Create/-Start to run them)"
            exit 0
        }
        if ($Create) {
            Write-Step "ALL CREATE up: creating all containers (not started)"
            & docker @($prof + @('create'))
            Write-Ok "Containers created (use -Start to run them)"
            exit 0
        }
        if ($Start) {
            Write-Step "ALL START up: starting all containers"
            & docker @($prof + @('start'))
            Write-Ok "Containers started"
            exit 0
        }
        if ($Stop) {
            Write-Step "ALL START down: stopping all containers (kept, not removed)"
            & docker @($prof + @('stop'))
            Write-Ok "Containers stopped"
            exit 0
        }
        if ($Purge) {
            Write-Warn "ALL BUILD down: removing all containers AND images$(if ($DropVolumes) { ' + VOLUMES' })"
            $a = $prof + @('down', '--rmi', 'all', '--remove-orphans')
            if ($DropVolumes) { $a += '--volumes' }
            & docker @a
            Write-Ok "Containers + images removed"
            exit 0
        }
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
        # pyproject.toml is the single source of truth: no requirements.txt.
        Write-Step "pip install -e .[dev,api,quant,ib,writer] (one-shot, ~2 min)"
        & "$venvPath\Scripts\python.exe" -m pip install --upgrade pip --quiet
        & "$venvPath\Scripts\python.exe" -m pip install -e ".[dev,api,quant,ib,writer]" --quiet
        Write-Ok "Dependencies installed"
    } else {
        Write-Ok ".venv already present (use -RecreateVenv to rebuild)"
    }
    & "$venvPath\Scripts\Activate.ps1"

    # ---------- 4. Secrets from SSM ----------
    Write-Step "Loading secrets from AWS SSM"
    & "$PSScriptRoot\load_secrets.ps1"

    # ---------- 5. docker compose up ----------
    # Full = engines + ib + obs ; -Core = no profile (api/frontend/nginx/pg/redis).
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
