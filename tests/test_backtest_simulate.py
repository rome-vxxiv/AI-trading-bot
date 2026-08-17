"""Trade-outcome simulation. Constructed OHLC series with hand-computed
expected exits, so each assertion can be verified by hand against the
stop=2xATR / target=3xATR rule rather than trusting the code under test."""

import pytest

from capital_agent.backtest.rsi_strategy import Decision
from capital_agent.backtest.simulate import simulate_trades

HOLD = Decision("hold", None, None, None, "hold")


def _long_signal(atr: float = 1.0, stop_multiple: float = 2.0) -> Decision:
    return Decision("enter_long", 25.0, atr, stop_multiple * atr, "oversold")


def _short_signal(atr: float = 1.0, stop_multiple: float = 2.0) -> Decision:
    return Decision("enter_short", 75.0, atr, stop_multiple * atr, "overbought")


def test_long_hits_target():
    # entry@100, stop=98, target=103 (3*ATR=3). Bar1 reaches target only.
    highs = [100.5, 104.0]
    lows = [99.5, 99.0]
    closes = [100.0, 103.0]
    ts = ["t0", "t1"]
    decisions = [_long_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    assert sim.trade_count == 1
    t = sim.trades[0]
    assert t.exit_reason == "target"
    assert t.pnl_r == pytest.approx(1.5)


def test_long_hits_stop():
    highs = [100.5, 101.0]
    lows = [99.5, 97.0]  # touches stop=98, not target=103
    closes = [100.0, 98.0]
    ts = ["t0", "t1"]
    decisions = [_long_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    t = sim.trades[0]
    assert t.exit_reason == "stop"
    assert t.pnl_r == pytest.approx(-1.0)


def test_short_hits_target():
    # entry@100, stop=102, target=97. Bar1 low touches target only.
    highs = [100.5, 101.0]
    lows = [99.5, 96.0]
    closes = [100.0, 97.0]
    ts = ["t0", "t1"]
    decisions = [_short_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    t = sim.trades[0]
    assert t.exit_reason == "target"
    assert t.pnl_r == pytest.approx(1.5)


def test_short_hits_stop():
    highs = [100.5, 103.0]  # touches stop=102
    lows = [99.5, 99.0]
    closes = [100.0, 102.0]
    ts = ["t0", "t1"]
    decisions = [_short_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    t = sim.trades[0]
    assert t.exit_reason == "stop"
    assert t.pnl_r == pytest.approx(-1.0)


def test_ambiguous_bar_resolves_to_stop():
    """A bar touching both stop (98) and target (103) in the same bar is
    an unavoidable OHLC ambiguity -- the conservative read (stop) wins."""
    highs = [100.5, 110.0]  # would hit target
    lows = [99.5, 90.0]     # would also hit stop
    closes = [100.0, 95.0]
    ts = ["t0", "t1"]
    decisions = [_long_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    t = sim.trades[0]
    assert t.exit_reason == "stop"
    assert t.pnl_r == pytest.approx(-1.0)


def test_overlapping_signal_is_skipped_while_position_open():
    """A second enter_long at index 1 must never open a second trade
    while the first one (opened at index 0) is still running."""
    highs = [100.5, 100.5, 104.0]
    lows = [99.5, 99.5, 99.0]
    closes = [100.0, 100.0, 103.0]
    ts = ["t0", "t1", "t2"]
    decisions = [_long_signal(), _long_signal(), HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    assert sim.trade_count == 1
    assert sim.trades[0].entry_index == 0


def test_trade_still_open_at_end_of_data():
    highs = [100.5, 100.6, 100.4]
    lows = [99.5, 99.4, 99.6]  # never reaches stop=98 or target=103
    closes = [100.0, 100.2, 100.1]
    ts = ["t0", "t1", "t2"]
    decisions = [_long_signal(), HOLD, HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    assert sim.trade_count == 0          # not counted as closed
    assert sim.open_at_end == 1
    t = sim.trades[0]
    assert t.exit_reason == "end_of_data"
    assert t.exit_price == closes[-1]
    assert t.pnl_r == pytest.approx((100.1 - 100.0) / 2.0)


def test_aggregate_stats_two_wins_one_loss():
    """Three sequential, non-overlapping trades: win, win, loss (in that
    order) so the drawdown is exercised too."""
    highs = [100.5, 104.0, 100.5, 104.0, 100.5, 103.0]
    lows = [99.5, 99.0, 99.5, 99.0, 99.5, 99.0]
    closes = [100.0, 103.0, 100.0, 103.0, 100.0, 102.0]
    ts = [f"t{i}" for i in range(6)]
    decisions = [
        _long_signal(), HOLD,          # trade 1: entry@0, target@1  -> +1.5R
        _long_signal(), HOLD,          # trade 2: entry@2, target@3  -> +1.5R
        _short_signal(), HOLD,         # trade 3: entry@4, stop@5    -> -1.0R
    ]
    sim = simulate_trades(highs, lows, closes, ts, decisions)

    assert sim.trade_count == 3
    assert sim.wins == 2
    assert sim.losses == 1
    assert sim.win_rate == pytest.approx(2 / 3)
    assert sim.avg_win_r == pytest.approx(1.5)
    assert sim.avg_loss_r == pytest.approx(-1.0)
    assert sim.expectancy_r == pytest.approx((1.5 + 1.5 - 1.0) / 3)
    assert sim.total_r == pytest.approx(2.0)
    assert sim.max_drawdown_r == pytest.approx(1.0)   # peak 3.0 -> 2.0
    assert sim.profit_factor == pytest.approx(3.0)     # 3.0 gross win / 1.0 gross loss


def test_no_trades_gives_none_stats_not_errors():
    highs = [100.5, 100.5]
    lows = [99.5, 99.5]
    closes = [100.0, 100.0]
    ts = ["t0", "t1"]
    decisions = [HOLD, HOLD]
    sim = simulate_trades(highs, lows, closes, ts, decisions)
    assert sim.trade_count == 0
    assert sim.win_rate is None
    assert sim.avg_win_r is None
    assert sim.avg_loss_r is None
    assert sim.expectancy_r is None
    assert sim.profit_factor is None
    assert sim.total_r == 0.0
    assert sim.max_drawdown_r == 0.0


def test_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        simulate_trades([1.0], [1.0], [1.0, 2.0], ["t0", "t1"], [HOLD, HOLD])
