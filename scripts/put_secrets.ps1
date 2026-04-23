<#
.SYNOPSIS
  Setup / rotation des secrets fx-vol dans AWS SSM Parameter Store.

.DESCRIPTION
  Prompt interactif (saisie masquee) pour chaque parametre SecureString, puis
  ssm put-parameter --overwrite chiffre par la CMK alias/fxvol-secrets.

  Usage typique :
  - Setup initial : remplace les PLACEHOLDER_TO_REPLACE creees par le bootstrap
  - Rotation : passer -Only IB_PASSWORD pour ne re-pousser qu'un parametre

.PARAMETER Profile
  Profil AWS CLI. Defaut : fxvol-dev.

.PARAMETER Region
  Region AWS. Defaut : eu-west-1.

.PARAMETER Only
  Nom d'un parametre unique a pousser (sans le prefixe /fxvol/prod/).
  Defaut : pousse les 4 SecureString.

.EXAMPLE
  .\scripts\put_secrets.ps1                        # pousse les 4 secrets
  .\scripts\put_secrets.ps1 -Only IB_PASSWORD      # rotation d'un seul
#>
param(
    [string]$Profile = 'fxvol-dev',
    [string]$Region = 'eu-west-1',
    [string]$Only = ''
)

$ErrorActionPreference = 'Stop'

$allSecrets = @('IB_USERID', 'IB_PASSWORD', 'DB_PASSWORD', 'VNC_PASSWORD')
$targets = if ($Only) {
    if ($Only -notin $allSecrets) { throw "Unknown secret '$Only'. Known : $($allSecrets -join ', ')" }
    @($Only)
} else { $allSecrets }

Write-Host "==> Pushing $($targets.Count) secret(s) to SSM (profile=$Profile, region=$Region)" -ForegroundColor Cyan

foreach ($name in $targets) {
    $secure = Read-Host -Prompt "Value for /fxvol/prod/$name" -AsSecureString
    $plain = [System.Net.NetworkCredential]::new('', $secure).Password
    if ([string]::IsNullOrWhiteSpace($plain)) {
        Write-Host "  Skipping $name (empty input)" -ForegroundColor Yellow
        continue
    }

    aws ssm put-parameter `
        --name "/fxvol/prod/$name" `
        --value $plain `
        --type SecureString `
        --key-id alias/fxvol-secrets `
        --overwrite `
        --profile $Profile --region $Region | Out-Null

    if ($LASTEXITCODE -ne 0) { throw "put-parameter failed for $name" }
    Write-Host "  [OK] /fxvol/prod/$name pushed" -ForegroundColor Green

    # Scrub la valeur en clair de la RAM des que possible
    Remove-Variable plain -ErrorAction SilentlyContinue
}

Write-Host "==> Done. Run .\scripts\load_secrets.ps1 to refresh shell env." -ForegroundColor Cyan
