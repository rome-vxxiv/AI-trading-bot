# ==============================================================
# GO LIVE ceremony (Windows). Flips CAP_DRY_RUN=false AND
# I_UNDERSTAND_LIVE_RISK=YES in .env. Even on DEMO account this
# means real preview -> execute calls open real positions.
#
# Undo with:  .\run_go_demo.ps1
# ==============================================================

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[X] .venv missing." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Red
Write-Host " GO-LIVE: flipping CAP_DRY_RUN=false + LIVE_RISK=YES"          -ForegroundColor Red
Write-Host " Even on demo, REAL orders will hit Capital.com."              -ForegroundColor Red
Write-Host " Restart the scheduler after this completes."                  -ForegroundColor Red
Write-Host "============================================================" -ForegroundColor Red
Write-Host ""

& .\.venv\Scripts\python.exe -m capital_agent go-live
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "go-live exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
