"""Driver parsing + session-gating tests. These do NOT spawn Claude Code
or the MCP subprocess — pure logic on canned outputs / fake sessions."""

from datetime import datetime, time
from pathlib import Path

import pytest

from capital_agent.driver.analysis import _extract_verdict_json, _greedy_scan
from capital_agent.scheduler.jobs.analysis import run_analysis_job
from capital_agent.sessions.parser import Guard, InstrumentSession, SessionsConfig, WeekdayTime


# ---- JSON extraction from Claude Code output --------------------

def test_greedy_scan_finds_verdict_object():
    text = "Some reasoning...\n\n{\"epic\": \"GOLD\", \"verdict\": \"neutral\"}"
    obj = _greedy_scan(text)
    assert obj == {"epic": "GOLD", "verdict": "neutral"}


def test_greedy_scan_ignores_non_verdict_objects():
    # First object has no `epic` key so must be skipped.
    text = '{"debug": true}\n\n{"epic": "GOLD", "verdict": "oversold"}'
    obj = _greedy_scan(text)
    assert obj is not None and obj["verdict"] == "oversold"


def test_extract_verdict_from_result_envelope():
    envelope = '{"result": "Here is the analysis:\\n\\n{\\"epic\\": \\"GOLD\\", \\"verdict\\": \\"overbought\\", \\"rsi_14\\": 78.2}"}'
    obj = _extract_verdict_json(envelope)
    assert obj is not None
    assert obj["verdict"] == "overbought"
    assert obj["rsi_14"] == 78.2


def test_extract_verdict_from_raw_json_only():
    envelope = '{"epic": "BTCUSD", "verdict": "neutral"}'
    obj = _extract_verdict_json(envelope)
    assert obj is not None and obj["epic"] == "BTCUSD"


def test_extract_verdict_returns_none_on_garbage():
    assert _extract_verdict_json("not json at all") is None
    assert _extract_verdict_json('{"result": "no json inside this text"}') is None


# ---- Session gating in the scheduler job ------------------------

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path, monkeypatch):
    import capital_agent.state.db as db_mod
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_maker", None)
    from capital_agent.state.db import get_engine
    get_engine(tmp_path)
    yield


def _gold_sessions() -> SessionsConfig:
    return SessionsConfig(
        instruments={
            "GOLD": InstrumentSession(
                epic="GOLD",
                open=WeekdayTime(6, 22, 0),   # SUN 22:00
                close=WeekdayTime(4, 21, 0),  # FRI 21:00
                guards=[Guard(time(21, 0), time(22, 0))],
                continuous=False,
            ),
        },
        session_edge_minutes=5,
    )


async def test_skipped_on_saturday(monkeypatch):
    sessions = _gold_sessions()
    calls: list[str] = []
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.run_analysis_once",
        _fake_runner(calls),
    )
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.datetime",
        _fixed_datetime(datetime(2026, 8, 15, 14, 0)),  # Saturday
    )
    from capital_agent.state import init_db
    from pathlib import Path
    await init_db(Path("."))  # kill switch off by default
    await run_analysis_job("GOLD", sessions, "readonly_gold_15m")
    assert calls == []


async def test_fires_on_wed_noon(monkeypatch):
    sessions = _gold_sessions()
    calls: list[str] = []
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.run_analysis_once",
        _fake_runner(calls),
    )
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.datetime",
        _fixed_datetime(datetime(2026, 8, 12, 12, 0)),  # Wednesday, mid-session
    )
    from capital_agent.state import init_db
    from pathlib import Path
    await init_db(Path("."))
    await run_analysis_job("GOLD", sessions, "readonly_gold_15m")
    assert calls == ["GOLD"]


async def test_skipped_in_daily_guard(monkeypatch):
    sessions = _gold_sessions()
    calls: list[str] = []
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.run_analysis_once",
        _fake_runner(calls),
    )
    # Wed 21:15 UTC → inside DAILY 21:00-22:00 guard
    monkeypatch.setattr(
        "capital_agent.scheduler.jobs.analysis.datetime",
        _fixed_datetime(datetime(2026, 8, 12, 21, 15)),
    )
    from capital_agent.state import init_db
    from pathlib import Path
    await init_db(Path("."))
    await run_analysis_job("GOLD", sessions, "readonly_gold_15m")
    assert calls == []


def _fake_runner(sink: list[str]):
    async def _run(epic: str, strategy_id: str = "readonly_analysis"):
        sink.append(epic)
        return {"epic": epic, "verdict": "neutral", "reason": "test"}
    return _run


def _fixed_datetime(when: datetime):
    class _FixedDT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return when.replace(tzinfo=tz) if tz else when
    return _FixedDT
