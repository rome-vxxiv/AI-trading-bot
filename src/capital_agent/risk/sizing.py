"""Position-size math. Given equity, risk %, stop distance, and the
instrument's dealing rules (min/step size), returns an integer-quantized
size or None if the calculated size is below the minimum.

The formula:
    risk_amount   = equity * risk_pct
    raw_size      = risk_amount / stop_distance     # units per $1 of price move
    size          = quantize(max(raw_size, min_size), step_size)
    if size > max_size          -> cap at max_size
    if raw_size < min_size      -> return None (position too small to be worth it)

For CFD instruments the point-value is 1 (a $1 price move on 1 unit
loses $1), so raw_size is directly in units. Non-USD accounts and FX
crosses complicate this — those become instrument-specific overrides in
a later step. For now BTCUSD, ETHUSD, XAUUSD (GOLD), and the major
indices size correctly with this formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    size: float
    reason: str
    risk_amount: float
    raw_size: float


def compute_size(*, equity: float, risk_pct: float, stop_distance: float,
                 min_size: float, step_size: float,
                 max_size: float | None = None) -> SizingResult:
    if equity <= 0:
        return SizingResult(False, 0.0, "equity_not_positive", 0.0, 0.0)
    if risk_pct <= 0 or risk_pct > 0.1:
        return SizingResult(False, 0.0, f"risk_pct_out_of_range:{risk_pct}", 0.0, 0.0)
    if stop_distance <= 0:
        return SizingResult(False, 0.0, "stop_distance_not_positive", 0.0, 0.0)
    if min_size <= 0 or step_size <= 0:
        return SizingResult(False, 0.0, "min_or_step_not_positive", 0.0, 0.0)

    risk_amount = equity * risk_pct
    raw = risk_amount / stop_distance

    if raw < min_size:
        return SizingResult(False, 0.0,
                            f"raw_size_{raw:.6f}_below_min_{min_size}",
                            risk_amount, raw)

    quantized = math.floor(raw / step_size) * step_size
    if quantized < min_size:
        quantized = min_size

    if max_size is not None and quantized > max_size:
        return SizingResult(True, max_size,
                            f"capped_at_max_size_{max_size}",
                            risk_amount, raw)

    return SizingResult(True, quantized, "ok", risk_amount, raw)
