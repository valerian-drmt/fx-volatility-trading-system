<#
.SYNOPSIS
  Fetch les secrets fx-vol depuis AWS SSM Parameter Store (/fxvol/prod/*) et
  les injecte dans la session PowerShell courante en tant que variables d'env.

.DESCRIPTION
  Source unique de verite : AWS SSM. Aucun .env n'est ecrit sur disque.
  Les secrets vivent uniquement en RAM dans la session shell. Docker Compose
  les recupere ensuite via l'heritage d'env du process parent.

  Compose egalement DATABASE_URL, REDIS_URL, PYTHONPATH (vars non-secretes
  derivables, pas stockees en SSM pour eviter la duplication).

.PARAMETER Profile
  Profil AWS CLI a utiliser. Defaut : fxvol-dev.

.PARAMETER Region
  Region AWS. Defaut : eu-west-1.

.EXAMPLE
  .\scripts\load_secrets.ps1
  .\scripts\load_secrets.ps1 -Profile fxvol-dev
#>
param(
    [string]$Profile = 'fxvol-dev',
    [string]$Region = 'eu-west-1'
)

$ErrorActionPreference = 'Stop'

# 1. Verifier que le profil AWS est utilisable (SSO valide ou access keys OK)
$identity = $null
try {
    $identity = aws sts get-caller-identity --profile $Profile --output json 2>$null | ConvertFrom-Json
} catch {}
if (-not $identity) {
    Write-Host "AWS profile '$Profile' not usable." -ForegroundColor Red
    Write-Host "  - If SSO : run 'aws sso login --profile $Profile'" -ForegroundColor Yellow
    Write-Host "  - If static keys : check ~/.aws/credentials" -ForegroundColor Yellow
    throw "AWS authentication required"
}
Write-Host "==> AWS identity : $($identity.Arn)" -ForegroundColor DarkGray

# 2. Fetch tous les parametres /fxvol/prod/* en une requete
$json = aws ssm get-parameters-by-path `
    --path /fxvol/prod `
    --with-decryption `
    --profile $Profile --region $Region `
    --output json
if ($LASTEXITCODE -ne 0) { throw "ssm get-parameters-by-path failed" }

$params = ($json | ConvertFrom-Json).Parameters
if (-not $params -or $params.Count -eq 0) {
    throw "No parameters found under /fxvol/prod/ - run put_secrets.ps1 first"
}

# 3. Export en $env:*
$placeholders = @()
foreach ($p in $params) {
    $key = $p.Name -replace '^/fxvol/prod/', ''
    Set-Item -Path "Env:$key" -Value $p.Value
    if ($p.Value -eq 'PLACEHOLDER_TO_REPLACE') { $placeholders += $key }
}

# 4. Derived vars (non-secretes mais dependantes des secrets)
$env:DATABASE_URL = "postgresql+asyncpg://fxvol:$($env:DB_PASSWORD)@localhost:5433/fxvol"
$env:REDIS_URL = "redis://localhost:6380/0"
$env:PYTHONPATH = "src"

# 5. Report
Write-Host "==> Loaded $($params.Count) secrets from SSM into shell env" -ForegroundColor Green
if ($placeholders.Count -gt 0) {
    Write-Host "==> WARNING : $($placeholders.Count) parametre(s) ont encore la valeur PLACEHOLDER_TO_REPLACE :" -ForegroundColor Yellow
    $placeholders | ForEach-Object { Write-Host "      - $_" -ForegroundColor Yellow }
    Write-Host "    Executer .\scripts\put_secrets.ps1 pour pousser les vraies valeurs." -ForegroundColor Yellow
}
