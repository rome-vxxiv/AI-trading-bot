# ==============================================================
# Backtest launcher (Windows). Replays the SAME RSI-14/ATR-14 math the
# live playbook uses, against historical bars from Capital.com. Pure
# read-only, no orders.
#
# Usage:
#   .\run_backtest.ps1                            # GOLD, MINUTE_15, 400 bars
#   .\run_backtest.ps1 -Epic BTCUSD               # BTC crypto
#   .\run_backtest.ps1 -Resolution HOUR -MaxBars 500
#   .\run_backtest.ps1 -Strategy rsi_trend_filtered   # candidate under evaluation
#   .\run_backtest.ps1 -Multi -NumWindows 5           # stitch several 1000-bar
#                                                        windows into one sample
# ==============================================================
param(
    [string]$Epic = "GOLD",
    [string]$Resolution = "MINUTE_15",
    [int]   $MaxBars = 400,
    [string]$From = "",
    [string]$To   = "",
    [string]$Strategy = "rsi_mean_reversion",
    [switch]$Multi,
    [int]   $NumWindows = 4
)

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[X] .venv missing. Run run_scheduler.ps1 first to install." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

if ($Multi) {
    $args = @("-m", "capital_agent", "backtest-multi",
              "--epic", $Epic, "--resolution", $Resolution, "--max-bars", $MaxBars,
              "--strategy", $Strategy, "--num-windows", $NumWindows)
    if ($To) { $args += @("--to-iso", $To) }
    Write-Host "[+] Backtesting $Strategy on $Epic across $NumWindows windows of $MaxBars bars each..." -ForegroundColor Green
} else {
    $args = @("-m", "capital_agent", "backtest",
              "--epic", $Epic, "--resolution", $Resolution, "--max-bars", $MaxBars,
              "--strategy", $Strategy)
    if ($From) { $args += @("--from-iso", $From) }
    if ($To)   { $args += @("--to-iso",   $To)   }
    Write-Host "[+] Backtesting $Strategy on $Epic ($Resolution x $MaxBars bars)..." -ForegroundColor Green
}

& .\.venv\Scripts\python.exe @args
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "backtest exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
