<#
.SYNOPSIS
  Claude Code PreToolUse hook : bloque toute commande Bash / PowerShell qui
  afficherait en clair la valeur d'un secret fx-vol.

.DESCRIPTION
  Regle documentee en tete de CLAUDE.md. Ce hook est une defense en profondeur :
  meme si Claude violait la regle, la commande serait bloquee avant execution.

  Recoit le tool input via stdin JSON (format Claude Code hooks).
  Exit 2 = tool bloque, Exit 0 = tool autorise.
#>

$ErrorActionPreference = 'Stop'

$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }

try {
    $payload = $raw | ConvertFrom-Json
} catch {
    # Input mal forme : on laisse passer (le hook ne doit pas casser un tool ok)
    exit 0
}

$cmd = ''
if ($payload.tool_input.command) { $cmd = $payload.tool_input.command }
elseif ($payload.tool_input.script) { $cmd = $payload.tool_input.script }
if (-not $cmd) { exit 0 }

# Patterns interdits : toute forme d'affichage direct d'un secret.
# Les autoriser casserait le principe de CLAUDE.md (zero exposition en sortie de tool).
$forbidden = @(
    # Echo direct des vars sensibles (accepte .Length/:length/${#VAR} en PS)
    '(?i)(echo|Write-Host|Write-Output)\s+\$env:(IB_USERID|IB_PASSWORD|DB_PASSWORD|VNC_PASSWORD)(?!\.Length)',
    '(?i)(echo|printf)\s+"?\$(IB_USERID|IB_PASSWORD|DB_PASSWORD|VNC_PASSWORD)"?(?!\})',
    # Dump complet de l env
    '(?i)\bprintenv\b',
    '(?i)Get-ChildItem\s+Env:',
    '(?i)\benv\b\s*$',
    '(?i)\benv\b\s*\|',
    # Lecture directe de fichiers contenant des secrets
    '(?i)\bcat\s+\.env\b',
    '(?i)\btype\s+\.env\b',
    '(?i)Get-Content\s+.*\.env\b',
    '(?i)\bcat\s+/run/fxvol\.env\b',
    '(?i)\bcat\s+.*\.aws[\\/]credentials\b',
    '(?i)Get-Content\s+.*\.aws[\\/]credentials\b',
    # SSM get-parameter qui remonterait la valeur (pas de --query restrictif)
    '(?i)aws\s+ssm\s+get-parameters?\b(?:(?!--query).)*--with-decryption(?:(?!--query).)*$'
)

foreach ($pattern in $forbidden) {
    if ($cmd -match $pattern) {
        # Ecrit un message en stderr, Claude Code le renverra au modele
        [Console]::Error.WriteLine("BLOCKED by block_secrets.ps1 : command matches forbidden pattern")
        [Console]::Error.WriteLine("Pattern   : $pattern")
        [Console]::Error.WriteLine("Command   : $cmd")
        [Console]::Error.WriteLine("Reference : CLAUDE.md section 'Regle absolue : zero exposition des secrets'")
        exit 2
    }
}

exit 0
