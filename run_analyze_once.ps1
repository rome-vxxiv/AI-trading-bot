# ==============================================================
# One-shot analysis launcher (Windows) - fires the read-only Claude Code
# analysis against $epic ignoring session gating. Handy for testing when
# markets are closed (e.g. GOLD on weekends).
#
# Usage: right-click -> Run with PowerShell (defaults: GOLD, readonly_manual)
#   Or:  powershell -ExecutionPolicy Bypass -File .\run_analyze_once.ps1 -Epic BTCUSD
# ==============================================================
param(
    [string]$Epic = "GOLD",
    [string]$Strategy = "readonly_manual"
)

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[X] .venv missing. Run run_scheduler.ps1 first to install." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "[X] claude CLI not on PATH. Run run_scheduler.ps1 first - it installs Claude Code." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

Write-Host "[+] Firing analysis on $Epic ..." -ForegroundColor Green
& .\.venv\Scripts\python.exe -m capital_agent analyze-once --epic $Epic --strategy $Strategy
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "analyze-once exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
