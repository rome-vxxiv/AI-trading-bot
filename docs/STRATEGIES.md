# Strategies

A strategy in this repo is (a) a **prompt file** in `prompts/` telling the
LLM exactly what to compute and what to preview, and (b) a **backtest
module** in `src/capital_agent/backtest/` that reproduces the same
decision math in Python. The two must agree bar-for-bar on the same
inputs — enforced by `tests/test_backtest_indicators.py`.

## Shipped playbooks

| Strategy id | Prompt | Backtest | Trading allowed | Notes |
| --- | --- | --- | --- | --- |
| `readonly_analysis` | `prompts/readonly_analysis.md` | — | none | step-3 surveillance |
| `rsi_mean_reversion` | `prompts/rsi_mean_reversion.md` | `backtest.rsi_strategy.decide_series` | preview only | step-4 dry-run |

## Triple-layer safety

Trading tools are never callable in step 4. Three independent gates:

1. **`--allowedTools`** — each `PlaybookSpec` in
   `src/capital_agent/driver/runner.py` whitelists exactly the tools the
   playbook needs. `rsi_mean_reversion` allows preview but not execute.
2. **`--disallowedTools`** — the runner's `COMMON_DENIED` list blocks
   every `cap_trade_execute_*`, `cap_trade_positions_close`, and
   `cap_trade_orders_cancel` for every playbook, unconditionally.
3. **`CAP_DRY_RUN=true`** — the upstream MCP server itself refuses to
   execute when this env var is set. The runner also refuses to spawn
   a strategy that has `require_dry_run=True` unless the env is set,
   so an accidental `.env` edit can't quietly go live.

The `rsi_mean_reversion` spec has `require_dry_run=True`. To flip that
off (step 6), set `require_dry_run=False` in the spec AND set
`CAP_DRY_RUN=false` in `.env` AND set `I_UNDERSTAND_LIVE_RISK=YES`.
All three are required.

## RSI mean-reversion (rsi_mean_reversion)

Rule:
- Fetch last 200 `MINUTE_15` bars for the epic.
- Compute Wilder RSI-14 on closes.
- Compute Wilder ATR-14 on true range.
- If `rsi_14 <= 30` → preview a BUY with `stop_distance = 2 * ATR`, `profit_distance = 3 * ATR`.
- If `rsi_14 >= 70` → preview a SELL with the same stop/target ratios.
- Otherwise → hold.

Fixed size `0.01` in step 4 (dry-run). Real per-trade sizing math lands
in step 5 with the full risk layer.

## Running

```
# One-shot strategy pass (needs CAP_DRY_RUN=true)
python -m capital_agent strategy-once --epic GOLD --strategy rsi_mean_reversion

# Backtest replay (no orders, no cost)
python -m capital_agent backtest --epic GOLD --resolution MINUTE_15 --max-bars 400

# Windows launchers
.\run_strategy_once.ps1 -Epic GOLD
.\run_backtest.ps1 -Epic GOLD
```

## Adding a new strategy

1. Write `prompts/my_strategy.md` — direct-imperative, `{EPIC}` placeholder, strict JSON schema for the reply. See `readonly_analysis.md` for shape.
2. Add a `PlaybookSpec` to `PLAYBOOKS` in `driver/runner.py` naming the prompt, the allowed tool list, and `require_dry_run` (default `True` for anything with preview).
3. Write the backtest twin in `src/capital_agent/backtest/my_strategy.py` — same decision function shape as `rsi_strategy.decide`.
4. Add a golden-numbers test in `tests/test_backtest_indicators.py`. If the live decision drifts from the backtest, this test flags it.
5. Register the scheduler job in `scheduler/app.py` when you want it firing.
