# Agent driver (step 3)

The driver spawns `claude -p` non-interactively once per scheduled tick.
Each invocation is a fresh process — it opens its own MCP subprocess via
`config/mcp.json`, authenticates against Capital.com demo, calls the
whitelisted tools, and exits. No shared state between invocations
except what's in the database.

## What runs in step 3

Only ONE job: **`analysis_gold_15m`**. It fires on the UTC cron
`0,15,30,45 * * * *` (aligned to :00 / :15 / :30 / :45 every hour) and
calls the read-only playbook against **GOLD**.

The job is session-gated. Skip reasons logged when it doesn't fire:

- `kill_switch_active` — someone hit `POST /kill`.
- `epic_not_in_sessions_yaml` — misconfigured (shouldn't happen).
- `session_closed` — outside `SUN 22:00 → FRI 21:00`. GOLD skips
  **every weekend** and every day at 21:00–22:00 UTC (rollover pause).
- `in_daily_guard` — inside a `DAILY hh:mm-hh:mm` guard.
- `within_session_edge` — first/last 5 min of the session window.

## Trading is deliberately impossible in step 3

Two layers stop it:

1. `--allowedTools` restricts the model to these three tools:
   - `Read` (filesystem — for `state/tick-context.json`)
   - `mcp__capital-com__cap_market_prices`
   - `mcp__capital-com__cap_market_sentiment`
2. `--disallowedTools` explicitly blocks every `cap_trade_*` tool as a
   belt over `allowedTools` in case future Claude Code releases change
   the semantics.

Even a compromised prompt cannot open a position — the CLI itself
refuses. The prompt's rules are the third layer, not the first.

## Cost

At ~800 output tokens + ~2500 input tokens per call, one call on Sonnet
runs roughly $0.02 (as of 2026). GOLD trades ~24×5 = 120 hours/week.
At 15-min cadence that's 480 invocations/week ≈ **$10/week** worst
case. Idle windows (guards + weekends) skip without cost. See
`docs/COSTS.md` (added in step 7) for the full model.

## Model output contract

The playbook (`prompts/readonly_analysis.md`) enforces a strict JSON
schema. The driver's `_extract_verdict_json` finds the first JSON
object in the assistant's final message that has an `epic` key and
parses it. Malformed output → `analysis.parse_error` logged, no signal
stored, exit code non-zero.

Fields expected:

```json
{
  "epic": "GOLD",
  "analyzed_at_utc": "2026-08-15T12:15:00Z",
  "candle_count": 200,
  "last_close": 2418.55,
  "sma_20": 2415.90,
  "rsi_14": 63.4,
  "atr_14": 8.12,
  "sentiment_long_pct": 71.0,
  "verdict": "neutral",
  "reason": "RSI 63.4 (< 70) with sentiment 71% long — no extreme."
}
```

Stored in the `signals` table with strategy_id `readonly_gold_15m`.

## Testing without waiting for a session

```
.\run_analyze_once.ps1                     # GOLD by default
.\run_analyze_once.ps1 -Epic BTCUSD       # crypto — always in session
```

Bypasses the session gate; still runs through the full driver, the
same tools, and stores a signal row. Costs one API call.

## What isn't here yet (step 4+)

- Real preview/execute path. The driver still can't call trading tools.
- Strategy math the backtest can reproduce bar-for-bar. Right now the
  prompt says "compute RSI-14"; there's no backtest harness pinning
  the exact formula the model uses.
- Cooldowns, ATR sanity checks, spread checks, kill-switch response.
  These land in step 5 with the full risk layer.
- Telegram alerts. Step 5.
