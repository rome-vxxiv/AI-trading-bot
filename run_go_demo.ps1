# ==============================================================
# GO DEMO — restore CAP_DRY_RUN=true + I_UNDERSTAND_LIVE_RISK=NO.
# Safe to run any time. Restart the scheduler after.
# ==============================================================

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}
Set-Location -Path $PSScriptRoot

& .\.venv\Scripts\python.exe -m capital_agent go-demo
Read-Host "Press Enter to close"
