# Upstream MCP — Verified Findings

Read from `capital-com-sv/capital-mcp` at `main` (shallow clone,
2026‑08‑14). All names and shapes below are copied from
`capital_mcp/server.py` and `capital_mcp/config.py` — no guessing.

The original build spec assumed a handful of tool names and env-var
names that do **not** match the real server. Those deltas are called
out inline; anything in this repo must use the **actual** names.

---

## 1. Environment variables (spec → actual)

| Spec name             | Actual name         | Notes                                                    |
| --------------------- | ------------------- | -------------------------------------------------------- |
| `CAPITAL_API_KEY`     | `CAP_API_KEY`       | Required                                                 |
| `CAPITAL_IDENTIFIER`  | `CAP_IDENTIFIER`    | Required (login email)                                   |
| `CAPITAL_PASSWORD`    | `CAP_API_PASSWORD`  | Required (API custom password, NOT the platform password)|
| `CAPITAL_ENV`         | `CAP_ENV`           | `demo` \| `live`                                         |

Additional safety / control env vars provided by the server:

| Env var                          | Default | Purpose                                                  |
| -------------------------------- | ------- | -------------------------------------------------------- |
| `CAP_ALLOW_TRADING`              | `false` | Master switch. Trading tools refuse when `false`.        |
| `CAP_ALLOWED_EPICS`              | `""`    | CSV allowlist. `ALL` = unrestricted. Empty = block all.  |
| `CAP_MAX_POSITION_SIZE`          | `1.0`   | Per-order cap (units).                                   |
| `CAP_MAX_WORKING_ORDER_SIZE`     | `1.0`   | Per-order cap for LIMIT/STOP working orders.             |
| `CAP_MAX_OPEN_POSITIONS`         | `3`     | Concurrent positions cap.                                |
| `CAP_MAX_ORDERS_PER_DAY`         | `20`    | Daily order counter cap.                                 |
| `CAP_REQUIRE_EXPLICIT_CONFIRM`   | `true`  | `confirm=true` required on execute calls.                |
| `CAP_DRY_RUN`                    | `false` | Refuses every execution when `true`.                     |
| `CAP_DEFAULT_ACCOUNT_ID`         | —       | Preferred account after login.                           |
| `CAP_HTTP_TIMEOUT_S`             | `15`    |                                                          |
| `CAP_LOG_LEVEL`                  | `INFO`  |                                                          |
| `CAP_WS_ENABLED`                 | `false` | Enables `cap_stream_*` tools.                            |
| `CAP_PING_INTERVAL_S` (internal) | `480`   | Server pings internally every 8 min.                     |

Base URLs (verified in `config.py`):

- Demo:    `https://demo-api-capital.backend-capital.com`
- Live:    `https://api-capital.backend-capital.com`
- Stream:  `wss://api-streaming-capital.backend-capital.com/connect`

## 2. Full tool inventory (38 tools)

Grouped as they appear in `server.py`. Names below are the actual
Python function names — the MCP tool names are identical (FastMCP
default). When exposed to Claude Code, tool ids are
`mcp__capital-com__<name>` (prefix depends on the MCP-config key).

### Session (4)

- `cap_session_status()` — is the token still valid?
- `cap_session_login(force: bool = False, account_id: str | None = None)`
- `cap_session_ping()` — keepalive. **Use this over `status` for the scheduler's 5-min heartbeat.**
- `cap_session_logout()`

### Markets (6)

