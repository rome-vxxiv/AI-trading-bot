"""Trade-outcome simulation. `rsi_strategy.decide_series` only says when
a signal fired -- it never says what would have happened to the trade.
This module walks forward from each signal to a stop or target exit
against subsequent bars, so backtest output can answer "would this have
made money" instead of just "when did RSI cross."

Assumptions, stated because OHLC bars don't reveal intrabar sequencing:
- Entry fills at the signal bar's own close (same price the live
  playbook previews from).
- If a single bar's range touches both the stop and the target, the
  stop wins -- worst case, since we can't know which happened first.
- One open position at a time, mirroring risk.yaml's
  max_positions_total=1: a signal while a trade is open is skipped,
  not queued.
- No spread, slippage, financing, or commission. Real results will run
  behind this by some amount this module does not estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .rsi_strategy import Decision

ExitReason = Literal["stop", "target", "end_of_data"]
Direction = Literal["long", "short"]


@dataclass(frozen=True)
class TradeResult:
    entry_index: int
    entry_ts: str
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    exit_index: int
    exit_ts: str
    exit_price: float
    exit_reason: ExitReason
    pnl_r: float  # signed multiple of initial risk: +1.5 = full target, -1.0 = full stop


@dataclass(frozen=True)
class BacktestSummary:
    trade_count: int          # closed trades only
    wins: int
    losses: int
    open_at_end: int
    win_rate: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    expectancy_r: float | None
    total_r: float            # includes the still-open trade, marked to market
    max_drawdown_r: float
    profit_factor: float | None
    trades: list[TradeResult]


def simulate_trades(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    ts: Sequence[str], decisions: Sequence[Decision],
    *, profit_atr_multiple: float = 3.0,
) -> BacktestSummary:
    n = len(closes)
    if not (len(highs) == len(lows) == len(ts) == len(decisions) == n):
        raise ValueError("highs/lows/closes/ts/decisions must all be the same length")

    trades: list[TradeResult] = []
    i = 0
    while i < n:
        d = decisions[i]
        if d.kind == "hold" or d.stop_distance is None or d.atr is None:
            i += 1
            continue

        direction: Direction = "long" if d.kind == "enter_long" else "short"
        entry_price = closes[i]
        stop_dist = d.stop_distance
        target_dist = profit_atr_multiple * d.atr
        if direction == "long":
            stop_price = entry_price - stop_dist
            target_price = entry_price + target_dist
        else:
            stop_price = entry_price + stop_dist
            target_price = entry_price - target_dist

        trade = _walk_to_exit(
            highs, lows, closes, ts, start=i + 1,
            direction=direction, entry_index=i, entry_price=entry_price,
            stop_price=stop_price, target_price=target_price, stop_dist=stop_dist,
        )
        trades.append(trade)
        # One position at a time: resume scanning for new signals only
        # after this one has closed (or at end of data, stop entirely).
        i = trade.exit_index + 1 if trade.exit_reason != "end_of_data" else n

    return summarize_trades(trades)


def _walk_to_exit(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    ts: Sequence[str], *, start: int, direction: Direction, entry_index: int,
    entry_price: float, stop_price: float, target_price: float, stop_dist: float,
) -> TradeResult:
    n = len(closes)
    for j in range(start, n):
        hi, lo = highs[j], lows[j]
        if direction == "long":
            hit_stop = lo <= stop_price
            hit_target = hi >= target_price
        else:
            hit_stop = hi >= stop_price
            hit_target = lo <= target_price
        if hit_stop:  # stop wins when a bar touches both -- conservative
            exit_price, exit_reason = stop_price, "stop"
        elif hit_target:
            exit_price, exit_reason = target_price, "target"
        else:
            continue
        return TradeResult(
            entry_index=entry_index, entry_ts=ts[entry_index], direction=direction,
            entry_price=entry_price, stop_price=stop_price, target_price=target_price,
            exit_index=j, exit_ts=ts[j], exit_price=exit_price, exit_reason=exit_reason,
            pnl_r=_pnl_r(direction, entry_price, exit_price, stop_dist),
        )
    # Ran off the end of data still open -- mark to the last close.
    last = n - 1
    return TradeResult(
        entry_index=entry_index, entry_ts=ts[entry_index], direction=direction,
        entry_price=entry_price, stop_price=stop_price, target_price=target_price,
        exit_index=last, exit_ts=ts[last], exit_price=closes[last], exit_reason="end_of_data",
        pnl_r=_pnl_r(direction, entry_price, closes[last], stop_dist),
    )


def _pnl_r(direction: Direction, entry_price: float, exit_price: float, stop_dist: float) -> float:
    raw = (exit_price - entry_price) if direction == "long" else (entry_price - exit_price)
    return raw / stop_dist


def summarize_trades(trades: list[TradeResult]) -> BacktestSummary:
    closed = [t for t in trades if t.exit_reason != "end_of_data"]
    open_at_end = len(trades) - len(closed)
    wins = [t for t in closed if t.pnl_r > 0]
    losses = [t for t in closed if t.pnl_r <= 0]

    peak = cum = max_dd = 0.0
    for t in closed:
        cum += t.pnl_r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    gross_win = sum(t.pnl_r for t in wins)
    gross_loss = -sum(t.pnl_r for t in losses)

    return BacktestSummary(
        trade_count=len(closed),
        wins=len(wins),
        losses=len(losses),
        open_at_end=open_at_end,
        win_rate=(len(wins) / len(closed)) if closed else None,
        avg_win_r=(gross_win / len(wins)) if wins else None,
        avg_loss_r=(-gross_loss / len(losses)) if losses else None,
        expectancy_r=(sum(t.pnl_r for t in closed) / len(closed)) if closed else None,
        total_r=sum(t.pnl_r for t in trades),
        max_drawdown_r=max_dd,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        trades=trades,
    )
