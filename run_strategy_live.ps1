# ==============================================================
# LIVE strategy launcher. Uses the rsi_mean_reversion_live playbook
# variant. If RSI signal fires, Python will actually EXECUTE the
# preview on your (demo) account.
#
# Preconditions:
#   - Run run_go_live.ps1 first to flip .env fuses.
#   - Kill switch must be unlocked.
#   - GOLD session must be open (or use -Epic BTCUSD for 24/7).
# ==============================================================
param(
    [string]$Epic = "GOLD"
)

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

$dry = Select-String -Path .env -Pattern '^CAP_DRY_RUN=false' -Quiet
$fuse = Select-String -Path .env -Pattern '^I_UNDERSTAND_LIVE_RISK=YES' -Quiet
if (-not ($dry -and $fuse)) {
    Write-Host "[X] .env fuses are not set for live. Run run_go_live.ps1 first." -ForegroundColor Red
    Write-Host "    CAP_DRY_RUN=false present: $dry" -ForegroundColor Yellow
    Write-Host "    I_UNDERSTAND_LIVE_RISK=YES present: $fuse" -ForegroundColor Yellow
    Read-Host "Press Enter to close"; exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Red
Write-Host " LIVE strategy run on $Epic. Real orders will be placed"       -ForegroundColor Red
Write-Host " if a signal fires and passes all risk checks."                -ForegroundColor Red
Write-Host "============================================================" -ForegroundColor Red

Remove-Item Env:CAP_DRY_RUN -ErrorAction SilentlyContinue
Remove-Item Env:I_UNDERSTAND_LIVE_RISK -ErrorAction SilentlyContinue

& .\.venv\Scripts\python.exe -m capital_agent strategy-once --epic $Epic --strategy rsi_mean_reversion_live
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "strategy-live exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
