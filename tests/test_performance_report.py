"""Real trade-outcome reporting. Unit tests on the pure aggregation
functions plus a couple of DB round-trips through actual Signal rows,
matching the fixture pattern in test_state.py / test_driver.py."""

from pathlib import Path

import pytest

from capital_agent.performance_report import _group_by, _summarize, build_performance_report
from capital_agent.state import Signal, init_db, session_scope
from capital_agent.state.db import get_engine


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path, monkeypatch):
    import capital_agent.state.db as db_mod
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_maker", None)
    get_engine(tmp_path)
    yield


def _t(outcome, pnl, epic="GOLD", strategy_id="rsi_mean_reversion_live", decision="enter_long"):
    return {"ts": "2026-01-01T00:00:00", "epic": epic, "strategy_id": strategy_id,
            "decision": decision, "outcome": outcome, "pnl": pnl}


# ---- pure aggregation -------------------------------------------

def test_summarize_basic_stats():
    r = _summarize([_t("win", 10.0), _t("win", 5.0), _t("loss", -8.0)])
    assert r.trade_count == 3
    assert r.wins == 2 and r.losses == 1
    assert r.win_rate == pytest.approx(2 / 3)
    assert r.total_pnl == pytest.approx(7.0)
    assert r.avg_win == pytest.approx(7.5)
    assert r.avg_loss == pytest.approx(-8.0)
    assert r.profit_factor == pytest.approx(15.0 / 8.0)


def test_summarize_empty_gives_none_not_errors():
    r = _summarize([])
    assert r.trade_count == 0
    assert r.win_rate is None
    assert r.avg_win is None
    assert r.avg_loss is None
    assert r.profit_factor is None
    assert r.total_pnl == 0.0


def test_summarize_counts_tagged_outcome_without_recoverable_pnl():
    """A trade tagged win/loss but with no recoverable pnl (broker
    transaction lookup failed) still counts toward trade_count/win_rate;
    only the currency stats fall back to the priced subset."""
    r = _summarize([_t("win", None), _t("loss", -5.0)])
    assert r.trade_count == 2
    assert r.wins == 1 and r.losses == 1
    assert r.total_pnl == pytest.approx(-5.0)
    assert r.avg_loss == pytest.approx(-5.0)
    assert r.avg_win is None  # the one win had no priced pnl


def test_group_by_epic():
    tagged = [_t("win", 10.0, epic="GOLD"), _t("loss", -5.0, epic="GOLD"),
              _t("win", 3.0, epic="BTCUSD")]
    groups = _group_by(tagged, "epic")
    assert groups["GOLD"]["trade_count"] == 2
    assert groups["GOLD"]["wins"] == 1
    assert groups["GOLD"]["total_pnl"] == pytest.approx(5.0)
    assert groups["BTCUSD"]["trade_count"] == 1
    assert groups["BTCUSD"]["total_pnl"] == pytest.approx(3.0)


# ---- DB round-trip -------------------------------------------------

async def test_build_report_excludes_holds_and_still_open(tmp_path):
    await init_db(tmp_path)
    async with session_scope() as s:
        s.add(Signal(strategy_id="rsi_mean_reversion_live", epic="GOLD", decision="hold",
                     model_output_json={"decision": "hold"}))
        s.add(Signal(strategy_id="rsi_mean_reversion_live", epic="GOLD", decision="enter_long",
                     model_output_json={"decision": "enter_long"}))  # no outcome yet
        s.add(Signal(strategy_id="rsi_mean_reversion_live", epic="GOLD", decision="enter_long",
                     model_output_json={"decision": "enter_long", "outcome": "win",
                                        "_execute": {"pnl_realized": 12.5}}))
        s.add(Signal(strategy_id="rsi_mean_reversion_live", epic="BTCUSD", decision="enter_short",
                     model_output_json={"decision": "enter_short", "outcome": "loss",
                                        "_execute": {"pnl_realized": -4.0}}))
        await s.commit()

    report = await build_performance_report()
    assert report.trade_count == 2
    assert report.wins == 1 and report.losses == 1
    assert report.total_pnl == pytest.approx(8.5)
    assert set(report.by_epic.keys()) == {"GOLD", "BTCUSD"}


async def test_build_report_epic_filter(tmp_path):
    await init_db(tmp_path)
    async with session_scope() as s:
        s.add(Signal(strategy_id="s1", epic="GOLD", decision="enter_long",
                     model_output_json={"outcome": "win", "_execute": {"pnl_realized": 1.0}}))
        s.add(Signal(strategy_id="s1", epic="BTCUSD", decision="enter_long",
                     model_output_json={"outcome": "win", "_execute": {"pnl_realized": 2.0}}))
        await s.commit()

    report = await build_performance_report(epic="GOLD")
    assert report.trade_count == 1
    assert report.total_pnl == pytest.approx(1.0)


async def test_build_report_empty_db_gives_zero_not_errors(tmp_path):
    await init_db(tmp_path)
    report = await build_performance_report()
    assert report.trade_count == 0
    assert report.win_rate is None
    assert report.by_epic == {}
