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
| `rsi_mean_reversion_live` | `prompts/rsi_mean_reversion.md` (same prompt) | `backtest.rsi_strategy.decide_series` | preview + real execute | step-6 live — currently wired to GOLD's scheduled job only |

## Triple-layer safety

Trading tools are never callable by the LLM itself, in either mode.
Three independent gates:

1. **`--allowedTools`** — each `PlaybookSpec` in
   `src/capital_agent/driver/runner.py` whitelists exactly the tools the
   playbook needs. Both `rsi_mean_reversion` and `rsi_mean_reversion_live`
   allow Claude to call preview, never execute — that never changes, in
   either mode. See "Live mode mechanics" below for who actually places
   the order.
2. **`--disallowedTools`** — the runner's `COMMON_DENIED` list blocks
   every `cap_trade_execute_*`, `cap_trade_positions_close`, and
   `cap_trade_orders_cancel` for every playbook, unconditionally —
   including `rsi_mean_reversion_live`. Claude cannot call these tools
   no matter what `.env` says.
3. **`CAP_DRY_RUN=true`** — the upstream MCP server itself refuses to
   execute when this env var is set. The runner also refuses to spawn
   a strategy that has `require_dry_run=True` unless the env is set,
   so an accidental `.env` edit can't quietly go live.

`rsi_mean_reversion` has `require_dry_run=True` and will refuse to run
at all once `CAP_DRY_RUN=false` — it does not silently fall back to
preview-only, it errors out (`_error: dry_run_required`) and does
nothing. `rsi_mean_reversion_live` is the opposite: `require_dry_run=False`,
`require_live_fuse=True`, and it refuses to run unless *both*
`CAP_DRY_RUN=false` and `I_UNDERSTAND_LIVE_RISK=YES` are set in `.env`
(`run_go_live.ps1` / `capital-agent go-live` sets both together). Flipping
an instrument from one to the other in `config/jobs.yaml` is deliberate,
one line, one instrument at a time — see `docs/SCHEDULER.md`.

### Live mode mechanics

Even with `rsi_mean_reversion_live`, Claude's role doesn't change: it
still only ever calls `cap_trade_preview_position` and reports a
decision, gated by the same `--allowedTools`/`--disallowedTools` pair as
dry-run. The difference is entirely in `driver/runner.py`, after Claude
exits: when `spec.executes_after_preview` is set, Python — not the LLM —
re-checks the preview against postflight risk rules and, only if that
passes, calls execute itself. The model never gets execute access in
either mode.

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

A selective rule like this one can produce too few trades in a single
1000-bar (Capital.com's per-request cap) window to mean anything — one
early GOLD test came back with 3 trades total across two windows. Use
`backtest-multi` to walk backward through several chunks in one session
and evaluate them as a single combined sample instead of doing that by
hand:

```
python -m capital_agent backtest-multi --epic GOLD --strategy rsi_trend_filtered --max-bars 1000 --num-windows 5
.\run_backtest.ps1 -Multi -Strategy rsi_trend_filtered -NumWindows 5
```

It fetches each 1000-bar chunk separately (that's the API's limit,
not a design choice) but **stitches them into one chronological series
before simulating** — never simulates chunk-by-chunk and pools the
results. An earlier version did that and it silently force-closed any
trade still open when a chunk ran out of bars, undercounting its real
outcome even though the next chunk's data (the trade's actual future)
was sitting right there. Consecutive fetches were also observed to
overlap by a few bars rather than land perfectly back-to-back;
`fetched_chunks` in the output reports `bars_dropped_as_overlap` per
chunk so that's visible rather than silently absorbed. The single
`combined` block (same shape as a normal backtest's `trade_simulation`)
is the only trade-level result — there is no more misleading
per-window trade breakdown, because trades no longer respect where one
fetch happened to end and the next began.

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
