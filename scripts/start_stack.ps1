<#
.SYNOPSIS
  Build + demarre la stack fx-vol et ouvre Windows Terminal avec un tab `logs -f` par service.

.PARAMETER NoBuild
  Saute le --build (plus rapide si aucune image n'a change).

.EXAMPLE
  .\scripts\start_stack.ps1
  .\scripts\start_stack.ps1 -NoBuild
#>
param([switch]$NoBuild)

$ErrorActionPreference = 'Stop'

$services = @(
    'postgres', 'redis', 'api', 'db-writer', 'frontend', 'nginx',
    'market-data', 'vol-engine', 'risk-engine', 'ib-gateway'
)

$projectDir = Split-Path -Parent $PSScriptRoot

Push-Location $projectDir
try {
    # Les creds IB doivent venir du .env, pas du shell. Un env shell vide
    # surchargerait le .env et empecherait ib-gateway de se logguer.
    Write-Host "==> Clearing shell IB env vars (so .env wins)" -ForegroundColor Cyan
    Remove-Item Env:IB_USERID, Env:IB_PASSWORD -ErrorAction SilentlyContinue

    Write-Host "==> docker compose up (build=$(-not $NoBuild))" -ForegroundColor Cyan
    $upArgs = @('compose', '--profile', 'engines', '--profile', 'ib', 'up', '-d')
    if (-not $NoBuild) { $upArgs += '--build' }
    & docker @upArgs

    Write-Host "==> Alembic upgrade head" -ForegroundColor Cyan
    docker compose exec -T api python -m alembic -c persistence/alembic.ini upgrade head

    # nginx cache les IPs des upstreams au demarrage. Si l'api a ete
    # recreee apres nginx, il faut un restart pour refresh le DNS Docker.
    Write-Host "==> Restarting nginx (refresh upstream DNS)" -ForegroundColor Cyan
    docker compose restart nginx | Out-Null
} finally {
    Pop-Location
}

function Get-TabInit {
    param([string]$ProjectDir)
    return @"
Set-Location '$ProjectDir'
if (Test-Path .\.venv\Scripts\Activate.ps1) { .\.venv\Scripts\Activate.ps1 }
`$env:PYTHONPATH='src'
`$env:DB_PASSWORD='fxvol'
`$env:VNC_PASSWORD='local-dev'
`$env:REDIS_URL='redis://localhost:6380/0'
`$env:DATABASE_URL='postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol'
"@
}

function New-LogsTabCommand {
    param([string]$Service, [string]$ProjectDir)
    $init = Get-TabInit $ProjectDir
    $script = @"
$init
Write-Host '==> logs -f $Service' -ForegroundColor Cyan
docker compose logs -f $Service
"@
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
}

function New-HealthcheckTabCommand {
    param([string]$ProjectDir, [int]$WaitSeconds = 20)
    $init = Get-TabInit $ProjectDir
    $script = @"
$init
Write-Host '==> Healthcheck : attente $WaitSeconds s que les containers soient prets...' -ForegroundColor Yellow
Start-Sleep -Seconds $WaitSeconds
Write-Host '==> docker compose ps' -ForegroundColor Cyan
docker compose ps
Write-Host '==> pg_isready' -ForegroundColor Cyan
docker compose exec postgres pg_isready -U fxvol -d fxvol
Write-Host '==> alembic_version' -ForegroundColor Cyan
docker compose exec postgres psql -U fxvol -d fxvol -c 'SELECT version_num FROM alembic_version;'
Write-Host '==> redis PING' -ForegroundColor Cyan
docker compose exec redis redis-cli PING
Write-Host '==> api /health (interne)' -ForegroundColor Cyan
docker exec fxvol-api curl -fsS http://127.0.0.1:8000/api/v1/health
Write-Host ''
Write-Host '==> frontend bundle' -ForegroundColor Cyan
docker exec fxvol-frontend wget -qO- http://127.0.0.1:8080/ | Select-Object -First 5
Write-Host '==> nginx / (public)' -ForegroundColor Cyan
curl.exe -I http://localhost/
Write-Host '==> nginx /api/v1/health' -ForegroundColor Cyan
curl.exe http://localhost/api/v1/health
Write-Host ''
Write-Host '==> /api/v1/health/extended (lien DB+Redis+engines)' -ForegroundColor Cyan
curl.exe http://localhost/api/v1/health/extended
Write-Host ''
Write-Host '==> IB Gateway port 4002' -ForegroundColor Cyan
Test-NetConnection 127.0.0.1 -Port 4002 | Select-Object TcpTestSucceeded
Write-Host '==> Heartbeats engines' -ForegroundColor Cyan
foreach (`$hb in 'market_data','vol_engine','risk_engine','db_writer') {
    `$v = docker compose exec redis redis-cli GET "heartbeat:`$hb"
    Write-Host ("  heartbeat:{0,-13} -> {1}" -f `$hb, `$v)
}
Write-Host '==> position_snapshots count' -ForegroundColor Cyan
docker compose exec postgres psql -U fxvol -d fxvol -c 'SELECT COUNT(*) FROM position_snapshots;'
Write-Host ''
Write-Host '==> Healthcheck termine.' -ForegroundColor Green
"@
    return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
}

Write-Host "==> Opening Windows Terminal tabs" -ForegroundColor Cyan
$shell = 'powershell.exe'
$wtArgs = @()
for ($i = 0; $i -lt $services.Count; $i++) {
    $svc = $services[$i]
    $encoded = New-LogsTabCommand -Service $svc -ProjectDir $projectDir
    if ($i -gt 0) { $wtArgs += ';' }
    $wtArgs += @('new-tab', '--title', $svc, $shell, '-NoExit', '-EncodedCommand', $encoded)
}
# Tab final : healthcheck global (attend 20s que tout soit up puis lance les 15 probes)
$wtArgs += ';'
$wtArgs += @('new-tab', '--title', 'healthcheck', $shell, '-NoExit', '-EncodedCommand',
    (New-HealthcheckTabCommand -ProjectDir $projectDir -WaitSeconds 20))
& wt @wtArgs

Write-Host "==> Done. 10 tabs logs + 1 tab healthcheck ouverts." -ForegroundColor Green
