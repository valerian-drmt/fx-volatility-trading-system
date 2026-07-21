<#
.SYNOPSIS
  SINGLE command to drive the LIVE fx-vol stack on EC2 (Windows / PowerShell).

.DESCRIPTION
  Counterpart of stack.ps1 (which handles LOCAL). This script drives the
  prod EC2 HOST without having to remember the aws/gh/ssm commands by hand.

  Two families of actions:
    * INSTANCE CONTROL (compute billing)
        instance-status / instance-stop / instance-start
    * CONTAINER CONTROL + DEPLOY (on the host, via SSM Session Manager - port 22 closed)
        connect / deploy / ps / up / down / restart / logs / alembic / health

  The host is reached through AWS SSM (never SSH). The instance is resolved by
  tag Name=fxvol-prod (override via -InstanceId). No secret is read or printed:
  the .env lives on the host (rendered by deploy.yml from SSM) and docker compose
  loads it by itself from /opt/fxvol.

  Laptop-side prerequisites:
    - AWS CLI v2 + Session Manager plugin (for 'connect')
    - gh CLI authenticated (for 'deploy')
    - AWS profile 'admin' (override via -Profile)

.PARAMETER Action
  connect          Interactive shell on the host (SSM Session Manager).
  deploy           Triggers the GitHub 'deploy-prod' workflow (build->ghcr->host).
                   With -Sha: rollback/deploy a specific commit (deploy_sha).
  health           GET https://<Domain>/fx-volatility-trading-system/api/v1/health from the laptop.
  ps               Lists the containers running on the host.
  stack            Full health dashboard in one call: every container + status,
                   per-container RAM/CPU, host memory + swap, and the engine
                   heartbeats in Redis. The /dev console is deliberately blocked
                   in prod (nginx returns 404), so this is how you inspect live.
  up               docker compose up -d on the host (restarts the stack).
  down             docker compose down on the host (stops the containers, keeps the instance).
  restart <svc>    docker compose restart <svc> (e.g. nginx, api, frontend).
  logs <svc>       Tail a service's logs (-Tail N, default 80).
  alembic          docker compose exec api alembic upgrade head.
  instance-status  EC2 state (running/stopped) + public IP.
  instance-stop    Stops the INSTANCE (cuts the ~$15/mo compute bill). Also stops the containers.
  instance-start   Starts the instance (the systemd unit re-ups the stack at boot if enabled).

.PARAMETER Target
  Service name for 'logs' / 'restart' (e.g. nginx, api, frontend, vol-engine).

.PARAMETER Sha
  With 'deploy': commit SHA to deploy (rollback). Otherwise = latest main build.

.PARAMETER InstanceId
  Instance override (otherwise resolved by tag Name=fxvol-prod).

.PARAMETER Region   Default eu-west-1.
.PARAMETER Profile  AWS CLI profile. Default admin.
.PARAMETER Domain   Default valeriandarmente.dev.
.PARAMETER Repo     GitHub repo for 'deploy'. Default valerian-drmt/fx-volatility-trading-system.
.PARAMETER Tail     With 'logs': number of lines. Default 80.

.EXAMPLE
  .\scripts\ops\ec2.ps1 health                 # is the site responding?
  .\scripts\ops\ec2.ps1 deploy                 # deploy main HEAD (after merge)
  .\scripts\ops\ec2.ps1 deploy -Sha 1a2b3c4    # rollback to an older commit
  .\scripts\ops\ec2.ps1 ps                      # containers up on the host
  .\scripts\ops\ec2.ps1 stack                   # full health dashboard (status + RAM/CPU + heartbeats)
  .\scripts\ops\ec2.ps1 logs nginx -Tail 120    # nginx logs
  .\scripts\ops\ec2.ps1 restart api             # restart a service
  .\scripts\ops\ec2.ps1 connect                 # interactive shell on the host
  .\scripts\ops\ec2.ps1 down                    # stop the containers (instance stays up)
  .\scripts\ops\ec2.ps1 instance-stop           # CUT the compute cost (stops the instance)
  .\scripts\ops\ec2.ps1 instance-start          # start the instance again
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet(
        'connect', 'deploy', 'health', 'ps', 'stack', 'up', 'down', 'restart', 'logs',
        'alembic', 'instance-status', 'instance-stop', 'instance-start'
    )]
    [string]$Action,

    [Parameter(Position = 1)]
    [string]$Target,

    [string]$Sha,
    [string]$InstanceId,
    [string]$Region  = 'eu-west-1',
    [string]$Profile = 'admin',
    [string]$Domain  = 'valeriandarmente.dev',
    [string]$Repo    = 'valerian-drmt/fx-volatility-trading-system',
    [int]$Tail       = 80
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

$HostDir = '/opt/fxvol'   # where docker-compose.yml + .env live on the host

