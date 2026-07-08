# Build one paste-ready env block for Render "Add from .env".
# Usage (from backend folder):
#   .\export-render-env.ps1
#   .\export-render-env.ps1 -CorsOrigin "https://nexa-1-9end.onrender.com"
#   .\export-render-env.ps1 -CopyToClipboard
param(
    [string]$CorsOrigin = "https://nexa-1-9end.onrender.com",
    [string]$EnvFile = ".env",
    [string]$KeyFile = "gcp-sa-config.json",
    [string]$OutFile = "..\render-paste.env",
    [switch]$CopyToClipboard
)

$ErrorActionPreference = "Stop"
$BackendDir = $PSScriptRoot
$EnvPath = Join-Path $BackendDir $EnvFile
$KeyPath = Join-Path $BackendDir $KeyFile
$OutPath = [System.IO.Path]::GetFullPath((Join-Path $BackendDir $OutFile))

if (-not (Test-Path $EnvPath)) {
    Write-Error "Missing $EnvPath - copy .env.example to .env first."
}
if (-not (Test-Path $KeyPath)) {
    Write-Error "Missing $KeyPath - run .\install-gcp-key.ps1 with your GCP JSON."
}

$skipKeys = @{
    DATABASE_URL = $true
    GCP_SA_KEY_FILE = $true
    GCP_SA_KEY_JSON = $true
    CORS_ORIGINS = $true
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# Paste in Render: Environment -> Add from .env")
$lines.Add("# Do NOT add DATABASE_URL (Render sets it from Postgres).")
$lines.Add("")

Get-Content $EnvPath | ForEach-Object {
    $line = $_.TrimEnd()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $key = $line.Substring(0, $eq).Trim()
    if ($skipKeys.ContainsKey($key)) { return }
    $lines.Add($line)
}

$keyJson = Get-Content $KeyPath -Raw | ConvertFrom-Json | ConvertTo-Json -Compress -Depth 20
$lines.Add("GCP_SA_KEY_JSON=$keyJson")
$lines.Add("CORS_ORIGINS=$CorsOrigin")

$defaults = [ordered]@{
    JWT_SECRET = "change-me-run-openssl-rand-hex-32"
    JWT_EXPIRE_MINUTES = "10080"
    ADMIN_EMAIL = "admin@example.com"
    ADMIN_PASSWORD = "change-me"
    DEFAULT_USER_CREDITS = "100"
    CREDITS_PER_GB = "1"
    ASK_DEBUG_LOG = "false"
}
$existing = @{}
foreach ($line in $lines) {
    if ($line -match '^([A-Z_][A-Z0-9_]*)=') { $existing[$Matches[1]] = $true }
}
foreach ($key in $defaults.Keys) {
    if (-not $existing.ContainsKey($key)) {
        $lines.Add("$key=$($defaults[$key])")
    }
}

$text = ($lines -join "`n") + "`n"
Set-Content -Path $OutPath -Value $text -Encoding UTF8

Write-Host ""
Write-Host "Wrote: $OutPath"
Write-Host "CORS_ORIGINS = $CorsOrigin"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Open render-paste.env and set ADMIN_EMAIL / ADMIN_PASSWORD / JWT_SECRET"
Write-Host "  2. Render -> Environment -> Add from .env -> paste all -> Save, rebuild, deploy"

if ($CopyToClipboard) {
    Set-Clipboard -Value $text
    Write-Host "  (copied to clipboard)"
}
