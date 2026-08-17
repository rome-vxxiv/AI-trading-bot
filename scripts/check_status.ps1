# ==============================================================
# One-shot health check. Run any time to answer "is the bot good?"
#   powershell -ExecutionPolicy Bypass -File .\scripts\check_status.ps1
# ==============================================================

$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path $PSScriptRoot -Parent)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " capital-agent status check" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Write-Host ""
Write-Host "[1] Process" -ForegroundColor Yellow
$proc = Get-Process python -ErrorAction SilentlyContinue
if ($proc) {
    $proc | ForEach-Object { Write-Host "  RUNNING  pid=$($_.Id)  started=$($_.StartTime)" -ForegroundColor Green }
} else {
    Write-Host "  NOT RUNNING — start it with .\run_scheduler.ps1" -ForegroundColor Red
}

Write-Host ""
Write-Host "[2] Health API" -ForegroundColor Yellow
try {
    $h = Invoke-RestMethod http://127.0.0.1:8080/healthz -TimeoutSec 3
    Write-Host "  OK  $($h.ts)" -ForegroundColor Green
    $status = Invoke-RestMethod http://127.0.0.1:8080/status -TimeoutSec 3
    Write-Host "  kill_switch.active = $($status.kill_switch.active)" -ForegroundColor $(if ($status.kill_switch.active) { "Red" } else { "Green" })
    Write-Host "  mcp_session.logged_in = $($status.mcp_session.logged_in)"
    Write-Host "  local_position_count = $($status.local_position_count)"
    Write-Host "  jobs:"
    $status.jobs | ForEach-Object { Write-Host "    $($_.id) -> next: $($_.next_run_time)" }
} catch {
    Write-Host "  NOT RESPONDING — scheduler isn't up (or health API port busy)" -ForegroundColor Red
}

Write-Host ""
Write-Host "[3] Last 10 log events" -ForegroundColor Yellow
if (Test-Path .\logs\agent.jsonl) {
    Get-Content .\logs\agent.jsonl -Tail 10 | ForEach-Object {
        try {
            $j = $_ | ConvertFrom-Json
            $color = "White"
            if ($j.level -eq "error")   { $color = "Red" }
            if ($j.level -eq "warning") { $color = "Yellow" }
            Write-Host "  $($j.timestamp)  $($j.event)" -ForegroundColor $color
        } catch { }
    }
} else {
    Write-Host "  No logs yet." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[4] Recent strategy decisions (from state.db)" -ForegroundColor Yellow
if ((Test-Path .\state\state.db) -and (Test-Path .\.venv\Scripts\python.exe)) {
    & .\.venv\Scripts\python.exe -c @"
import sqlite3
c = sqlite3.connect('state/state.db')
rows = c.execute('SELECT ts, epic, decision, reason FROM signals ORDER BY ts DESC LIMIT 5').fetchall()
if not rows:
    print('  (no signals recorded yet)')
for ts, epic, dec, reason in rows:
    print(f'  {ts}  {epic:10s} {dec:15s} {reason[:70]}')
"@
} else {
    Write-Host "  state.db not found yet." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[5] Equity snapshot" -ForegroundColor Yellow
if (Test-Path .\state\state.db) {
    & .\.venv\Scripts\python.exe -c @"
import sqlite3
c = sqlite3.connect('state/state.db')
row = c.execute('SELECT ts, equity, hwm, daily_start_equity FROM equity_snapshots ORDER BY ts DESC LIMIT 1').fetchone()
if row:
    ts, eq, hwm, day = row
    dd = (hwm - eq) / hwm * 100 if hwm else 0
    print(f'  {ts}  equity={eq:.2f}  hwm={hwm:.2f}  drawdown={dd:.2f}%')
else:
    print('  (no equity snapshots yet)')
"@
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
