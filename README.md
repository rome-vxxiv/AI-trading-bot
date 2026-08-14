# AI Trading Bot — Capital.com MCP Agent

Session-aware, LLM-driven trading agent for Capital.com. The execution layer
is the **official upstream** [`capital-com-sv/capital-mcp`](https://github.com/capital-com-sv/capital-mcp)
Model Context Protocol server; the scheduler and driver in this repo decide
when to run and enforce our own risk layer on top of the server's built-ins.

> **Status:** early scaffold. The upstream MCP has been inspected but not yet
> exercised against a demo account from this repo. See
> [`docs/UPSTREAM_MCP_FINDINGS.md`](docs/UPSTREAM_MCP_FINDINGS.md) for the
> real tool inventory and the deltas vs. the original build spec.

## Three-process model

| Process         | Role                                                                                                     |
| --------------- | -------------------------------------------------------------------------------------------------------- |
| `capital-mcp`   | Upstream MCP server. Broker execution layer. Not modified — wrapped.                                     |
| `scheduler`     | Long-running APScheduler process. Decides when to invoke the driver per instrument, per strategy.        |
| `agent-driver` | Invoked by the scheduler. Spawns `claude -p` non-interactively against the MCP, parses the JSON verdict. |

## Safety-first defaults

- `CAP_ENV=demo`, `I_UNDERSTAND_LIVE_RISK=NO`.
- `dry_run: true` at the driver layer — full pipeline runs, previews call, execute is intercepted.
- `risk_pct=0.25%`, `max_positions=1`, `max_daily_loss=1%`, `max_drawdown=5%`.
- Only one slow-tier crypto job enabled in `config/jobs.example.yaml`.

Promoting to live requires a documented ceremony — see
[`docs/RISK.md`](docs/RISK.md) once written.

## Layout (planned)

```
config/            # sessions, jobs, risk, allowlist, mcp.json — *.example.* committed
prompts/           # per-strategy playbook prompts (strict schema output)
src/capital_agent/ # scheduler, driver, risk, state, alerts, health
docs/              # architecture, scheduler, risk, strategies, costs
tests/             # unit + contract + fake-clock + backtest golden
docker/            # Dockerfiles per service
```

## First-run checklist

1. Obtain Capital.com **demo** API credentials (Settings → API integrations).
   The upstream server needs `CAP_API_KEY`, `CAP_IDENTIFIER`, `CAP_API_PASSWORD`
   — **not** the spec's `CAPITAL_*` names. See findings doc.
2. Copy `.env.example` → `.env` and fill in.
3. `docker compose up capital-mcp` — verify `cap_session_status` and
   `cap_account_list` return.
4. `docker compose up scheduler` (once step 2 of the build is complete).

Nothing beyond step (1) verification runs yet — this repo is being built in
demo-tested increments.
