"""Backtest runner. Fetches historical bars from Capital.com via the same
MCP client the scheduler uses, then applies the strategy decision series
and prints a signal timeline. No trades are placed.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ..logging_config import get_logger
from ..mcp_client import MCPClient, lifespan_mcp
from .rsi_strategy import decide_series
from .rsi_trend_filtered import decide_series_trend_filtered
from .simulate import TradeResult, simulate_trades, summarize_trades

log = get_logger(__name__)

_DECISION_SERIES_FNS = {
    "rsi_mean_reversion": decide_series,
    "rsi_trend_filtered": decide_series_trend_filtered,
}


async def _fetch_bars(mcp: MCPClient, epic: str, resolution: str, max_bars: int,
                      from_iso: str | None = None,
                      to_iso: str | None = None) -> tuple[list[float], list[float], list[float], list[str]]:
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
    return highs, lows, closes, ts


_SIM_NOTE = ("stop=2xATR, target=3xATR, one position at a time (mirrors "
            "risk.yaml max_positions_total=1), entry at signal-bar close, "
            "no spread/slippage/financing modeled -- real results will run behind this")


def _sim_to_dict(sim, *, recent_trades: int = 20) -> dict[str, Any]:
    return {
        "note": _SIM_NOTE,
        "trade_count": sim.trade_count,
        "wins": sim.wins,
        "losses": sim.losses,
        "open_at_end": sim.open_at_end,
        "win_rate": round(sim.win_rate, 3) if sim.win_rate is not None else None,
        "avg_win_r": round(sim.avg_win_r, 3) if sim.avg_win_r is not None else None,
        "avg_loss_r": round(sim.avg_loss_r, 3) if sim.avg_loss_r is not None else None,
        "expectancy_r": round(sim.expectancy_r, 3) if sim.expectancy_r is not None else None,
        "total_r": round(sim.total_r, 3),
        "max_drawdown_r": round(sim.max_drawdown_r, 3),
        "profit_factor": round(sim.profit_factor, 3) if sim.profit_factor is not None else None,
        "recent_trades": [asdict(t) for t in sim.trades[-recent_trades:]] if recent_trades else [],
    }


async def run_backtest(epic: str, resolution: str = "MINUTE_15",
                       max_bars: int = 400,
                       from_iso: str | None = None,
                       to_iso: str | None = None,
                       strategy: str = "rsi_mean_reversion") -> dict[str, Any]:
    """Fetch bars and print/return the decision series + trade simulation."""
    if strategy not in _DECISION_SERIES_FNS:
        raise ValueError(f"unknown backtest strategy {strategy!r}, "
                         f"known: {list(_DECISION_SERIES_FNS.keys())}")

    async with lifespan_mcp() as mcp:
        highs, lows, closes, ts = await _fetch_bars(
            mcp, epic, resolution, max_bars, from_iso=from_iso, to_iso=to_iso)

    decisions = _DECISION_SERIES_FNS[strategy](highs, lows, closes)

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

    sim = simulate_trades(highs, lows, closes, ts, decisions)

    summary = {
        "epic": epic,
        "strategy": strategy,
        "resolution": resolution,
        "bar_count": len(closes),
        "counts": counts,
        "first_ts": ts[0] if ts else None,
        "last_ts": ts[-1] if ts else None,
        "last_close": closes[-1] if closes else None,
        "last_rsi": round(decisions[-1].rsi, 2) if decisions and decisions[-1].rsi is not None else None,
        "last_atr": round(decisions[-1].atr, 6) if decisions and decisions[-1].atr is not None else None,
        "signal_count": len(signals),
        "signals": signals[-20:],   # tail — the full list gets long
        "trade_simulation": _sim_to_dict(sim),
    }
    print(json.dumps(summary, indent=2, default=str))
    return summary


async def run_backtest_multi_window(
    epic: str, resolution: str = "MINUTE_15", bars_per_window: int = 1000,
    num_windows: int = 4, strategy: str = "rsi_mean_reversion",
    end_iso: str | None = None,
) -> dict[str, Any]:
    """Walk backward through `num_windows` consecutive, non-overlapping
    chunks of `bars_per_window` bars each (each window's `to_date` is the
    previous window's earliest timestamp) and combine every trade across
    all of them into one aggregate. One MCP session for the whole walk.

    Exists because a single request is capped at 1000 bars by Capital.com
    -- a strategy selective enough to produce only a handful of trades per
    1000-bar window (e.g. a long-SMA trend filter) needs several windows
    stitched together before trade_count is large enough to mean anything.
    """
    if strategy not in _DECISION_SERIES_FNS:
        raise ValueError(f"unknown backtest strategy {strategy!r}, "
                         f"known: {list(_DECISION_SERIES_FNS.keys())}")
    decide_fn = _DECISION_SERIES_FNS[strategy]

    windows: list[dict[str, Any]] = []
    all_trades: list[TradeResult] = []
    cursor_to = end_iso

    async with lifespan_mcp() as mcp:
        for _ in range(num_windows):
            try:
                highs, lows, closes, ts = await _fetch_bars(
                    mcp, epic, resolution, bars_per_window, to_iso=cursor_to)
            except Exception as exc:  # noqa: BLE001
                log.warning("multi_window.fetch_error", error=str(exc)[:200])
                break
            if not closes:
                break

            decisions = decide_fn(highs, lows, closes)
            sim = simulate_trades(highs, lows, closes, ts, decisions)
            all_trades.extend(sim.trades)
            windows.append({
                "first_ts": ts[0], "last_ts": ts[-1], "bar_count": len(closes),
                "trade_count": sim.trade_count, "wins": sim.wins, "losses": sim.losses,
                "total_r": round(sim.total_r, 3),
            })
            cursor_to = ts[0]  # next window ends where this one begins

    combined = summarize_trades(all_trades)
    result = {
        "epic": epic,
        "strategy": strategy,
        "resolution": resolution,
        "bars_per_window": bars_per_window,
        "windows_requested": num_windows,
        "windows_fetched": len(windows),
        "windows": windows,
        "combined": _sim_to_dict(combined),
    }
    print(json.dumps(result, indent=2, default=str))
    return result
