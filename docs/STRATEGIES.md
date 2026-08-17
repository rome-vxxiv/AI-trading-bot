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

## Trade-outcome simulation (`backtest.simulate`)

`decide_series` only says *when* a signal fired — it says nothing about
whether the trade would have made money. `backtest/simulate.py` closes
that gap: it walks forward from each signal to a stop or target exit
against the following bars, and `run_backtest` prints the result under
`trade_simulation` — `win_rate`, `avg_win_r`/`avg_loss_r`, `expectancy_r`,
`total_r`, `max_drawdown_r`, `profit_factor`, plus the last 20 individual
trades. All P&L is expressed in **R** (multiples of initial risk): a
trade that hits target is exactly `+1.5R` (target is 3×ATR, stop is
2×ATR), a trade that hits its stop is exactly `-1.0R`.

Stated assumptions — read these before trusting the numbers:

- Entry fills at the signal bar's own close (the same price the live
  playbook previews from).
- A bar whose range touches both the stop and the target is resolved as
  a stop (the conservative read — OHLC bars don't reveal which happened
  first intrabar).
- One open position at a time, mirroring `risk.yaml`'s
  `max_positions_total: 1` — a signal while a trade is open is skipped,
  not queued.
- No spread, slippage, financing, or commission are modeled. Real
  results will run behind this by some amount this module does not
  estimate.

This only evaluates historical signal quality — it says nothing about
whether the same edge holds going forward.

## Candidate under evaluation: rsi_trend_filtered

Backtesting `rsi_mean_reversion` against two consecutive real GOLD
windows (Jul 17 – Aug 17) showed win rate and total R flipping sign
between windows — combined expectancy across both was ~+0.03R/trade,
indistinguishable from noise. The likely cause: the rule fades every
RSI extreme with no trend awareness, so it repeatedly fights whichever
direction the market is actually grinding in.

`backtest/rsi_trend_filtered.py` tests one fix: only take a signal when
it agrees with an SMA trend filter (long only above the SMA, short only
below it). Same RSI-14/ATR-14/stop/target math, just gated.

The SMA period matters more than it looks: an early version defaulted
to SMA-50 and it never fired a single signal, in either direction, on
1000 real GOLD bars *or* on dozens of synthetic random-walk trials.
Verified this wasn't a bug — a 14-bar RSI extreme is a big enough move
to single-handedly decide which side of anything from a 14- to
~100-period SMA price ends up on, so "oversold AND above the SMA"
(or the short equivalent) essentially never co-occurs at those periods.
Real separation only starts appearing around SMA-200, which is the
shipped default; `tests/test_rsi_trend_filtered.py` has a regression
test on fixed-seed data that fails loudly if this ever silently stops
firing again.

This is **backtest-only** — no prompt file, no `PlaybookSpec`, no
scheduler job. Compare it against the shipped rule before considering
either:

```
python -m capital_agent backtest --epic GOLD --strategy rsi_trend_filtered --max-bars 1000
.\run_backtest.ps1 -Strategy rsi_trend_filtered
```

Only promote it to a real playbook (new prompt file + `PlaybookSpec` +
golden test, per "Adding a new strategy" below) once it's beaten the
shipped rule across the same historical windows — not on a single
good-looking run.

## Adding a new strategy

1. Write `prompts/my_strategy.md` — direct-imperative, `{EPIC}` placeholder, strict JSON schema for the reply. See `readonly_analysis.md` for shape.
2. Add a `PlaybookSpec` to `PLAYBOOKS` in `driver/runner.py` naming the prompt, the allowed tool list, and `require_dry_run` (default `True` for anything with preview).
3. Write the backtest twin in `src/capital_agent/backtest/my_strategy.py` — same decision function shape as `rsi_strategy.decide`.
4. Add a golden-numbers test in `tests/test_backtest_indicators.py`. If the live decision drifts from the backtest, this test flags it.
5. Register the scheduler job in `scheduler/app.py` when you want it firing.
