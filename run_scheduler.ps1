# ==============================================================
# capital-agent step-2 scheduler launcher (Windows)
# ==============================================================
# Usage: right-click -> Run with PowerShell, or:
#        powershell -ExecutionPolicy Bypass -File .\run_scheduler.ps1
# Ctrl+C in the window stops it cleanly.
# ==============================================================

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "[X] .env missing in $PSScriptRoot. Copy .env.example -> .env and fill in CAP_*." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
if (-not (Test-Path ".venv")) {
    Write-Host "[+] Creating .venv (Python must be on PATH)..." -ForegroundColor Green
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] python -m venv failed. Install Python 3.11+ from python.org and tick 'Add to PATH'." -ForegroundColor Red
        Read-Host "Press Enter to close"; exit 1
    }
}

# Install / upgrade project. `pip install -e .` picks up dependencies from pyproject.
Write-Host "[+] Installing capital-agent + dependencies (~1-2 min first run)..." -ForegroundColor Green
& .\.venv\Scripts\pip.exe install --quiet --disable-pip-version-check -e .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] pip install failed. See error above." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " capital-agent scheduler starting."                            -ForegroundColor Cyan
Write-Host " Logs -> .\logs\agent.jsonl (JSON, one per line)"              -ForegroundColor Cyan
Write-Host " State -> .\state\state.db"                                    -ForegroundColor Cyan
Write-Host " Health -> http://127.0.0.1:8080/healthz"                      -ForegroundColor Cyan
Write-Host " Ctrl+C to stop."                                              -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

& .\.venv\Scripts\python.exe -m capital_agent run
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "capital-agent exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