- `cap_market_search(...)`
- `cap_market_get(epic)` — **spec called this `cap_market_details`**
- `cap_market_navigation_root()`
- `cap_market_navigation_node(node_id)`
- `cap_market_prices(epic, resolution="MINUTE_15", max=200, from_date=None, to_date=None)` — **spec called this `cap_prices_historical`**. Resolutions: `MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK`.
- `cap_market_sentiment(market_id)` — **spec called this `cap_client_sentiment`; note the parameter is `market_id`, not `epic`** (they're usually the same string, but the field name matters for the tool call).

### Account (6)

- `cap_account_list()`
- `cap_account_preferences_get()`
- `cap_account_preferences_set(...)`
- `cap_account_history_activity(...)`
- `cap_account_history_transactions(...)`
- `cap_account_demo_topup(amount, confirm=False)`

### Trading (12)

- `cap_trade_positions_list()`
- `cap_trade_positions_get(deal_id)`
- `cap_trade_orders_list()`
- `cap_trade_confirm_get(deal_reference)`
- `cap_trade_confirm_wait(deal_reference, timeout_s, ...)`
- `cap_trade_preview_position(epic, direction, size, guaranteed_stop=False, trailing_stop=False, stop_level=None, stop_distance=None, stop_amount=None, profit_level=None, profit_distance=None, profit_amount=None)`
- `cap_trade_preview_working_order(epic, direction, type, level, size, ...)`
- `cap_trade_preview_working_order_update(deal_id, ...)`
- `cap_trade_execute_position(preview_id, confirm=False, wait_for_confirm=True, timeout_s=15.0)`
- `cap_trade_execute_working_order(preview_id, confirm=False, ...)`
- `cap_trade_execute_working_order_update(preview_id, confirm=False, ...)`
- `cap_trade_positions_close(deal_id, ...)`
- `cap_trade_orders_cancel(deal_id, ...)`

### Watchlists (7)

- `cap_watchlists_list`, `_get`, `_create`, `_add_market`, `_remove_market`, `_delete`, plus one already-counted variant.

### Streaming (3, opt-in via `CAP_WS_ENABLED`)

- `cap_stream_prices(...)`, `cap_stream_alerts(...)`, `cap_stream_portfolio(...)`

### Resources (4 — MCP resources, not tools)

- `cap_status_resource`, `cap_risk_policy_resource`,
  `cap_allowed_epics_resource`, `cap_market_cache_resource`.
  These are read by the LLM via `Read`-style MCP resource fetches;
  useful for feeding current guardrails into the playbook context.

## 3. Two-phase execution — how it really works

Spec said "preview → execute with `confirm=true`". Actual:

1. Call `cap_trade_preview_position(...)` → returns
   ```
   {
     "preview_id": "<uuid>",
     "normalized_request": {...},
     "checks": [{name, status, message}, ...],
     "all_checks_passed": true|false,
     "estimated_entry": <float|null>,
     "estimated_risk_notes": [...],
     "expires_in_seconds": 120
   }
   ```
2. If `all_checks_passed` and our own risk layer also approves, call
   `cap_trade_execute_position(preview_id=..., confirm=true, wait_for_confirm=true, timeout_s=15.0)`.
3. Preview is single-use (consumed on execute) and expires after 120s.

**The spec's `preview_deal_ref` / `executed_deal_ref` field names are wrong** —
the correct model-output JSON schema uses `preview_id` and the broker's
`dealReference` (returned by execute). Our playbook prompts and driver
parser must use those names.

## 4. Session lifecycle

- Sessions expire after 10 min inactivity (Capital.com upstream).
- Server internally pings every 8 min (`CAP_PING_INTERVAL_S=480`).
- Our scheduler still runs `cap_session_ping` on a 5-min external
  heartbeat during trading windows — belt-and-braces, since idle
  windows without any trading tick would let the internal timer be
  our only guarantee.

## 5. Rate limits

- 10 req/s per user (Capital.com upstream).
- Trading endpoints: 1 req / 100 ms server-side (enforced by
  `rate_limit_type="trading"` in `capital_client.py`).
- Only one `agent-driver` may run at a time against the same account
  (single-slot semaphore in the scheduler).

## 6. Built-in risk layer (`capital_mcp/risk.py`, ~560 LOC)

Enforced by the MCP itself, in addition to whatever our driver does:

- Epic allowlist check.
- Per-order size cap (`CAP_MAX_POSITION_SIZE`).
- Concurrent-positions cap (`CAP_MAX_OPEN_POSITIONS`).
- Daily-order counter (`CAP_MAX_ORDERS_PER_DAY`, resets 00:00 UTC).
- Broker dealing-rule validation (min/max/increments per market).
- Preview cache with TTL enforced by `cap_preview_cache_ttl_s`.
- `confirm=true` gate + `CAP_DRY_RUN` gate + `CAP_ALLOW_TRADING` gate.

Our driver's own risk layer sits **above** these and can only be
stricter — never looser.

## 7. Install methods to support

Both are documented in upstream `INSTALL.md`:

- **Local**: `python -m venv .venv && pip install -e .` then wire a
  `mcp.json` for Claude Code pointing at `capital-mcp` on PATH.
- **Docker**: upstream ships a `Dockerfile`. Our `docker-compose.yml`
  will build from a pinned upstream tag (rather than fork) so
  updates are `docker compose pull && up -d`.

## 8. Deltas the spec must adopt

Wherever the spec text or example prompts referenced the old names, the
implementation in this repo uses the actual ones. Concretely:

- `CAPITAL_*` → `CAP_*` env vars.
- `cap_market_details` → `cap_market_get`.
- `cap_prices_historical` → `cap_market_prices`.
- `cap_client_sentiment` → `cap_market_sentiment(market_id=…)`.
- Playbook JSON output uses `preview_id` and `deal_reference`
  (from broker), not `preview_deal_ref` / `executed_deal_ref`.
- Scheduler keepalive uses `cap_session_ping`, not `cap_session_status`
  (status is fine but ping is the intended keepalive).

None of these changes weaken any risk control; they are pure name
corrections. Flagged here rather than silently renamed.
