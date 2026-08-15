# Risk

Three layers stack. Every trade decision passes through all three
before any preview can happen; anything downstream of a preview
(execute) is still blocked in step 5.

## Layer 1 — upstream MCP built-ins

Enforced by capital-mcp's `risk.py` before it touches Capital.com:

- `CAP_ALLOW_TRADING=false` blocks every trade tool (still true in step 5).
- `CAP_DRY_RUN=true` refuses every execute call regardless.
- `CAP_ALLOWED_EPICS` — if we accidentally list an unknown epic, MCP rejects.
- `CAP_MAX_POSITION_SIZE`, `CAP_MAX_OPEN_POSITIONS`, `CAP_MAX_ORDERS_PER_DAY`.

## Layer 2 — our runtime pre-flight and post-flight

`src/capital_agent/risk/` runs BEFORE the LLM is invoked (pre-flight)
and AFTER the preview response comes back (post-flight).

Pre-flight checks — LLM call is skipped if any fail:

| Check | Reason logged | Source |
| --- | --- | --- |
| Kill switch active | `kill_switch_active:<reason>` | `state.db` `kill_switch` row |
| Epic not in allowlist | `epic_not_in_allowlist:<epic>` | `config/allowlist.yaml` |
| Max total positions reached | `max_positions_total:...` | `state.db` `positions_local` |
| Max positions per instrument | `max_per_instrument:...` | same |
| Any-trade cooldown | `any_trade_cooldown_until_<iso>` | `signals` table |
| Losing-trade cooldown | `loss_cooldown_until_<iso>` | signals with `outcome=loss` |

Post-flight checks — preview response must satisfy all:

| Check | Reason logged |
| --- | --- |
| `decision != "hold"` implies `preview_id != null` | `no_preview_id_from_llm` |
| Broker's own preview.all_checks_passed must be true | `broker_preview_checks_failed` |
| Stop distance ≤ `stop_max_atr_multiples × ATR` | `stop_distance_<n>x_atr_exceeds_<max>` |

## Layer 3 — spawn-time gates

`driver/runner.py` won't even build the CLI args if:

- The playbook's `require_dry_run=True` but `CAP_DRY_RUN` isn't `true`.
- `--allowedTools` doesn't whitelist the tool the model would need.
- `--disallowedTools` explicitly blocks every `cap_trade_execute_*` on
  every playbook regardless of allowlist.

## Configuration — `config/risk.yaml`

Every knob has a first-run safe default in `config/risk.example.yaml`.
Values here can only be equal to or stricter than the upstream MCP;
loosening them beyond the MCP's own cap is harmless (MCP wins) but the
loader warns.

```yaml
dry_run: true                 # driver intercepts execute even if model tries
risk_pct_per_trade: 0.0025    # 0.25% of equity per trade
max_positions_total: 1
max_positions_per_instrument: 1
max_daily_loss_pct: 0.01      # 1% of start-of-day equity
max_drawdown_pct: 0.05        # 5% from HWM → kill switch
stop:
  required: true              # every entry MUST have a stop
  max_atr_multiples: 5.0
  atr_period: 14
cooldowns:
  after_any_trade_minutes: 15
  after_losing_trade_minutes: 60
spread:
  max_multiple_of_median_1h: 2.0
```

## Kill switch

Sits in `state.db` `kill_switch` row. Fail-safe: missing row = active.

Activated by:

- `POST http://127.0.0.1:8080/kill?reason=...` with `X-Auth-Token: $HEALTH_API_TOKEN`.
- (Step 6) drawdown monitor when equity < HWM × (1 − `max_drawdown_pct`).

Cleared only by:

- `POST http://127.0.0.1:8080/unlock` with the same token.

While active, every strategy job's pre-flight rejects with reason
`kill_switch_active:<reason>` and emits a Telegram alert. Keepalive and
reconciliation still run.

## Telegram alerts

Configured via `.env`:

```
TELEGRAM_BOT_TOKEN=1234:AAA...
TELEGRAM_CHAT_ID=98765
```

Fires on: `scheduler.starting`, `scheduler.stopped`, `kill_switch.activated`,
`kill_switch.cleared`, `playbook.decision` (any non-hold), `playbook.rejected`
(pre-flight), `playbook.parse_error`, `playbook.timeout`,
`playbook.nonzero_exit`.

No Telegram config → alerts silently no-op; nothing else changes.

## What isn't wired yet (step 6+)

- Drawdown monitor that auto-triggers the kill switch (needs equity snapshots).
- Daily loss cap enforcement.
- Spread check (needs real-time bid/ask snapshots).
- Position-outcome recording (whether a closed trade was a loss) — enables the losing-trade cooldown.
