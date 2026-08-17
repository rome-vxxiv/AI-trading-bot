"""Candidate strategy: RSI mean-reversion, but only traded with the
prevailing trend instead of blindly faded in both directions.

The shipped `rsi_mean_reversion` rule takes every RSI<=30/>=70 crossing
regardless of context. Backtesting it across two consecutive ~2-week
GOLD windows (see the conversation, not reproduced here) showed the win
rate and total R sign flipping between windows -- consistent with a
rule that has no real edge and gets hurt by trending markets in both
directions (it was mostly shorting into an uptrend in one window, then
mostly buying into a downtrend in the other).

This variant adds an SMA trend filter: only take a long when price is
above the SMA (buying a dip WITH an uptrend), only take a short when
price is below it (shorting a bounce WITH a downtrend). Counter-trend
crossings are held instead of traded.

This is a candidate under evaluation, not a shipped playbook -- it has
no prompt file and is not registered as a PlaybookSpec or scheduler job.
Backtest it and compare against rsi_mean_reversion before considering
either of those next steps.
"""

from __future__ import annotations

from collections.abc import Sequence

from .indicators import atr_wilder, rsi_wilder, sma
from .rsi_strategy import Decision


def decide_trend_filtered(
    rsi_value: float | None, atr_value: float | None,
    sma_value: float | None, close_value: float | None,
    *, oversold: float = 30.0, overbought: float = 70.0,
    atr_stop_multiple: float = 2.0,
) -> Decision:
    if rsi_value is None:
        return Decision("hold", rsi_value, atr_value, None, "rsi not yet valid (warmup)")
    if atr_value is None:
        return Decision("hold", rsi_value, atr_value, None, "atr not yet valid (warmup)")
    if sma_value is None or close_value is None:
        return Decision("hold", rsi_value, atr_value, None, "sma not yet valid (warmup)")

    stop_dist = atr_stop_multiple * atr_value
    uptrend = close_value > sma_value

    if rsi_value <= oversold and uptrend:
        return Decision("enter_long", rsi_value, atr_value, stop_dist,
                        f"rsi {rsi_value:.2f} <= {oversold} and price above SMA "
                        f"(with-trend), long with stop {atr_stop_multiple}*ATR")
    if rsi_value >= overbought and not uptrend:
        return Decision("enter_short", rsi_value, atr_value, stop_dist,
                        f"rsi {rsi_value:.2f} >= {overbought} and price below SMA "
                        f"(with-trend), short with stop {atr_stop_multiple}*ATR")
    if rsi_value <= oversold or rsi_value >= overbought:
        return Decision("hold", rsi_value, atr_value, stop_dist,
                        f"rsi {rsi_value:.2f} crossed but against the SMA trend -- skipped")
    return Decision("hold", rsi_value, atr_value, stop_dist,
                    f"rsi {rsi_value:.2f} inside neutral zone [{oversold}, {overbought}]")


def decide_series_trend_filtered(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    *, rsi_period: int = 14, atr_period: int = 14, sma_period: int = 50,
    oversold: float = 30.0, overbought: float = 70.0,
    atr_stop_multiple: float = 2.0,
) -> list[Decision]:
    rsis = rsi_wilder(closes, rsi_period)
    atrs = atr_wilder(highs, lows, closes, atr_period)
    smas = sma(closes, sma_period)
    return [
        decide_trend_filtered(r, a, s, c, oversold=oversold, overbought=overbought,
                              atr_stop_multiple=atr_stop_multiple)
        for r, a, s, c in zip(rsis, atrs, smas, closes)
    ]
