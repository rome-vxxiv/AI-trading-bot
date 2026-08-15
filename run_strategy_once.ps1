# ==============================================================
# One-shot strategy launcher (Windows) - fires the RSI mean-reversion
# playbook against $Epic. Preview flow only; execute is blocked at three
# layers (allowedTools whitelist, disallowedTools blocklist, CAP_DRY_RUN).
#
# Usage: right-click -> Run with PowerShell (defaults GOLD)
#   Or:  powershell -ExecutionPolicy Bypass -File .\run_strategy_once.ps1 -Epic BTCUSD
# ==============================================================
param(
    [string]$Epic = "GOLD",
    [string]$Strategy = "rsi_mean_reversion"
)

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[X] .venv missing. Run run_scheduler.ps1 first to install." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "[X] claude CLI not on PATH. Run run_scheduler.ps1 first to install Claude Code." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

# Safety check: CAP_DRY_RUN must be true for strategy playbooks in step 4.
$dryRun = Select-String -Path .env -Pattern '^CAP_DRY_RUN=true' -Quiet
if (-not $dryRun) {
    Write-Host "[X] CAP_DRY_RUN must be 'true' in .env for step 4 strategies." -ForegroundColor Red
    Write-Host "    Edit .env and set CAP_DRY_RUN=true, then re-run." -ForegroundColor Yellow
    Read-Host "Press Enter to close"; exit 1
}

Write-Host "[+] Firing strategy '$Strategy' on $Epic (CAP_DRY_RUN=true)..." -ForegroundColor Green
& .\.venv\Scripts\python.exe -m capital_agent strategy-once --epic $Epic --strategy $Strategy
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "strategy-once exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
