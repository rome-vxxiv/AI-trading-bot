"""Pure logic inside backtest/runner.py that doesn't need a live MCP
session. _stitch_chronological is what backtest-multi relies on to avoid
force-closing trades at artificial window boundaries -- these tests lock
its dedup behavior directly against hand-built fetch results."""

from capital_agent.backtest.runner import _stitch_chronological


def _chunk(ts: list[str]) -> tuple[list[float], list[float], list[float], list[str]]:
    n = len(ts)
    return ([1.0] * n, [1.0] * n, [float(i) for i in range(n)], ts)


def test_single_chunk_passes_through_unchanged():
    fetched = [_chunk(["t2", "t3", "t4"])]
    highs, lows, closes, ts, meta = _stitch_chronological(fetched)
    assert ts == ["t2", "t3", "t4"]
    assert len(meta) == 1
    assert meta[0]["bars_dropped_as_overlap"] == 0


def test_two_contiguous_chunks_stitch_cleanly():
    # Fetched newest-first, as run_backtest_multi_window collects them.
    newer = _chunk(["t3", "t4", "t5"])
    older = _chunk(["t0", "t1", "t2"])
    highs, lows, closes, ts, meta = _stitch_chronological([newer, older])
    assert ts == ["t0", "t1", "t2", "t3", "t4", "t5"]
    assert meta[0]["bars_dropped_as_overlap"] == 0  # older chunk, nothing before it
    assert meta[1]["bars_dropped_as_overlap"] == 0  # newer chunk, no overlap


def test_overlapping_chunks_drop_the_duplicate_tail():
    """This is the real bug: consecutive fetches were observed to overlap
    by a few bars rather than being perfectly contiguous."""
    newer = _chunk(["t2", "t3", "t4"])   # t2 duplicates older's last bar
    older = _chunk(["t0", "t1", "t2"])
    highs, lows, closes, ts, meta = _stitch_chronological([newer, older])
    assert ts == ["t0", "t1", "t2", "t3", "t4"]  # t2 kept once, not twice
    assert meta[1]["bars_dropped_as_overlap"] == 1


def test_fully_overlapping_chunk_drops_everything():
    newer = _chunk(["t0", "t1"])   # entirely within older's range
    older = _chunk(["t0", "t1", "t2"])
    highs, lows, closes, ts, meta = _stitch_chronological([newer, older])
    assert ts == ["t0", "t1", "t2"]
    assert meta[1]["bars_dropped_as_overlap"] == 2


def test_empty_input_gives_empty_output():
    highs, lows, closes, ts, meta = _stitch_chronological([])
    assert ts == [] and closes == [] and meta == []


def test_values_travel_with_their_timestamps_not_just_ts_list():
    """Regression guard: highs/lows/closes must be sliced with the same
    `start` offset as ts, not accidentally left unsliced or misaligned."""
    newer = ([20.0, 21.0], [19.0, 19.5], [20.5, 21.5], ["t1", "t2"])
    older = ([10.0], [9.0], [10.5], ["t0"])
    highs, lows, closes, ts, meta = _stitch_chronological([newer, older])
    assert ts == ["t0", "t1", "t2"]
    assert closes == [10.5, 20.5, 21.5]
    assert highs == [10.0, 20.0, 21.0]
    assert lows == [9.0, 19.0, 19.5]
