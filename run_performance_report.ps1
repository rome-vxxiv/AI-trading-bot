# ==============================================================
# Real performance report (Windows). Reads ACTUAL closed trades from
# local history -- not a backtest -- and reports win rate/expectancy/
# P&L from what really happened. Local database only, no broker calls.
#
# Usage:
#   .\run_performance_report.ps1                          # everything
#   .\run_performance_report.ps1 -Epic GOLD                # one instrument
#   .\run_performance_report.ps1 -Strategy rsi_mean_reversion_live
# ==============================================================
param(
    [string]$Epic = "",
    [string]$Strategy = ""
)

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[X] .venv missing. Run run_scheduler.ps1 first to install." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

$args = @("-m", "capital_agent", "performance")
if ($Epic)     { $args += @("--report-epic", $Epic) }
if ($Strategy) { $args += @("--report-strategy", $Strategy) }

Write-Host "[+] Reading real trade history..." -ForegroundColor Green
& .\.venv\Scripts\python.exe @args
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "performance report exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
