"""Pure-Python indicator math. Wilder-smoothed RSI + ATR so that live
LLM decisions can be replayed against historical bars bit-for-bit.

Contract for callers:
- All series are lists of floats, oldest to newest.
- Return series line up with input length; positions before the warmup
  period are `None` (not NaN) so JSON serialization is trivial.
- No numpy dependency — keeps the wheel small and the math auditable.
"""

from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be > 0")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    window_sum = float(sum(values[:period]))
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def rsi_wilder(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder-smoothed RSI. Same formula the strategy playbook uses."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        g = gains[i - 1]
        loss_ = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss_) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_wilder(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder-smoothed Average True Range. Requires len(highs)=len(lows)=len(closes)."""
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("highs/lows/closes must have equal length")
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    trs: list[float] = [highs[0] - lows[0]]  # placeholder for i=0
    for i in range(1, n):
        prev_close = closes[i - 1]
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(prev_close - lows[i]),
        ))

    atr = sum(trs[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out
