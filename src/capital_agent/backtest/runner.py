"""Backtest runner. Fetches historical bars from Capital.com via the same
MCP client the scheduler uses, then applies the strategy decision series
and prints a signal timeline. No trades are placed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..logging_config import get_logger
from ..mcp_client import lifespan_mcp
from .rsi_strategy import decide_series

log = get_logger(__name__)


async def run_backtest(epic: str, resolution: str = "MINUTE_15",
                       max_bars: int = 400,
                       from_iso: str | None = None,
                       to_iso: str | None = None) -> dict[str, Any]:
    """Fetch bars and print/return the decision series."""
    async with lifespan_mcp() as mcp:
        args: dict[str, Any] = {"epic": epic, "resolution": resolution, "max": max_bars}
        if from_iso:
            args["from_date"] = from_iso
        if to_iso:
            args["to_date"] = to_iso
        payload = await mcp.call("cap_market_prices", args, timeout_s=30)

    if not isinstance(payload, dict) or "prices" not in payload:
        raise RuntimeError(f"unexpected cap_market_prices payload: {str(payload)[:200]}")

    prices = payload["prices"]
    highs = [float(p["highPrice"]["bid"]) for p in prices]
    lows = [float(p["lowPrice"]["bid"]) for p in prices]
    closes = [float(p["closePrice"]["bid"]) for p in prices]
    ts = [str(p.get("snapshotTime") or p.get("snapshotTimeUTC")) for p in prices]

    decisions = decide_series(highs, lows, closes)

    signals = []
    counts = {"enter_long": 0, "enter_short": 0, "hold": 0}
    for t, close, d in zip(ts, closes, decisions):
        counts[d.kind] = counts.get(d.kind, 0) + 1
        if d.kind != "hold":
            signals.append({
                "ts": t, "close": close, "kind": d.kind,
                "rsi": round(d.rsi or 0, 2),
                "atr": round(d.atr or 0, 6),
                "stop_distance": round(d.stop_distance or 0, 6),
                "reason": d.reason,
            })

    summary = {
        "epic": epic,
        "resolution": resolution,
        "bar_count": len(prices),
        "counts": counts,
        "first_ts": ts[0] if ts else None,
        "last_ts": ts[-1] if ts else None,
        "last_close": closes[-1] if closes else None,
        "last_rsi": round(decisions[-1].rsi, 2) if decisions and decisions[-1].rsi is not None else None,
        "last_atr": round(decisions[-1].atr, 6) if decisions and decisions[-1].atr is not None else None,
        "signal_count": len(signals),
        "signals": signals[-20:],   # tail — the full list gets long
    }
    print(json.dumps(summary, indent=2, default=str))
    return summary
