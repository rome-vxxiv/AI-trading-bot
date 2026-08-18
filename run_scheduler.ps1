# ==============================================================
# capital-agent scheduler launcher (Windows)
# ==============================================================
# Usage: right-click -> Run with PowerShell, or:
#        powershell -ExecutionPolicy Bypass -File .\run_scheduler.ps1
# Ctrl+C in the window stops it cleanly.
# ==============================================================

$ErrorActionPreference = "Stop"
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}

Set-Location -Path $PSScriptRoot

function Fail($msg) {
    Write-Host ""
    Write-Host "[X] $msg" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

# ---- .env --------------------------------------------------------
if (-not (Test-Path ".env")) {
    Fail ".env missing. Copy .env.example -> .env and fill in CAP_* and ANTHROPIC_API_KEY."
}

# ---- Python venv -------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "[+] Creating .venv..." -ForegroundColor Green
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Fail "python -m venv failed. Install Python 3.11+ from python.org (tick 'Add to PATH')."
    }
}

Write-Host "[+] Installing capital-agent + dependencies (~1-2 min first run)..." -ForegroundColor Green
& .\.venv\Scripts\pip.exe install --quiet --disable-pip-version-check -e .
if ($LASTEXITCODE -ne 0) { Fail "pip install failed. See error above." }

# ---- Node.js + Claude Code CLI (needed for step 3+) --------------
$node = Get-Command node -ErrorAction SilentlyContinue
$npm  = Get-Command npm  -ErrorAction SilentlyContinue
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude -and -not $node) {
    Write-Host ""
    Write-Host "[!] Claude Code CLI is not installed, and Node.js (needed to install it) is missing." -ForegroundColor Yellow
    Write-Host "    Install Node.js LTS from https://nodejs.org/ (default options are fine)." -ForegroundColor Yellow
    Write-Host "    Then re-run this script and it will install Claude Code for you." -ForegroundColor Yellow
    Write-Host "    You can still start the scheduler now - the analysis job will log" -ForegroundColor Yellow
    Write-Host "    analysis.error events but keepalive + reconcile will run fine." -ForegroundColor Yellow
    Write-Host ""
}
elseif (-not $claude -and $npm) {
    Write-Host "[+] Installing Claude Code CLI (npm i -g @anthropic-ai/claude-code)..." -ForegroundColor Green
    & npm install -g "@anthropic-ai/claude-code"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] npm install failed. Try running PowerShell as Administrator and re-run this script." -ForegroundColor Yellow
    }
    $claude = Get-Command claude -ErrorAction SilentlyContinue
}

if ($claude) {
    Write-Host "[+] claude: $($claude.Source)" -ForegroundColor Green
    # ANTHROPIC_API_KEY check (grep-style, avoids sourcing .env into this shell)
    $hasKey = Select-String -Path .env -Pattern '^ANTHROPIC_API_KEY=.+' -Quiet
    if (-not $hasKey) {
        Write-Host ""
        Write-Host "[!] ANTHROPIC_API_KEY is empty in .env. The analysis job will fail until you set it." -ForegroundColor Yellow
        Write-Host "    Get a key at https://console.anthropic.com/settings/keys and paste it into .env." -ForegroundColor Yellow
        Write-Host ""
    }
}

# ---- Go ----------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " capital-agent scheduler starting."                            -ForegroundColor Cyan
Write-Host " Logs   -> .\logs\agent.jsonl (JSON, one per line)"            -ForegroundColor Cyan
Write-Host " State  -> .\state\state.db"                                   -ForegroundColor Cyan
Write-Host " Health -> http://127.0.0.1:8080/healthz"                      -ForegroundColor Cyan
Write-Host " Analysis: GOLD every 15 min (session-gated)."                 -ForegroundColor Cyan
Write-Host " Ctrl+C to stop."                                              -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

& .\.venv\Scripts\python.exe -m capital_agent run
$exit = $LASTEXITCODE
Write-Host ""
Write-Host "capital-agent exited with code $exit" -ForegroundColor Yellow
Read-Host "Press Enter to close"
