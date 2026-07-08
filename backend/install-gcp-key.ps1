# Install a new GCP service account JSON key (Cursor cannot edit secret JSON files).
# Usage:
#   .\install-gcp-key.ps1 "C:\Users\YOU\Downloads\your-project-abc123.json"
param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath
)

$ErrorActionPreference = "Stop"
$BackendDir = $PSScriptRoot
$TargetPath = Join-Path $BackendDir "gcp-sa-config.json"

if (-not (Test-Path $SourcePath)) {
    Write-Error "Source file not found: $SourcePath"
}

Copy-Item -Path $SourcePath -Destination $TargetPath -Force

$key = Get-Content $TargetPath -Raw | ConvertFrom-Json
Write-Host ""
Write-Host "Key installed to:" $TargetPath
Write-Host "Service account:" $key.client_email
Write-Host "Project:" $key.project_id
Write-Host ""
Write-Host "Next: restart the backend (uvicorn) and check http://127.0.0.1:8000/setup/status"
