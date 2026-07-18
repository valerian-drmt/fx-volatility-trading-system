<#
.SYNOPSIS
  Commande UNIQUE pour piloter le stack fx-vol LIVE sur EC2 (Windows / PowerShell).

.DESCRIPTION
  Pendant gauche/droite de start_stack.ps1 (qui gere le LOCAL). Ce script gere le
  HOST EC2 de prod sans avoir a retenir les commandes aws/gh/ssm a la main.

  Deux familles d'actions :
    * CONTROLE INSTANCE (facturation compute)
        instance-status / instance-stop / instance-start
    * CONTROLE CONTAINERS + DEPLOY (sur l'host, via SSM Session Manager - port 22 ferme)
        connect / deploy / ps / up / down / restart / logs / alembic / health

  L'host est joint par AWS SSM (jamais SSH). L'instance est resolue par tag
  Name=fxvol-prod (override via -InstanceId). Aucun secret n'est lu ni affiche :
  le .env vit sur l'host (rendu par deploy.yml depuis SSM) et docker compose le
  charge tout seul depuis /opt/fxvol.

  Pre-requis cote laptop :
    - AWS CLI v2 + Session Manager plugin (pour 'connect')
    - gh CLI authentifie (pour 'deploy')
    - profil AWS 'admin' (override via -Profile)

.PARAMETER Action
  connect          Shell interactif sur l'host (SSM Session Manager).
  deploy           Declenche le workflow GitHub 'deploy-prod' (build->ghcr->host).
                   Avec -Sha : rollback/deploie un commit precis (deploy_sha).
  health           GET https://<Domain>/fx-volatility-trading-system/api/v1/health depuis le laptop.
  ps               Liste les containers tournant sur l'host.
  up               docker compose up -d sur l'host (relance le stack).
  down             docker compose down sur l'host (stoppe les containers, garde l'instance).
  restart <svc>    docker compose restart <svc> (ex: nginx, api, frontend).
  logs <svc>       Tail des logs d'un service (-Tail N, defaut 80).
  alembic          docker compose exec api alembic upgrade head.
  instance-status  Etat EC2 (running/stopped) + IP publique.
  instance-stop    Arrete l'INSTANCE (coupe la facture compute ~$15/mo). Stoppe aussi les containers.
  instance-start   Demarre l'instance (le systemd unit re-up le stack au boot si enable).

.PARAMETER Target
  Nom du service pour 'logs' / 'restart' (ex: nginx, api, frontend, vol-engine).

.PARAMETER Sha
  Avec 'deploy' : commit SHA a deployer (rollback). Sinon = dernier build de main.

.PARAMETER InstanceId
  Override de l'instance (sinon resolue par tag Name=fxvol-prod).

.PARAMETER Region   Defaut eu-west-1.
.PARAMETER Profile  Profil AWS CLI. Defaut admin.
.PARAMETER Domain   Defaut valeriandarmente.dev.
.PARAMETER Repo     Repo GitHub pour 'deploy'. Defaut valerian-drmt/fx-volatility-trading-system.
.PARAMETER Tail     Avec 'logs' : nombre de lignes. Defaut 80.

