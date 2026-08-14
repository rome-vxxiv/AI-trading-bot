# Scheduler

Long-running Python process. In step (2) it does:

1. Loads settings, sessions, allowlist.
2. Spawns the upstream MCP as a stdio subprocess and initializes it.
3. Runs one-shot startup audits:
   - `tools/list` from MCP.
   - `cap_market_get(epic)` for each allowlisted epic to log broker-reported
     `openingHours` vs. our local sessions.yaml.
4. Schedules three recurring jobs:
   - **keepalive** — every 5 min. Calls `cap_session_ping`, then
     `cap_session_status`. If `logged_in` comes back false, re-logs in.
   - **reconcile** — every 60 s. Calls `cap_trade_positions_list`,
     compares to the local `positions_local` table, adopts broker-only
     positions with `strategy_id=null`, deletes local-only ones, logs
     any field deltas. **Broker is source of truth.**
   - **daily_reset** — cron `00:00 UTC`. Logs yesterday's daily-stats row
     and seeds today's.
5. Serves the health API on `127.0.0.1:8080`.

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

## What's deliberately NOT in step (2)

- No strategy evaluation, no LLM call.
- No trading, no preview, no execute. `cap_trade_preview_position` and
  `cap_trade_execute_position` are never called.
- No Telegram alerts (step 5).
- No news blackout (spec optional).
- No cross-instrument concurrency semaphores (there's only reconcile
  running against MCP, which is naturally serialized by the client lock).

Every one of those is wired in a later step — see `TaskList` in the
project brief and the `docs/UPSTREAM_MCP_FINDINGS.md` name deltas.

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
- `session_audit.market epic=BTCUSD ...`  (once per allowlist epic)
- `scheduler.started`
- Every 60 s: `reconcile.ok broker_positions=0 local_positions=0`
- Every 5 min: `keepalive.ok logged_in=true expires_in_s=...`

If you see `keepalive.relogin` more than once per hour, the session
token is churning — check network / credentials.
