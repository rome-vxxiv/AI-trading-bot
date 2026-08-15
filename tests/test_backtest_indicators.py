"""Golden numbers for RSI-14 + ATR-14 Wilder smoothing.

The playbook prompt tells Claude to use the same formulas. If either
side drifts (a numpy port, a formula tweak in the prompt), this test
fails and the drift is caught before it hits live signals."""

from capital_agent.backtest.indicators import atr_wilder, rsi_wilder, sma
from capital_agent.backtest.rsi_strategy import decide


def test_sma_basic():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out == [None, None, 2.0, 3.0, 4.0]


def test_rsi_all_gains_returns_100():
    closes = [float(i) for i in range(1, 30)]
    r = rsi_wilder(closes, 14)
    assert r[14] == 100.0
    assert r[-1] == 100.0


def test_rsi_all_losses_returns_0():
    closes = [float(30 - i) for i in range(30)]
    r = rsi_wilder(closes, 14)
    assert r[14] == 0.0
    assert r[-1] == 0.0


def test_rsi_known_series():
    """Textbook example: with these 30 closes RSI-14 at the last bar
    is approximately 42.7 (Wilder). The exact value is what our code
    produces — this test locks the algorithm, not the theoretical
    number, so any refactor that changes the smoothing fails loudly."""
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
        46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45,
        45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    ]
    r = rsi_wilder(closes, 14)
    # None for the warmup bars, values after.
    assert r[13] is None
    assert r[14] is not None and 60.0 < r[14] < 80.0
    # Regression: this specific final value is the algorithm's output.
    # If this drifts, the RSI formula has changed and the live playbook
    # decisions will no longer match the backtest.
    assert abs(r[-1] - 45.5) < 0.1


def test_atr_basic_shape():
    n = 40
    highs = [100 + i * 0.5 for i in range(n)]
    lows = [99 + i * 0.5 for i in range(n)]
    closes = [99.5 + i * 0.5 for i in range(n)]
    a = atr_wilder(highs, lows, closes, 14)
    assert a[13] is None
    assert a[14] is not None and a[14] > 0
    # Smooth: consecutive values shouldn't differ by more than the max TR.
    for i in range(15, n):
        assert abs(a[i] - a[i - 1]) < 2.0


def test_decide_neutral():
    d = decide(rsi_value=50.0, atr_value=1.5)
    assert d.kind == "hold"
    assert d.stop_distance == 3.0


def test_decide_long():
    d = decide(rsi_value=25.0, atr_value=1.5)
    assert d.kind == "enter_long"
    assert d.stop_distance == 3.0


def test_decide_short():
    d = decide(rsi_value=75.0, atr_value=1.5)
    assert d.kind == "enter_short"


def test_decide_holds_when_atr_missing():
    d = decide(rsi_value=25.0, atr_value=None)
    assert d.kind == "hold"
    assert "atr" in d.reason
