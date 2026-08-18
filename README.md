# AI Trading Bot — Capital.com MCP Agent

Session-aware, LLM-driven trading agent for Capital.com. The broker
execution layer is the **official upstream**
[`capital-com-sv/capital-mcp`](https://github.com/capital-com-sv/capital-mcp)
Model Context Protocol server; the scheduler, driver, and risk layer
in this repo run on top of it. Every LLM invocation is one
non-interactive `claude -p` call — the LLM previews, our Python
executes.

**Status:** production-ready dry-run bot with a proven live path on a
Capital.com demo account. See "What runs" below.

## Three-process model

```
                    ┌──────────────────────────────────────┐
                    │           capital-agent              │
                    │  (long-running Python, one process)  │
                    │                                      │
scheduled ticks ──> │  APScheduler ──> risk preflight ──>  │
                    │      strategy Python pre-compute ──> │
                    │        `claude -p` subprocess ────┐  │
                    │        (Claude Code CLI)          │  │
                    │      risk postflight ──> execute  │  │
                    │            (Python calls MCP)     │  │
                    │                                   │  │
                    │  keepalive + reconcile + drawdown │  │
                    │  Health API :8080                 │  │
                    └────────────────────────────────┬──┴──┘
                                                     │ stdio
                                                     ▼
                                      ┌────────────────────────┐
                                      │    capital-mcp         │
                                      │  (upstream, unmodified)│
                                      │       ▲                │
                                      │       │ HTTPS          │
                                      └───────┼────────────────┘
                                              │
                                              ▼
                                      Capital.com REST + WS
                                      (demo or live)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a sequence
diagram of one tick.

## What runs

| Component | Command | Notes |
| --- | --- | --- |
| Scheduler (long-running) | `run_scheduler.ps1` / `python -m capital_agent run` | Loops keepalive (5 min), reconcile (60 s), drawdown check (60 s), daily reset (00:00 UTC), and one scheduled strategy job (default: dry-run RSI on GOLD, cron `0,15,30,45 * * * *`). |
| Read-only analysis | `run_analyze_once.ps1 -Epic BTCUSD` | Claude fetches candles + sentiment, prints JSON verdict. No trading tools. |
| Dry-run strategy | `run_strategy_once.ps1 -Epic BTCUSD` | Preview flow. Claude cannot execute — `--allowedTools` doesn't include execute tools and `CAP_DRY_RUN=true`. |
| Live strategy (demo) | `run_go_live.ps1` then `run_strategy_live.ps1 -Epic BTCUSD` | Real preview → execute. Python calls execute after our postflight validates. |
| Restore safe | `run_go_demo.ps1` | Flip fuses back to dry-run. |
| Backtest replay | `run_backtest.ps1 -Epic BTCUSD` | Zero cost. Replays the strategy math on historical bars. |

## Safety layers

Three independent gates block execute:

1. **`--allowedTools` whitelist** on every Claude invocation. Read-only
   playbooks: only market data tools. Strategy playbooks: preview
   only. **Never execute.**
2. **`--disallowedTools` blocklist** explicitly denies every
   `cap_trade_execute_*` / `cap_trade_positions_close` /
   `cap_trade_orders_cancel` on every playbook, unconditionally.
3. **`CAP_DRY_RUN=true` at the MCP layer** refuses execute regardless.

Above those, our **runtime risk layer** (`src/capital_agent/risk/`)
rejects a decision before the LLM is ever spawned when: kill switch is
active, epic isn't in the allowlist, max positions reached, or a
cooldown is in effect. See [`docs/RISK.md`](docs/RISK.md).

## First-run checklist

### Windows (fastest)

1. Install Python 3.11+ from https://python.org (tick "Add to PATH").
2. Install Git for Windows from https://git-scm.com/download/win.
3. Install Node.js LTS from https://nodejs.org.
4. `git clone https://github.com/SP24AM/AI-trading-bot.git && cd AI-trading-bot`.
5. `git checkout claude/capital-mcp-trading-agent-9pra1h`.
6. `cp .env.example .env` and fill in `CAP_API_KEY`, `CAP_IDENTIFIER`,
   `CAP_API_PASSWORD`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `HEALTH_API_TOKEN`.
7. `.\run_scheduler.ps1` — installs the venv + Claude Code CLI on
   first run, then starts. Watch for `mcp.tools_ready count=38` and
   `scheduler.started`.

### Linux / VPS via Docker

1. Same `.env` and `config/*.yaml` prep.
2. `docker compose build && docker compose up -d`.
3. `docker compose logs -f agent`.

### Bare-metal Linux via systemd

See [`deploy/systemd/capital-agent.service`](deploy/systemd/capital-agent.service).

## Everyday operations

- **Kill trading**: `curl -X POST -H "X-Auth-Token: $HEALTH_API_TOKEN" http://127.0.0.1:8080/kill?reason=eyes_off`
- **Resume**: same URL, `/unlock`.
- **Peek state**: `curl http://127.0.0.1:8080/status`
- **View last N signals**: `sqlite3 state/state.db 'SELECT ts, epic, decision, reason FROM signals ORDER BY ts DESC LIMIT 20;'`

## Docs

- [`docs/UPSTREAM_MCP_FINDINGS.md`](docs/UPSTREAM_MCP_FINDINGS.md) — the real 38-tool inventory + env-var name deltas.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — three-process model, per-tick sequence.
- [`docs/SCHEDULER.md`](docs/SCHEDULER.md) — session model, jobs, health API.
- [`docs/RISK.md`](docs/RISK.md) — three-layer safety stack, config knobs, kill switch, drawdown monitor.
- [`docs/STRATEGIES.md`](docs/STRATEGIES.md) — how playbooks + backtests stay in sync.
- [`docs/DRIVER.md`](docs/DRIVER.md) — the Claude Code invocation contract.
- [`docs/COSTS.md`](docs/COSTS.md) — Anthropic + broker fee model with a worked example.
