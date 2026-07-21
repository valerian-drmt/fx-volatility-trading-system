<#
.SYNOPSIS
  Interactive launcher for every fx-vol ops script (Windows / PowerShell).

.DESCRIPTION
  Single clickable entry point (see cmd_menu.bat at the repo root) that wraps the
  four ops scripts so none of their flags have to be memorised:

    * scripts/local/stack.ps1          -> LOCAL docker stack
    * scripts/local/load_secrets.ps1         -> SSM secrets into this session
    * scripts/aws/ec2.ps1                    -> PROD host on EC2
    * infrastructure/aws/provision-deploy-oidc.ps1 -> one-shot AWS provisioning

  The menu runs in a long-lived shell: secrets loaded by one action stay in RAM
  for the following ones (env vars are process-wide). Nothing is written to disk
  and no secret value is ever printed -- the 'secrets status' action shows names
  and lengths only.

  Destructive actions (volume drop, prod deploy, instance stop, compose down)
  require typing the confirmation word shown on screen.

.EXAMPLE
  .\scripts\fxvol.ps1        # or just double-click cmd_menu.bat
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# This launcher lives at scripts/; the wrapped scripts are split into
# scripts/local (docker stack) and scripts/aws (EC2 host).
$ScriptsDir = $PSScriptRoot
$ProjectDir = Split-Path -Parent $ScriptsDir
$StackPs1   = Join-Path $ScriptsDir 'local\stack.ps1'
$SecretsPs1 = Join-Path $ScriptsDir 'local\load_secrets.ps1'
$Ec2Ps1     = Join-Path $ScriptsDir 'aws\ec2.ps1'
$OidcPs1    = Join-Path $ProjectDir 'infrastructure\aws\provision-deploy-oidc.ps1'

# Secret-bearing env vars we may report on. Names + lengths only, never values.
$SecretVars = @(
    'DB_PASSWORD', 'REDIS_PASSWORD', 'IB_USERID', 'IB_PASSWORD', 'VNC_PASSWORD',
    'FRED_API_KEY', 'AUTH_SECRET', 'AUTH_PASSWORD_HASH'
)

