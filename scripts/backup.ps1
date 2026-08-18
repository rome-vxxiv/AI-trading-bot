# Windows equivalent of backup.sh. Copies state.db (SQLite handles
# concurrent readers fine for a hot file) and gzips it. Run from Task
# Scheduler nightly.
#
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path $PSScriptRoot -Parent)

$stateDb  = ".\state\state.db"
$backupDir = Join-Path $HOME "capital-agent-backups"
$retention = 30

if (-not (Test-Path $stateDb)) {
    Write-Host "[X] state.db not found" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$out = Join-Path $backupDir "state-$stamp.db"

Copy-Item $stateDb $out -Force
Compress-Archive -Path $out -DestinationPath "$out.zip" -Force
Remove-Item $out

Get-ChildItem $backupDir -Filter "state-*.db.zip" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$retention) } |
    Remove-Item -Force

Write-Host "wrote $out.zip"
