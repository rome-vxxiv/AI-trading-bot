# Costs

Two cost centres. Both are small at our default cadence; both scale
linearly with tick rate.

## Anthropic API (Claude Code)

Each `strategy-once` invocation makes ~2-4 API calls internally to
Claude (spawn, tool-decision turns, final output). Since step 5 pushed
indicator math to Python, the LLM only reads a small
tick-context.json + decides + optionally previews. Token profile per
invocation:

| Component | Approx tokens |
| --- | --- |
| System + tool schemas + prompt | ~2500 |
| tick-context.json (Read) | ~400 |
| Preview call (if signal fires) | ~200 |
| Final JSON reply | ~150 |
| **Total per invocation** | **~3250** (input) + ~500 (output) |

At Sonnet pricing (as of 2026 — check
https://docs.claude.com/en/docs/about-claude/models#model-pricing for
current):

- Input: ~$3 / MTok
- Output: ~$15 / MTok

Per invocation: `3250/1M × $3 + 500/1M × $15 ≈ $0.017`.

### Worked example — GOLD every 15 min

GOLD trades Sun 22:00 → Fri 21:00 UTC, minus one hour daily rollover.
That's `24 × 5 - 5 = 115 open hours per week`. At 4 ticks/hour:

```
115 × 4 × $0.017 ≈ $7.80 per week
                ≈ $34 per month
```

Session gating skips are FREE — we never spawn Claude if the session
is closed, so weekends and daily rollover pauses don't cost anything.
Preflight rejections (kill switch, cooldowns, allowlist) are also
free — the runner exits before the LLM spawn.

### Worked example — BTCUSD every 15 min

Crypto is 24/7:

```
24 × 7 × 4 × $0.017 ≈ $11.42 per week
                    ≈ $50 per month
```

### Tuning knobs

- **Cadence** — change `CronTrigger(minute="0,15,30,45", ...)` in
  `scheduler/app.py`. Every 60 min is ¼ the cost.
- **Model** — Claude Code lets you pin a specific model via the
  `ANTHROPIC_MODEL` env var. Haiku is ~5× cheaper than Sonnet but
  outputs are less consistent for structured JSON.
- **Prompt** — the leaner the prompt, the fewer input tokens. Current
  playbook is ~1.5KB; each halving saves ~35% on input cost.

## Capital.com

Demo account: **zero broker cost**. Every trade is simulated on their
side; spread and financing are shown for realism but you don't lose
real money.

Live account: fees vary by instrument. For the strategies here
(15-min timeframe, RSI mean-reversion, ~1 signal per few hours),
turnover is low, so:

- Spread: paid on entry + exit. For GOLD, ~$0.30 per 0.01 lot round
  trip. At 5 trades/day = $1.50/day = ~$30/month.
- Overnight financing: our TP=3×ATR generally closes intraday. If a
  position holds overnight: ~-0.02%/day for CFDs.
- Currency conversion: only if your account currency differs from the
  instrument's quote currency.

Broker fees are not directly measured by this repo. You can pull
them from `cap_account_history_transactions` to sanity-check.

## Free tier / infra

- **VPS** (if you rent one): €4-6/month for a 2 vCPU / 2 GB EU VPS is
  enough. This process is idle most of the time (`await
  stop_event.wait()`); actual CPU spikes only on the 15-min tick.
- **Windows PC** (current setup): $0 additional. Costs are electricity
  + your PC uptime discipline.

## Kill-switch trip costs

Missed signals during a kill-switch window are the OPPORTUNITY cost,
not a direct cost. The bot never "loses" money by being off.

The Anthropic cost of a kill-switch trip is exactly one alert HTTP POST
to Telegram (free) + zero LLM calls (rejected at preflight, never
spawned). So keeping the switch on for a day of investigation is free.
