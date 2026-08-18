"""Python-side indicator pre-compute. Fetches bars via the MCP client,
computes RSI-14 and ATR-14 using our own backtest module, then writes a
tick-context.json the playbook reads via the Read tool.

Result: the LLM never does arithmetic. It reads pre-computed numbers,
applies the rule, and previews. Latency drops from ~4 min to ~15 s AND
live decisions match the backtest bit-for-bit — because the same code
runs on the same bars in both places.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..backtest.indicators import atr_wilder, rsi_wilder
from ..logging_config import get_logger
from ..mcp_client import MCPClient
from ..risk.policy import RiskPolicy

log = get_logger(__name__)


async def prepare_tick_context(*, mcp: MCPClient, epic: str, strategy_id: str,
                               resolution: str, max_bars: int,
                               state_dir: Path,
                               account_balance: float | None,
                               policy: RiskPolicy) -> dict[str, Any] | None:
    """Return the context dict on success, None if bars couldn't be
    fetched or the series is too short for indicators."""
    payload = await mcp.call("cap_market_prices",
                             {"epic": epic, "resolution": resolution,
                              "max": max_bars}, timeout_s=20)
    if not isinstance(payload, dict) or "prices" not in payload:
        log.warning("preflight.bars_bad_payload",
                    epic=epic, payload_head=str(payload)[:200])
        return None

    prices = payload["prices"]
    if len(prices) < 20:
        log.warning("preflight.too_few_bars", epic=epic, n=len(prices))
        return None

    closes = [float(p["closePrice"]["bid"]) for p in prices]
    highs = [float(p["highPrice"]["bid"]) for p in prices]
    lows = [float(p["lowPrice"]["bid"]) for p in prices]

    rsi_series = rsi_wilder(closes, 14)
    atr_series = atr_wilder(highs, lows, closes, 14)

    last_close = closes[-1]
    last_rsi = rsi_series[-1]
    last_atr = atr_series[-1]

    if last_rsi is None or last_atr is None:
        log.warning("preflight.indicator_none",
                    epic=epic, last_rsi=last_rsi, last_atr=last_atr)
        return None

    stop_distance = round(2.0 * last_atr, 6)
    profit_distance = round(3.0 * last_atr, 6)

    # Suggested size for the LLM to pass to preview. If we can't compute
    # (no equity), fall back to CAP_MAX_POSITION_SIZE floor 0.01.
    suggested_size = 0.01
    equity_hint = account_balance if isinstance(account_balance, int | float) else None
    if equity_hint and equity_hint > 0 and stop_distance > 0:
        risk_amt = equity_hint * policy.risk_pct_per_trade
        raw = risk_amt / stop_distance
        suggested_size = max(0.01, round(raw, 3))

    ctx = {
        "epic": epic,
        "strategy_id": strategy_id,
        "ts_utc": datetime.now(UTC).isoformat(),
        "resolution": resolution,
        "candle_count": len(prices),
        "last_close": last_close,
        "rsi_14": round(last_rsi, 4),
        "atr_14": round(last_atr, 6),
        "stop_distance": stop_distance,
        "profit_distance": profit_distance,
        "suggested_size": suggested_size,
        "risk_pct_per_trade": policy.risk_pct_per_trade,
        "account_balance_hint": equity_hint,
        "policy_thresholds": {
            "rsi_oversold": 30, "rsi_overbought": 70,
            "atr_stop_multiple": 2.0, "atr_profit_multiple": 3.0,
        },
        "instructions_for_llm": (
            "Use these pre-computed numbers directly; do not recompute "
            "RSI or ATR. Apply the decision rule to rsi_14 and preview "
            "(never execute) if signal fires."
        ),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tick-context.json").write_text(
        json.dumps(ctx, indent=2), encoding="utf-8")
    log.info("preflight.context_ready", epic=epic, strategy_id=strategy_id,
             last_close=last_close, rsi_14=ctx["rsi_14"], atr_14=ctx["atr_14"])
    return ctx