function Write-Head($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   [!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "   [X]  $msg" -ForegroundColor Red }

# Free-text prompt. Returns $null when the user just hits Enter.
function Read-Value($prompt) {
    $v = (Read-Host $prompt).Trim()
    if ([string]::IsNullOrWhiteSpace($v)) { return $null }
    return $v
}

# Typed-word guard for anything destructive or outward-facing.
function Confirm-Word($word, $what) {
    Write-Warn $what
    $typed = (Read-Host "   Type '$word' to confirm (anything else aborts)").Trim()
    if ($typed -ceq $word) { return $true }
    Write-Host "   Aborted." -ForegroundColor DarkGray
    return $false
}

# Run a child script, but never let its failure kill the menu.
function Invoke-Script($path, [string[]]$argv) {
    if (-not (Test-Path $path)) { Write-Err "Missing script: $path"; return }
    try {
        & $path @argv
    } catch {
        Write-Err $_.Exception.Message
    }
}

function Show-SecretStatus {
    Write-Head 'Secrets loaded in this session (names + lengths only)'
    foreach ($name in $SecretVars) {
        $val = [Environment]::GetEnvironmentVariable($name)
        if ($val) {
            Write-Host ("   {0,-20} set, {1} chars" -f $name, $val.Length) -ForegroundColor Green
        } else {
            Write-Host ("   {0,-20} MISSING" -f $name) -ForegroundColor DarkGray
        }
    }
    foreach ($name in @('DATABASE_URL', 'REDIS_URL', 'PYTHONPATH')) {
        $set = if ([Environment]::GetEnvironmentVariable($name)) { 'set' } else { 'MISSING' }
        Write-Host ("   {0,-20} {1}" -f $name, $set) -ForegroundColor DarkGray
    }
}

function Show-Menu {
    $loaded = if ($env:DB_PASSWORD) { 'secrets: loaded' } else { 'secrets: NOT loaded' }
    $branch = try { (& git -C $ProjectDir rev-parse --abbrev-ref HEAD).Trim() } catch { '?' }

    Write-Host ''
    Write-Host '  fx-volatility-trading-system - ops launcher' -ForegroundColor White
    Write-Host "  branch: $branch    $loaded" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  LOCAL STACK  (stack.ps1)' -ForegroundColor Cyan
    Write-Host '   1  Start full stack           (build, engines+ib+obs)'
    Write-Host '   2  Start full stack fast      (-NoBuild, reuse images)'
    Write-Host '   3  Start core only            (api/frontend/nginx/pg/redis)'
    Write-Host '   4  Rebuild one service        (-Service <name>)'
    Write-Host '   5  Tail one service log       (-Service <name> -Logs -NoBuild)'
    Write-Host '   6  Refresh RAM                (-Refresh, keeps data)'
    Write-Host '   7  Stop stack                 (-Down)'
    Write-Host '   8  Stop stack + DROP volumes  (-Down -DropVolumes)   [destructive]'
    Write-Host ''
    Write-Host '  SECRETS  (load_secrets.ps1)' -ForegroundColor Cyan
    Write-Host '   9  Load secrets from AWS SSM into this session'
    Write-Host '  10  Show which secrets are loaded (no values)'
    Write-Host ''
    Write-Host '  PROD on EC2  (ec2.ps1)' -ForegroundColor Cyan
    Write-Host '  11  Health check the live site'
    Write-Host '  12  List containers on the host    (ps)'
    Write-Host '  13  Tail a host service log        (logs <svc>)'
    Write-Host '  14  Restart a host service         (restart <svc>)'
    Write-Host '  15  Alembic upgrade head on host'
    Write-Host '  16  Deploy main HEAD               [outward-facing]'
    Write-Host '  17  Deploy / rollback to a SHA     [outward-facing]'
    Write-Host '  18  Containers up on host          (up)'
    Write-Host '  19  Containers down on host        (down)          [destructive]'
    Write-Host '  20  Instance status'
    Write-Host '  21  Instance STOP  (cuts compute billing)          [destructive]'
    Write-Host '  22  Instance START'
    Write-Host '  23  Interactive shell on host      (SSM session)'
    Write-Host ''
    Write-Host '  AWS PROVISIONING  (one-shot)' -ForegroundColor Cyan
    Write-Host '  24  provision-deploy-oidc.ps1      [outward-facing]'
    Write-Host ''
    Write-Host '   q  Quit to this shell (secrets stay loaded)' -ForegroundColor DarkGray
    Write-Host ''
}

Push-Location $ProjectDir
try {
    while ($true) {
        Show-Menu
        $choice = (Read-Host '  Choice').Trim().ToLower()

        switch ($choice) {

            # ---------- LOCAL STACK ----------
            '1' { Invoke-Script $StackPs1 @() }
            '2' { Invoke-Script $StackPs1 @('-NoBuild') }
            '3' { Invoke-Script $StackPs1 @('-Core') }

            '4' {
                $svc = Read-Value '   Service(s), comma-separated (e.g. frontend,api)'
                if (-not $svc) { Write-Warn 'No service given.'; break }
                Invoke-Script $StackPs1 @('-Service', $svc)
            }

            '5' {
                $svc = Read-Value '   Service to tail (e.g. api)'
                if (-not $svc) { Write-Warn 'No service given.'; break }
                Invoke-Script $StackPs1 @('-Service', $svc, '-NoBuild', '-Logs')
            }

            '6' {
                if (Confirm-Word 'REFRESH' "'wsl --shutdown' stops ALL WSL distros (your other WSL sessions close). Data and images survive.") {
                    Invoke-Script $StackPs1 @('-Refresh')
                }
            }

            '7' { Invoke-Script $StackPs1 @('-Down') }

            '8' {
                if (Confirm-Word 'DROP' 'This WIPES the Postgres data and the Redis cache. Irreversible.') {
                    Invoke-Script $StackPs1 @('-Down', '-DropVolumes')
                }
            }

            # ---------- SECRETS ----------
            '9' {
                Write-Head 'Loading secrets from AWS SSM'
                Invoke-Script $SecretsPs1 @()
                Show-SecretStatus
            }
            '10' { Show-SecretStatus }

            # ---------- PROD ----------
            '11' { Invoke-Script $Ec2Ps1 @('health') }
            '12' { Invoke-Script $Ec2Ps1 @('ps') }

            '13' {
                $svc = Read-Value '   Host service to tail (e.g. nginx)'
                if (-not $svc) { Write-Warn 'No service given.'; break }
                $tail = Read-Value '   Lines (Enter = 80)'
                $argv = @('logs', $svc)
                if ($tail) { $argv += @('-Tail', $tail) }
                Invoke-Script $Ec2Ps1 $argv
            }

            '14' {
                $svc = Read-Value '   Host service to restart (e.g. api)'
                if (-not $svc) { Write-Warn 'No service given.'; break }
                Invoke-Script $Ec2Ps1 @('restart', $svc)
            }

            '15' { Invoke-Script $Ec2Ps1 @('alembic') }

            '16' {
                if (Confirm-Word 'DEPLOY' 'Deploys main HEAD to the LIVE public site.') {
                    Invoke-Script $Ec2Ps1 @('deploy')
                }
            }

            '17' {
                $sha = Read-Value '   Commit SHA to deploy'
                if (-not $sha) { Write-Warn 'No SHA given.'; break }
                if (Confirm-Word 'DEPLOY' "Deploys commit $sha to the LIVE public site.") {
                    Invoke-Script $Ec2Ps1 @('deploy', '-Sha', $sha)
                }
            }

            '18' { Invoke-Script $Ec2Ps1 @('up') }

            '19' {
                if (Confirm-Word 'DOWN' 'Stops every container on the PROD host: the public site goes offline.') {
                    Invoke-Script $Ec2Ps1 @('down')
                }
            }

            '20' { Invoke-Script $Ec2Ps1 @('instance-status') }

            '21' {
                if (Confirm-Word 'STOP' 'Stops the EC2 INSTANCE: the site goes offline until instance-start.') {
                    Invoke-Script $Ec2Ps1 @('instance-stop')
                }
            }

            '22' { Invoke-Script $Ec2Ps1 @('instance-start') }
            '23' { Invoke-Script $Ec2Ps1 @('connect') }

            # ---------- PROVISIONING ----------
            '24' {
                if (Confirm-Word 'PROVISION' 'Creates/updates IAM roles, the OIDC provider, the S3 bucket and GitHub repo variables. Needs the AWS profile "admin".') {
                    Invoke-Script $OidcPs1 @()
                }
            }

            'q' {
                Write-Host ''
                Write-Ok 'Menu closed. This shell keeps the loaded secrets in RAM.'
                return
            }

            default { Write-Warn "Unknown choice: '$choice'" }
        }

        Write-Host ''
        Read-Host '   -- Enter to return to the menu --' | Out-Null
    }
} finally {
    Pop-Location
}