.EXAMPLE
  .\scripts\ops\ec2.ps1 health                 # le site repond-il ?
  .\scripts\ops\ec2.ps1 deploy                 # deploie main HEAD (apres merge)
  .\scripts\ops\ec2.ps1 deploy -Sha 1a2b3c4    # rollback sur un ancien commit
  .\scripts\ops\ec2.ps1 ps                      # containers up sur l'host
  .\scripts\ops\ec2.ps1 logs nginx -Tail 120    # logs nginx
  .\scripts\ops\ec2.ps1 restart api             # restart un service
  .\scripts\ops\ec2.ps1 connect                 # shell interactif sur l'host
  .\scripts\ops\ec2.ps1 down                    # stoppe les containers (instance reste up)
  .\scripts\ops\ec2.ps1 instance-stop           # COUPE LE COUT compute (arrete l'instance)
  .\scripts\ops\ec2.ps1 instance-start          # relance l'instance
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet(
        'connect', 'deploy', 'health', 'ps', 'up', 'down', 'restart', 'logs',
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

$HostDir = '/opt/fxvol'   # ou vit docker-compose.yml + .env sur l'host

# ---------- Resolution de l'instance par tag (sauf si -InstanceId) ----------
function Resolve-InstanceId {
    if ($InstanceId) { return $InstanceId }
    Write-Step "Resolution de l'instance (tag Name=fxvol-prod)"
    $id = (& aws ec2 describe-instances `
            --region $Region --profile $Profile `
            --filters 'Name=tag:Name,Values=fxvol-prod' `
                      'Name=instance-state-name,Values=pending,running,stopping,stopped' `
            --query 'Reservations[].Instances[].InstanceId | [0]' `
            --output text).Trim()
    if (-not $id -or $id -eq 'None') {
        throw "Aucune instance avec tag Name=fxvol-prod (region $Region, profil $Profile). Passe -InstanceId."
    }
    Write-Ok "Instance : $id"
    return $id
}

# ---------- Helper SSM : envoie une commande shell sur l'host, attend, imprime ----------
# SSM RunShellScript tourne sous /bin/sh (dash) : pas de 'set -o pipefail'.
# La commande est jouee dans un seul shell -> 'cd /opt/fxvol && ...' OK.
function Invoke-OnHost {
    param([string]$ShellCommand, [string]$Label)

    $iid = Resolve-InstanceId
    if (-not $Label) { $Label = "Execution sur l'host : $ShellCommand" }
    Write-Step $Label

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
    if (-not $cmdId) { throw "send-command n'a pas retourne de CommandId" }

    & aws ssm wait command-executed `
        --region $Region --profile $Profile `
        --command-id $cmdId --instance-id $iid 2>$null
    # 'wait command-executed' sort != 0 si le script host renvoie != 0 : on imprime
    # quand meme stdout/stderr (plus utile que l'echec du wait lui-meme).

    $inv = (& aws ssm get-command-invocation `
            --region $Region --profile $Profile `
            --command-id $cmdId --instance-id $iid `
            --query '{status:Status,out:StandardOutputContent,err:StandardErrorContent}' `
            --output json) | ConvertFrom-Json

    if ($inv.out) { Write-Host $inv.out }
    if ($inv.err) { Write-Warn $inv.err.Trim() }
    if ($inv.status -ne 'Success') {
        Write-Warn "Status SSM : $($inv.status)"
    } else {
        Write-Ok "OK"
    }
}

switch ($Action) {

    'connect' {
        $iid = Resolve-InstanceId
        Write-Step "Shell interactif sur $iid (Ctrl-D / 'exit' pour sortir)"
        Write-Warn "Rappel : 'cd $HostDir' avant tout 'sudo docker compose ...'"
        & aws ssm start-session --region $Region --profile $Profile --target $iid
    }

    'deploy' {
        Write-Step "Declenche le workflow deploy-prod"
        if ($Sha) {
            Write-Warn "Deploy d'un SHA precis (rollback) : $Sha"
            & gh workflow run deploy-prod --repo $Repo -f "deploy_sha=$Sha"
        } else {
            & gh workflow run deploy-prod --repo $Repo
        }
        Write-Ok "Lance. Suivi : gh run watch --repo $Repo  (ou gh run list --repo $Repo)"
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
            Write-Warn "Echec : $($_.Exception.Message)"
            exit 1
        }
    }

    'ps'      { Invoke-OnHost "cd $HostDir && sudo docker compose ps" "Containers sur l'host" }
    'up'      { Invoke-OnHost "cd $HostDir && sudo docker compose up -d" "docker compose up -d" }
    'down'    { Invoke-OnHost "cd $HostDir && sudo docker compose down" "docker compose down (containers stoppes, instance reste up)" }
    'alembic' { Invoke-OnHost "cd $HostDir && sudo docker compose exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head" "Alembic upgrade head" }

    'restart' {
        if (-not $Target) { throw "Usage : ec2.ps1 restart <service>  (ex: nginx, api, frontend)" }
        Invoke-OnHost "cd $HostDir && sudo docker compose restart $Target" "Restart $Target"
    }

    'logs' {
        if (-not $Target) { throw "Usage : ec2.ps1 logs <service> [-Tail N]" }
        Invoke-OnHost "cd $HostDir && sudo docker compose logs --tail=$Tail $Target" "Logs $Target (tail $Tail)"
    }

    'instance-status' {
        $iid = Resolve-InstanceId
        Write-Step "Etat de $iid"
        & aws ec2 describe-instances `
            --region $Region --profile $Profile --instance-ids $iid `
            --query 'Reservations[].Instances[].{state:State.Name,type:InstanceType,publicIp:PublicIpAddress,launch:LaunchTime}' `
            --output table
    }

    'instance-stop' {
        $iid = Resolve-InstanceId
        Write-Step "Arret de l'instance $iid (coupe la facture compute)"
        Write-Warn "Les containers s'arretent avec l'instance. EBS + EIP + KMS/Route53 continuent (~\$7-8/mo)."
        & aws ec2 stop-instances --region $Region --profile $Profile --instance-ids $iid `
            --query 'StoppingInstances[].{id:InstanceId,from:PreviousState.Name,to:CurrentState.Name}' --output table
        Write-Ok "Demande d'arret envoyee. Verifie avec : ec2.ps1 instance-status"
    }

    'instance-start' {
        $iid = Resolve-InstanceId
        Write-Step "Demarrage de l'instance $iid"
        & aws ec2 start-instances --region $Region --profile $Profile --instance-ids $iid `
            --query 'StartingInstances[].{id:InstanceId,from:PreviousState.Name,to:CurrentState.Name}' --output table
        Write-Ok "Demande de demarrage envoyee. L'IP publique peut changer (sauf EIP). Patiente ~1min puis : ec2.ps1 health"
    }
}
