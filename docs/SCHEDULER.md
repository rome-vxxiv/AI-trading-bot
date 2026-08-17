# Scheduler

Long-running Python process. In step (2) it does:

1. Loads settings, sessions, holiday calendar, job definitions, allowlist.
2. Spawns the upstream MCP as a stdio subprocess and initializes it.
3. Runs one-shot startup audits:
   - `tools/list` from MCP.
   - `cap_market_get(epic)` for each allowlisted epic to log broker-reported
     `openingHours`/status vs. our local sessions.yaml (`session_audit.market`).
4. Schedules four always-on jobs:
   - **keepalive** — every 5 min. Calls `cap_session_ping`, then
     `cap_session_status`. If `logged_in` comes back false, re-logs in.
   - **reconcile** — every 60 s. Calls `cap_trade_positions_list`,
     compares to the local `positions_local` table, adopts broker-only
     positions with `strategy_id=null`, deletes local-only ones, logs
     any field deltas. **Broker is source of truth.**
   - **daily_reset** — cron `00:00 UTC`. Logs yesterday's daily-stats row
     and seeds today's.
   - **drawdown** — every 60 s. Snapshots equity, auto-trips the kill
     switch on drawdown breach, alerts on daily-loss-cap breach.
5. Schedules strategy jobs — see below.
6. Serves the health API on `127.0.0.1:8080`.

## Strategy jobs (config-driven)

Every strategy tick is defined in `config/jobs.yaml`, not hardcoded in
Python. `scheduler/jobs_config.py` loads it into a list of `JobDef`s at
boot; `scheduler/app.py` registers one `CronTrigger` job per entry.
Adding an instrument to an existing strategy/cadence is a config-only
change — no code required.

```yaml
tiers:
  fast:   { minutes: 5  }
  medium: { minutes: 15 }
  slow:   { minutes: 60 }

jobs:
  - id: rsi_gold_15m
    enabled: true
    strategy: rsi_mean_reversion
    epic: GOLD
    tier: medium
```

`tiers:` is the real source of cadence — `minutes` controls the
cron-minute spacing within each hour and must divide evenly into 60.
Current default: all six instruments (GOLD, BTCUSD, US500, GOOGL, MSFT,
NVDA) on the `medium` (15-min) tier, all running `rsi_mean_reversion`
(dry-run/preview-only — see `docs/RISK.md` for what it takes to flip an
instrument to `rsi_mean_reversion_live`).

Each tick is gated, in order, inside `scheduler/jobs/analysis.py`
(`_run_gated`) — an enabled job still correctly no-ops most ticks when
its instrument's market is shut:

1. Epic must be present in `sessions.yaml`.
2. Session must be open (`is_open`).
3. Not an exchange holiday, if the instrument sets `holiday_market`
   (individual equities only — crypto/FX/commodities/CFD-indices don't
   observe exchange holidays and don't set this field).
4. Not inside a `DAILY` guard window (e.g. rollover, or an equity's
   overnight close).
5. Not within `session_edge_minutes` of session open/close.

Only after all five pass does it spawn Claude Code. Preflight risk
checks (kill switch, allowlist, max positions, cooldowns) run inside
`run_playbook_once` itself and reject before the LLM call too — none of
this is billed to Anthropic when it's skipped.

Individual US equities (GOOGL/MSFT/NVDA) are modeled as a Mon 13:30 →
Fri 20:00 week span plus a `DAILY 20:00-13:30` guard that wraps past
midnight — the guard excludes every overnight period, the week-span
anchors exclude weekends. That 13:30-20:00 UTC window is correct during
US Daylight Saving Time (EDT) only; during EST months real NYSE hours
are 14:30-21:00 UTC and this file does not auto-adjust. Verify against
`cap_market_get`'s reported `openingHours` if trading through a DST
transition.

## Running

```
python -m capital_agent            # foreground; Ctrl+C to stop
python -m capital_agent status     # one-shot session-status probe
```

Environment (loaded from `.env`):

- `CAP_*` — upstream MCP credentials + built-in guards.
- `KEEPALIVE_INTERVAL_SECONDS` — default 300.
- `RECONCILIATION_INTERVAL_SECONDS` — default 60.
- `CAPITAL_AGENT_STATE_DIR` — default `./state`.
- `CAPITAL_AGENT_LOG_DIR` — default `./logs`.
- `CAPITAL_AGENT_CONFIG_DIR` — default `./config`.
- `HEALTH_API_TOKEN` — required for `/kill` and `/unlock`.
- `HEALTH_API_BIND` — default `127.0.0.1:8080`.

## Health API

- `GET /healthz` → `{"ok": true, "ts": "..."}`
- `GET /status` → MCP session, kill switch, local position count, next
  fire time per job.
- `GET /jobs` → job list with triggers.
- `POST /kill` (`X-Auth-Token: <HEALTH_API_TOKEN>`) → activate kill switch.
- `POST /unlock` (`X-Auth-Token: <HEALTH_API_TOKEN>`) → clear it.

## History

This doc originally described step (2) of the build (scheduler skeleton
only — no LLM, no trading, no alerts). Steps 3-7 since added: the LLM
driver + dry-run strategy (step 3-4), the full risk layer / kill switch /
Telegram alerting (step 5), live demo execute + reconciliation (step 6),
and hardening — tests, systemd/docker-compose, ops docs (step 7). What's
above reflects the system as it stands today, including the later
multi-instrument + holiday-calendar + config-driven-jobs work. There is
still no news blackout (spec-optional, unbuilt) and no cross-instrument
concurrency semaphore beyond the MCP client's own serializing lock and
`max_positions_total` in `risk.yaml`.

## Kill-switch semantics (in effect from step 2, meaningful from step 3+)

- `kill_switch.active=true` blocks all trading-adjacent jobs (once they
  exist). Reconciliation, keepalive, and daily reset always run.
- Must be cleared explicitly via `POST /unlock`. There is no
  auto-recovery.
- On startup, if the row is missing it is seeded `active=false`. If the
  DB itself is missing, the kill switch is treated as `active=true`
  (fail-safe) until the DB is initialized.

## What to look for when it's running

The console prints one JSON line per event. You want to see:

- `boot`
- `mcp.started`
- `mcp.tools_ready count=38`
- `config.loaded` — check `scheduled_jobs` lists all six job ids and
  `holiday_markets` lists `["US", "UK", "DE"]`.
- `session_audit.market epic=... broker_status=...`  (once per allowlist
  epic — this is the only live signal that a newly-added epic code is
  actually valid on your account; a `session_audit.error` instead means
  the epic was rejected by `cap_market_get`, which usually means the
  code is wrong)
- `scheduler.started` — check `jobs` lists all six `{id, epic, tier}`
  entries.
- Every 60 s: `reconcile.ok broker_positions=0 local_positions=0`
- Every 60 s: `drawdown.snapshot equity=... hwm=...` (or
  `drawdown.daily_cap_hit` on a breach)
- Every 5 min: `keepalive.ok logged_in=true expires_in_s=...`
- On a strategy tick: `job.start epic=... strategy=...` then either
  `playbook.ok` or `job.skipped reason=session_closed|exchange_holiday|
  in_daily_guard|within_session_edge|epic_not_in_sessions_yaml`.

If you see `keepalive.relogin` more than once per hour, the session
token is churning — check network / credentials.