# ---------- Instance resolution by tag (unless -InstanceId) ----------
function Resolve-InstanceId {
    if ($InstanceId) { return $InstanceId }
    Write-Step "Resolving the instance (tag Name=fxvol-prod)"
    $id = (& aws ec2 describe-instances `
            --region $Region --profile $Profile `
            --filters 'Name=tag:Name,Values=fxvol-prod' `
                      'Name=instance-state-name,Values=pending,running,stopping,stopped' `
            --query 'Reservations[].Instances[].InstanceId | [0]' `
            --output text).Trim()
    if (-not $id -or $id -eq 'None') {
        throw "No instance with tag Name=fxvol-prod (region $Region, profile $Profile). Pass -InstanceId."
    }
    Write-Ok "Instance : $id"
    return $id
}

# ---------- SSM helper: send a shell command to the host, wait, print ----------
# SSM RunShellScript runs under /bin/sh (dash): no 'set -o pipefail'.
# The command runs in a single shell -> 'cd /opt/fxvol && ...' is OK.
function Invoke-OnHost {
    param([string]$ShellCommand, [string]$Label)

    $iid = Resolve-InstanceId
    if (-not $Label) { $Label = "Running on the host : $ShellCommand" }
    Write-Step $Label

    # This file has CRLF endings, so any multi-line command (here-strings) would
    # reach the host with trailing \r on every line: /bin/sh then tries to run
    # 'sort\r' ("not found") and 'cd /opt/fxvol\r' ("can't cd"). Normalise to LF.
    $ShellCommand = $ShellCommand -replace "`r`n", "`n" -replace "`r", "`n"

    # PowerShell strips the embedded double-quotes when handing a JSON string to
    # a native exe, so aws receives {commands:[...]} (invalid JSON) and rejects
    # it. Write the payload to a temp file and pass it via file:// instead.
    $paramsJson = ConvertTo-Json @{ commands = @($ShellCommand) } -Compress
    $paramsFile = New-TemporaryFile
    [System.IO.File]::WriteAllText(
        $paramsFile.FullName, $paramsJson,
        (New-Object System.Text.UTF8Encoding($false)))
    try {
        $cmdId = (& aws ssm send-command `
                --region $Region --profile $Profile `
                --instance-ids $iid `
                --document-name 'AWS-RunShellScript' `
                --comment "fxvol ec2.ps1 $Action" `
                --parameters ("file://" + $paramsFile.FullName.Replace('\', '/')) `
                --query 'Command.CommandId' --output text).Trim()
    }
    finally {
        Remove-Item $paramsFile -Force -ErrorAction SilentlyContinue
    }
    if (-not $cmdId) { throw "send-command did not return a CommandId" }

    & aws ssm wait command-executed `
        --region $Region --profile $Profile `
        --command-id $cmdId --instance-id $iid 2>$null
    # 'wait command-executed' exits != 0 when the host script returns != 0: we
    # still print stdout/stderr (more useful than the wait failure itself).

    $inv = (& aws ssm get-command-invocation `
            --region $Region --profile $Profile `
            --command-id $cmdId --instance-id $iid `
            --query '{status:Status,out:StandardOutputContent,err:StandardErrorContent}' `
            --output json) | ConvertFrom-Json

    if ($inv.out) { Write-Host $inv.out }
    if ($inv.err) { Write-Warn $inv.err.Trim() }
    if ($inv.status -ne 'Success') {
        Write-Warn "SSM status : $($inv.status)"
    } else {
        Write-Ok "OK"
    }
}

switch ($Action) {

    'connect' {
        $iid = Resolve-InstanceId
        # Print the server cheat-sheet in this window BEFORE opening the shell,
        # so the quick commands stay visible (scroll up) during the session.
        Write-Host ""
        Write-Host "  ===== fxvol server — quick commands (run once connected) =====" -ForegroundColor Cyan
        Write-Host "  cd $HostDir                                       # ALWAYS first" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  sudo docker compose ps                            # containers + health" -ForegroundColor Gray
        Write-Host "  sudo docker compose logs -f <svc>                 # tail a service (Ctrl-C)" -ForegroundColor Gray
        Write-Host "  sudo docker compose restart <svc>                 # restart a service" -ForegroundColor Gray
        Write-Host "  sudo docker compose up -d                         # (re)start the stack" -ForegroundColor Gray
        Write-Host "  sudo docker compose down                          # stop the stack" -ForegroundColor Gray
        Write-Host "  sudo docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  sudo docker stats --no-stream                     # per-container RAM/CPU" -ForegroundColor Gray
        Write-Host "  free -m ; df -h /                                 # host RAM / disk" -ForegroundColor Gray
        Write-Host "  exit   (or Ctrl-D)                                # leave the server" -ForegroundColor Gray
        Write-Host "  ==============================================================" -ForegroundColor Cyan
        Write-Host ""
        Write-Step "Opening interactive shell on $iid"
        & aws ssm start-session --region $Region --profile $Profile --target $iid
    }

    'deploy' {
        Write-Step "Triggering the deploy-prod workflow"
        if ($Sha) {
            Write-Warn "Deploying a specific SHA (rollback) : $Sha"
            & gh workflow run deploy-prod --repo $Repo -f "deploy_sha=$Sha"
        } else {
            & gh workflow run deploy-prod --repo $Repo
        }
        Write-Ok "Launched. Follow with: gh run watch --repo $Repo  (or gh run list --repo $Repo)"
    }

    'health' {
        $url = "https://$Domain/fx-volatility-trading-system/api/v1/health"
        Write-Step "GET $url"
        try {
            $resp = Invoke-WebRequest -Uri $url -TimeoutSec 15 -UseBasicParsing
            # A bare 200 also passes on the SPA fallback page — require API JSON
            # (same assert as the deploy.yml smoke step).
            if ($resp.Content -notmatch '"status"') { throw "body is not API JSON (SPA fallback?)" }
            Write-Ok "HTTP $($resp.StatusCode) : $($resp.Content)"
        } catch {
            Write-Warn "Failure : $($_.Exception.Message)"
            exit 1
        }
    }

    'ps'      { Invoke-OnHost "cd $HostDir && sudo docker compose ps" "Containers on the host" }

    'stack' {
        # One SSM round-trip for the whole picture. Sorted by memory so the
        # biggest consumer is first — on a 4 GB box that is the number that
        # matters. 'docker stats' needs --no-stream or it never returns.
        # Engine liveness is the Redis heartbeat, not the container status: a
        # container can be Up while its IB connection is dead.
        # Single-quoted here-string: the shell/awk below needs both quote kinds,
        # and PowerShell escaping inside a normal string mangles them. __DIR__ is
        # substituted afterwards rather than interpolated for the same reason.
        # 'sort -h' cannot read docker's MiB/GiB suffixes, so awk normalises to
        # MiB first — otherwise the ordering is silently meaningless.
        $script = @'
cd __DIR__
echo '--- CONTAINERS ---'
sudo docker compose ps --format '{{.Name}}\t{{.Status}}' | sort
echo
echo '--- RAM / CPU (sorted by RAM) ---'
sudo docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' \
  | awk -F'\t' '{v=$2; sub(/ .*/,"",v); u=v; gsub(/[0-9.]/,"",u); n=v+0;
                 if(u=="GiB")n*=1024; else if(u=="KiB")n/=1024;
                 printf "%8.1f MiB  %-22s CPU %s\n", n, $1, $3}' \
  | sort -rn
echo
echo '--- HOST MEMORY ---'
free -m | sed -n '1,3p'
echo
echo '--- DISK ---'
df -h / | tail -1
echo
echo '--- ENGINE HEARTBEATS (Redis) ---'
sudo docker compose exec -T redis redis-cli KEYS 'heartbeat:*'
'@
        Invoke-OnHost ($script -replace '__DIR__', $HostDir) "Live stack health"
    }

    'up'      { Invoke-OnHost "cd $HostDir && sudo docker compose up -d" "docker compose up -d" }
    'down'    { Invoke-OnHost "cd $HostDir && sudo docker compose down" "docker compose down (containers stopped, instance stays up)" }
    'alembic' { Invoke-OnHost "cd $HostDir && sudo docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head" "Alembic upgrade head" }

    'restart' {
        if (-not $Target) { throw "Usage : ec2.ps1 restart <service>  (e.g. nginx, api, frontend)" }
        Invoke-OnHost "cd $HostDir && sudo docker compose restart $Target" "Restart $Target"
    }

    'logs' {
        if (-not $Target) { throw "Usage : ec2.ps1 logs <service> [-Tail N]" }
        Invoke-OnHost "cd $HostDir && sudo docker compose logs --tail=$Tail $Target" "Logs $Target (tail $Tail)"
    }

    'instance-status' {
        $iid = Resolve-InstanceId
        Write-Step "State of $iid"
        & aws ec2 describe-instances `
            --region $Region --profile $Profile --instance-ids $iid `
            --query 'Reservations[].Instances[].{state:State.Name,type:InstanceType,publicIp:PublicIpAddress,launch:LaunchTime}' `
            --output table
    }

    'instance-stop' {
        $iid = Resolve-InstanceId
        Write-Step "Stopping instance $iid (cuts the compute bill)"
        Write-Warn "The containers stop with the instance. EBS + EIP + KMS/Route53 keep billing (~\$7-8/mo)."
        & aws ec2 stop-instances --region $Region --profile $Profile --instance-ids $iid `
            --query 'StoppingInstances[].{id:InstanceId,from:PreviousState.Name,to:CurrentState.Name}' --output table
        Write-Ok "Stop request sent. Check with : ec2.ps1 instance-status"
    }

    'instance-start' {
        $iid = Resolve-InstanceId
        Write-Step "Starting instance $iid"
        & aws ec2 start-instances --region $Region --profile $Profile --instance-ids $iid `
            --query 'StartingInstances[].{id:InstanceId,from:PreviousState.Name,to:CurrentState.Name}' --output table
        Write-Ok "Start request sent. The public IP may change (unless EIP). Wait ~1min then : ec2.ps1 health"
    }
}
