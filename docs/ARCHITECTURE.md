# Architecture

## Processes

Single Python process (`python -m capital_agent run`) hosts:

- `APScheduler` (asyncio) with 5 recurring jobs.
- `MCPClient` — long-lived stdio subprocess wrapping `capital-mcp`
  spawned once on boot, all tool calls serialized through one
  asyncio lock.
- `FastAPI` health/status server on `127.0.0.1:8080`.
- Each scheduled strategy tick spawns `claude -p` as an ephemeral
  subprocess for the LLM call itself; that subprocess opens its own
  short-lived MCP connection via `claude mcp add`.

So on disk it's ONE long process (the scheduler), plus TWO
short-lived subprocesses per strategy tick (Claude Code + the MCP
launched by Claude). The scheduler's own persistent MCP handles the
Python-side pre-compute and reconciliation.

## Per-tick sequence — dry-run strategy

```
scheduler tick @ :00/:15/:30/:45 UTC
   │
   ├─ session gate (is GOLD open? guard? edge?)
   │      → skip with reason if closed
   │
   ├─ risk preflight
   │      • kill_switch active?
   │      • epic in allowlist?
   │      • max positions reached?
   │      • cooldown active?
   │      → reject + Telegram alert if any fail (no LLM cost)
   │
   ├─ Python indicator pre-compute
   │      • cap_market_prices(epic, MINUTE_15, 60)  via persistent MCP
   │      • compute Wilder RSI-14 + ATR-14 (backtest.indicators)
   │      • compute suggested_size from equity × risk_pct / stop
   │      • write state/tick-context.json
   │
   ├─ spawn `claude -p` (~5-15s)
   │      stdin  = playbook prompt with {EPIC} substituted
   │      args   = --allowedTools Read,cap_trade_preview_position
   │              --disallowedTools <all execute tools>
   │              --max-turns 15
   │      LLM does: Read tick-context, decide, preview if signal fires
   │
   ├─ parse verdict JSON from claude stdout
   │      → parse error = alert + skip
   │
   ├─ risk postflight
   │      • preview_id present if decision != hold?
   │      • broker preview.all_checks_passed?
   │      • stop_distance within max_atr_multiples × ATR?
   │      → reject + alert if any fail
   │
   ├─ (LIVE ONLY) Python calls cap_trade_execute_position(preview_id)
   │      • CAP_DRY_RUN=false AND I_UNDERSTAND_LIVE_RISK=YES required
   │      • cap_trade_confirm_wait for broker ACCEPTED/REJECTED
   │      • record PositionsLocal row for reconcile to track
   │
   └─ save Signal row + Telegram alert on non-hold or reject
```

## Per-tick sequence — reconciliation

Runs every 60 s independently:

```
reconcile tick
   │
   ├─ cap_trade_positions_list  →  set(remote_deal_ids)
   ├─ SELECT * FROM positions_local  →  set(local_deal_ids)
   │
   ├─ broker-only (remote - local) → adopt into local table
   │       with strategy_id=null. Alert on drift.
   │
   ├─ field deltas (size/stop/tp changed) → update + info log
   │
   └─ local-only (local - remote) → position closed
           • DELETE from positions_local
           • outcome_tagger fetches
             cap_account_history_transactions
             for that deal_id, computes realized P&L,
             writes {outcome, pnl_realized} into the
             originating Signal.model_output_json
           • Telegram alert with P&L
```

## Per-tick sequence — drawdown monitor

Runs every 60 s:

```
drawdown tick
   │
   ├─ cap_account_list → current equity + currency
   │
   ├─ SELECT last EquitySnapshot → previous HWM + daily_start
   │       HWM = max(equity, prev.hwm)
   │       daily_start = prev.daily_start if same UTC day else equity
   │
   ├─ INSERT EquitySnapshot(equity, hwm, daily_start)
   │
   ├─ if (hwm - equity) / hwm >= max_drawdown_pct:
   │       kill switch ON + Telegram alert
   │
   └─ if (equity - daily_start) / daily_start <= -max_daily_loss_pct:
           daily-loss-cap alert (log + Telegram, no auto-kill)
```

## Data flow

Everything persisted to `state/state.db` (SQLite via aiosqlite).
Tables:

- **equity_snapshots** — one row per drawdown tick (60 s).
- **positions_local** — mirror of open broker positions; reconcile-owned.
- **signals** — one row per LLM invocation (all decisions, all reject reasons).
- **kill_switch** — single-row switch. `active` bool + reason string.
- **daily_stats** — one row per UTC day, seeded by `daily_reset` job.
- **jobs_audit** — reserved for future per-tick audit if we want it.

`state.db` is the ONLY writable persistence. Broker is source of
truth for positions and equity; local rows exist for scheduling
memory, cooldown timers, and reporting.

## Failure semantics

- **MCP subprocess dies** → next `mcp.call()` re-spawns via `lifespan_mcp()`.
  Structured events `mcp.stop_error` / `mcp.started` bracket the recovery.
- **DB unavailable** → kill switch's `is_active()` returns `(True,
  "db_error")` — fail-safe.
- **Kill switch row missing** → treated as active. Runner refuses to
  spawn Claude.
- **Health API port in use** → logs `health_api.port_in_use` and gives
  up gracefully. Scheduler keeps running without observability. Not
  fatal.
- **Claude Code returns non-zero** → alert + skip. No signal recorded.
- **LLM verdict fails JSON parse** → alert + skip.
- **Preview passes broker checks but our postflight rejects** → no
  execute call. Signal recorded with `_postflight_ok=false`.
- **Execute call errors (network, MCP crash)** → `_execute.status =
  MCP_ERROR`, alert fires, no position row created, no state drift.
- **Broker rejects the order (REJECTED / MARKET_CLOSED / etc)** →
  `_execute.ok=false` with `status` and `reason` fields. Alert fires.
