Analyze {EPIC} on Capital.com right now. Do these steps and stop.

Step 1: Call mcp__capital-com__cap_market_prices with epic {EPIC}, resolution MINUTE_15, max 30.
Step 2: Call mcp__capital-com__cap_market_sentiment with market_id {EPIC}.
Step 3: Take last_close = closePrice.bid of the most recent candle. Take sma_ref = closePrice.bid of the candle 20 positions before the last one, or the earliest one if fewer than 20 are available.
Step 4: Classify verdict as "up" when last_close is more than sma_ref times 1.005, "down" when last_close is less than sma_ref times 0.995, otherwise "flat".

Your entire reply must be ONE JSON object, no markdown, no prose. Use this schema exactly:
{"epic": "{EPIC}", "candle_count": 0, "last_close": 0.0, "sma_ref": 0.0, "sentiment_long_pct": null, "verdict": "flat", "reason": ""}

Fill in the real numbers from your tool calls, choose the correct verdict, and put a one-sentence explanation with both numbers in reason. Never call any tool other than the two above. Never open, modify, or cancel a position or order.
