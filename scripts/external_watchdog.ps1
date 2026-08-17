# ==============================================================
# STANDALONE external watchdog. Deliberately has ZERO dependency on
# the bot's own Python process — it only needs PowerShell + your .env
# file. This is what still works if the scheduler crashes outright,
# the PC loses power and doesn't come back cleanly, or Windows kills
# the process during an update. The in-process watchdog
# (scheduler/watchdog.py) cannot alert on those cases because the code
# that would send the alert is dead along with the process it's
# supposed to be watching.
#
# What it does, once per run:
#   - reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID directly from .env
#   - hits http://127.0.0.1:8080/healthz
#   - if healthy: does nothing (silent — no spam on every check)
#   - if unreachable: sends ONE Telegram message, but only if it
#     hasn't already sent one in the last $CooldownMinutes (state
#     tracked in a small marker file so repeated failed checks don't
#     spam you every 5 minutes)
#
# SETUP — run this once to schedule it via Windows Task Scheduler:
#
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#       -Argument "-ExecutionPolicy Bypass -File `"$PWD\scripts\external_watchdog.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Once (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::MaxValue)
#   Register-ScheduledTask -TaskName "CapitalAgentWatchdog" -Action $action -Trigger $trigger -Description "Alerts if capital-agent scheduler goes unreachable"
#
# Task Scheduler runs this independently of whether the bot's own
# PowerShell window is open or closed, and survives the bot process
# crashing (though NOT a full PC power-off — nothing can alert you
# about that except a UPS or a genuinely external monitor).
# ==============================================================

param(
    [int]$CooldownMinutes = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) { exit 0 }   # nothing configured, nothing to do

$token = $null
$chat  = $null
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^TELEGRAM_BOT_TOKEN=(.+)$') { $token = $Matches[1].Trim() }
    if ($_ -match '^TELEGRAM_CHAT_ID=(.+)$')   { $chat  = $Matches[1].Trim() }
}
if (-not $token -or -not $chat) { exit 0 }   # Telegram not configured

$markerFile = Join-Path $root "state\.watchdog_last_alert"

function Send-Telegram($text) {
    try {
        Invoke-RestMethod -Method Post "https://api.telegram.org/bot$token/sendMessage" `
            -Body @{ chat_id = $chat; text = $text } -TimeoutSec 10 | Out-Null
    } catch {
        # Nothing more we can do — even the alert channel is unreachable.
    }
}

$healthy = $false
try {
    Invoke-RestMethod http://127.0.0.1:8080/healthz -TimeoutSec 5 | Out-Null
    $healthy = $true
} catch {
    $healthy = $false
}

if ($healthy) {
    # Recovered? If we'd previously alerted, send a one-time all-clear
    # and clear the marker so the next outage alerts fresh.
    if (Test-Path $markerFile) {
        Send-Telegram "capital-agent: scheduler is back and responding to /healthz."
        Remove-Item $markerFile -Force
    }
    exit 0
}

# Unhealthy. Debounce: only alert once per $CooldownMinutes.
$shouldAlert = $true
if (Test-Path $markerFile) {
    $last = Get-Item $markerFile | Select-Object -ExpandProperty LastWriteTime
    if (((Get-Date) - $last).TotalMinutes -lt $CooldownMinutes) {
        $shouldAlert = $false
    }
}

if ($shouldAlert) {
    $procRunning = [bool](Get-Process python -ErrorAction SilentlyContinue)
    $detail = if ($procRunning) { "python.exe is running but not answering — likely hung or health API port conflict." }
              else { "python.exe is NOT running — the scheduler has stopped or crashed." }
    Send-Telegram "capital-agent ALERT: health check failed. $detail Check the PC / restart run_scheduler.ps1."
    New-Item -ItemType File -Path $markerFile -Force | Out-Null
}
