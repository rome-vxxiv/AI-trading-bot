"""RSI mean-reversion decision rule. This is the SAME rule the live
playbook (prompts/rsi_mean_reversion.md) tells the LLM to apply, so the
backtest and live decisions are identical on identical inputs.

Rule:
- RSI-14 <= oversold  -> enter_long
- RSI-14 >= overbought -> enter_short
- otherwise            -> hold

An entry is only allowed when we also have a valid ATR-14 (for stop
sizing). If ATR is missing, decision degrades to "hold".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .indicators import atr_wilder, rsi_wilder

DecisionKind = Literal["enter_long", "enter_short", "hold"]


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    rsi: float | None
    atr: float | None
    stop_distance: float | None    # 2 * ATR for a long-entry stop
    reason: str


def decide(rsi_value: float | None, atr_value: float | None,
           *, oversold: float = 30.0, overbought: float = 70.0,
           atr_stop_multiple: float = 2.0) -> Decision:
    if rsi_value is None:
        return Decision("hold", rsi_value, atr_value, None,
                        "rsi not yet valid (warmup)")
    if atr_value is None:
        return Decision("hold", rsi_value, atr_value, None,
                        "atr not yet valid (warmup)")
    stop_dist = atr_stop_multiple * atr_value
    if rsi_value <= oversold:
        return Decision("enter_long", rsi_value, atr_value, stop_dist,
                        f"rsi {rsi_value:.2f} <= {oversold}, long with stop {atr_stop_multiple}*ATR")
    if rsi_value >= overbought:
        return Decision("enter_short", rsi_value, atr_value, stop_dist,
                        f"rsi {rsi_value:.2f} >= {overbought}, short with stop {atr_stop_multiple}*ATR")
    return Decision("hold", rsi_value, atr_value, stop_dist,
                    f"rsi {rsi_value:.2f} inside neutral zone [{oversold}, {overbought}]")


def decide_series(highs: Sequence[float], lows: Sequence[float],
                  closes: Sequence[float], *, rsi_period: int = 14,
                  atr_period: int = 14, oversold: float = 30.0,
                  overbought: float = 70.0,
                  atr_stop_multiple: float = 2.0) -> list[Decision]:
    """Compute a Decision for every bar. Used by the backtest CLI to
    replay the live strategy on historical bars."""
    rsis = rsi_wilder(closes, rsi_period)
    atrs = atr_wilder(highs, lows, closes, atr_period)
    return [
        decide(r, a, oversold=oversold, overbought=overbought,
               atr_stop_multiple=atr_stop_multiple)
        for r, a in zip(rsis, atrs)
    ]
