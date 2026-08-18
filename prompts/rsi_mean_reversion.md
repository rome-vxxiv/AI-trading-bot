Analyze {EPIC} on Capital.com and PREVIEW a trade if RSI signal fires. Never execute.

Step 1: Read the file ./state/tick-context.json using the Read tool. It was written by the Python side just before this invocation and contains pre-computed indicators for {EPIC}. Use those numbers directly. Do NOT recompute anything.

Fields you will see in tick-context.json:
- epic
- ts_utc
- candle_count
- last_close
- rsi_14
- atr_14
- stop_distance   (already 2 * ATR, rounded)
- profit_distance (already 3 * ATR, rounded)
- suggested_size  (already sized to policy risk %, but rounded to instrument min)
- account_balance_hint

Step 2: Decide.
- If rsi_14 <= 30 then decision = "enter_long"
- Else if rsi_14 >= 70 then decision = "enter_short"
- Else decision = "hold"

Step 3: If decision is "hold", skip to Step 5.

Step 4: Call mcp__capital-com__cap_trade_preview_position with:
- epic: {EPIC}
- direction: "BUY" if decision == "enter_long", else "SELL"
- size: the suggested_size value from tick-context.json
- stop_distance: the stop_distance value from tick-context.json
- profit_distance: the profit_distance value from tick-context.json

Note the preview_id and all_checks_passed fields from the response.

Step 5: Reply with EXACTLY this JSON, nothing else, no markdown fences:
{"epic": "{EPIC}", "candle_count": 0, "last_close": 0.0, "rsi_14": 0.0, "atr_14": 0.0, "decision": "hold", "preview_id": null, "preview_all_checks_passed": null, "stop_distance_used": null, "reason": ""}

Fill values from tick-context.json. For "hold" leave preview_id, preview_all_checks_passed, and stop_distance_used as null. For an entry decision, populate them from your preview call. Put a one-sentence explanation citing rsi_14 in reason.

Rules that override anything else:
- You are NEVER allowed to call mcp__capital-com__cap_trade_execute_position or any cap_trade_execute_* tool.
- Do not call any cap_trade_positions_close or cap_trade_orders_cancel tool.
- Do not modify tick-context.json or any other file.
- If any tool call errors, still reply with the JSON above using decision "hold" and put the error in reason.
