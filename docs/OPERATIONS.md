# Operations

Everyday operator playbook. Assumes the bot is running (Docker,
systemd, or Windows launcher).

## Health check

```bash
curl http://127.0.0.1:8080/healthz            # {"ok": true, "ts": "..."}
curl http://127.0.0.1:8080/status | jq .      # mcp_session, kill_switch, jobs, positions
curl http://127.0.0.1:8080/jobs   | jq .      # per-job next fire time
```

Same via CLI (any host that has the .venv):

```bash
capital-agent status
capital-agent jobs
```

## Checking real performance

Not a backtest — this reads what actually happened: every trade whose
outcome was tagged (win/loss/flat + realized P&L) when the position
genuinely closed at the broker.

```bash
capital-agent performance                              # everything
capital-agent performance --report-epic GOLD
capital-agent performance --report-strategy rsi_mean_reversion_live
```

```
.\run_performance_report.ps1
.\run_performance_report.ps1 -Epic GOLD
```

Early on this will mostly read `"trade_count": 0` — there's nothing to
report until trades have actually closed. That's expected, not broken.
Compare against `capital-agent backtest`'s `trade_simulation` for the
same instrument: backtest tells you what the rule would have done
historically, this tells you what it actually did. They should drift
apart somewhat (backtest doesn't model spread/slippage/financing) — if
they drift by a lot, something in execution is worth investigating.

## Kill everything

If markets are misbehaving, if you're going on vacation, or if you
just want a break:

```bash
capital-agent kill                  # reads HEALTH_API_TOKEN from env
```

or:

```bash
curl -X POST -H "X-Auth-Token: $HEALTH_API_TOKEN" \
    "http://127.0.0.1:8080/kill?reason=vacation"
```

While the switch is active, every scheduled strategy tick is rejected
at preflight with reason `kill_switch_active:vacation` and Telegram
gets an alert. Keepalive, reconciliation, and drawdown monitoring
still run — you'll still notice broker-side stop-outs.

**Resume:**

```bash
capital-agent unlock
```

## Update procedure

1. `git fetch origin && git log --oneline HEAD..origin/main` — review what changed.
2. `capital-agent kill --reason updating` — freeze trading.
3. `git pull`.
4. Docker: `docker compose build && docker compose up -d`.
   Systemd: `systemctl restart capital-agent`.
   Windows: Ctrl+C the scheduler window, re-run `.\run_scheduler.ps1`.
5. `capital-agent status` — verify `mcp_session.logged_in=true`, jobs
   registered, kill_switch still active.
6. `capital-agent unlock` — resume.
7. Watch `.\logs\agent.jsonl` (or `journalctl -fu capital-agent`) for
   the next few ticks.

## Adding a new instrument

1. Edit `config/allowlist.yaml`: add the epic (verify via `cap_market_search` first).
2. Edit `config/sessions.yaml`: add the session window and any DAILY guards. See existing entries for shape.
3. Edit `config/risk.yaml` if the instrument needs different sizing.
4. Restart the scheduler.
5. Sanity: `capital-agent analyze-once --epic <NEW_EPIC>`.

## Adding a new strategy

See [`docs/STRATEGIES.md`](STRATEGIES.md). Short version:

1. `prompts/<name>.md` — direct-imperative prompt with `{EPIC}` template.
2. `src/capital_agent/backtest/<name>.py` — Python twin of the decision rule.
3. Register a `PlaybookSpec` in `driver/runner.py`'s `PLAYBOOKS`.
4. Add golden-numbers test in `tests/test_backtest_indicators.py`.
5. Wire a scheduler job by adding an entry to `config/jobs.yaml` when ready.

## Backups

Nightly SQLite dump — see `scripts/backup.sh` (Linux) or
`scripts/backup.ps1` (Windows). Cron/Task Scheduler entry:

```
# Linux crontab (3:15 UTC nightly)
15 3 * * * /opt/capital-agent/scripts/backup.sh

# Windows Task Scheduler action
Program: powershell.exe
Arguments: -ExecutionPolicy Bypass -File C:\Users\metaa\capital-mcp-check\scripts\backup.ps1
```

Retention default 30 days. Override with `CAPITAL_AGENT_BACKUP_RETENTION_DAYS`.

## Rotating credentials

Every 90 days (or immediately after any suspected exposure):

- **Capital.com** — Settings → API integrations → delete key → create
  new → update `CAP_API_KEY` + `CAP_API_PASSWORD` in `.env`.
- **Anthropic** — console.anthropic.com/settings/keys → delete →
  create → update `ANTHROPIC_API_KEY`.
- **Telegram** — BotFather → `/mybots` → your bot → API Token → Revoke
  → update `TELEGRAM_BOT_TOKEN`.
- **Health API** — regenerate `HEALTH_API_TOKEN` in `.env`.

Restart the scheduler after each rotation.

## Escalation to live

You've been running the dry-run scheduler for at least 30 days and
the demo P&L is where you want it. Ceremony:

1. Read your last 30 days of demo results: `capital-agent performance`
   (see "Checking real performance" above) — look at `win_rate`,
   `profit_factor`, and `total_pnl` per instrument in `by_epic`, not
   just the headline number.
2. Confirm: kill switch works (`capital-agent kill && capital-agent unlock`).
3. Confirm: Telegram alerts arrive (`capital-agent kill; capital-agent unlock`).
4. **Set `CAP_ENV=live`** in `.env`. (Also update `CAP_API_KEY` /
   `CAP_IDENTIFIER` / `CAP_API_PASSWORD` to your LIVE Capital.com
   credentials, which are DIFFERENT from your demo ones.)
5. **Reduce `risk_pct_per_trade` in risk.yaml to `0.001` (0.1%)** for
   the first two weeks live.
6. `capital-agent go-live --confirm`.
7. Restart the scheduler and edit `scheduler/app.py` to point the
   scheduled job at `rsi_mean_reversion_live` (or leave manual-only).
8. Watch every tick. Every one.

Only revert to `CAP_ENV=demo` after another manual `go-demo`.
