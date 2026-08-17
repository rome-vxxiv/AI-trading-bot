"""Trend-filtered RSI candidate. Same RSI/ATR math as the shipped
rsi_mean_reversion rule, but a signal only fires when it agrees with
an SMA trend filter -- these tests lock that gating behavior."""

from capital_agent.backtest.rsi_trend_filtered import (
    decide_series_trend_filtered,
    decide_trend_filtered,
)


def test_oversold_in_uptrend_enters_long():
    d = decide_trend_filtered(rsi_value=25.0, atr_value=1.5, sma_value=100.0, close_value=105.0)
    assert d.kind == "enter_long"
    assert d.stop_distance == 3.0


def test_oversold_in_downtrend_is_held_not_faded():
    """Price below the SMA (downtrend) but RSI oversold -- counter-trend,
    must NOT enter, unlike the plain rsi_mean_reversion rule."""
    d = decide_trend_filtered(rsi_value=25.0, atr_value=1.5, sma_value=100.0, close_value=95.0)
    assert d.kind == "hold"
    assert "against the SMA trend" in d.reason


def test_overbought_in_downtrend_enters_short():
    d = decide_trend_filtered(rsi_value=75.0, atr_value=1.5, sma_value=100.0, close_value=95.0)
    assert d.kind == "enter_short"
    assert d.stop_distance == 3.0


def test_overbought_in_uptrend_is_held_not_faded():
    d = decide_trend_filtered(rsi_value=75.0, atr_value=1.5, sma_value=100.0, close_value=105.0)
    assert d.kind == "hold"
    assert "against the SMA trend" in d.reason


def test_neutral_rsi_holds_regardless_of_trend():
    d = decide_trend_filtered(rsi_value=50.0, atr_value=1.5, sma_value=100.0, close_value=105.0)
    assert d.kind == "hold"
    assert "neutral zone" in d.reason


def test_holds_when_sma_not_yet_valid():
    d = decide_trend_filtered(rsi_value=25.0, atr_value=1.5, sma_value=None, close_value=105.0)
    assert d.kind == "hold"
    assert "sma" in d.reason


def test_holds_when_atr_missing():
    d = decide_trend_filtered(rsi_value=25.0, atr_value=None, sma_value=100.0, close_value=105.0)
    assert d.kind == "hold"
    assert "atr" in d.reason


def test_series_warmup_is_hold_until_longest_period_is_valid():
    """sma_period=20 here is longer than rsi/atr's default 14, so the
    series must stay in warmup (hold) until bar 20, not bar 14."""
    n = 25
    closes = [100.0 + (i % 3) for i in range(n)]  # mild oscillation, no trend
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    decisions = decide_series_trend_filtered(highs, lows, closes, sma_period=20)
    assert len(decisions) == n
    for d in decisions[:19]:
        assert d.kind == "hold"
