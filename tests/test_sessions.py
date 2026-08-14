"""Session-window logic. All times UTC. Monday=0."""

from datetime import datetime, time

from capital_agent.sessions.parser import (
    Guard,
    InstrumentSession,
    WeekdayTime,
    load_sessions,
)
from capital_agent.sessions.window import is_in_guard, is_open, next_open, within_edge_guard


def _mk_24_7() -> InstrumentSession:
    w = WeekdayTime(6, 0, 0)  # SUN 00:00
    return InstrumentSession(epic="BTCUSD", open=w, close=w, guards=[], continuous=True)


def _mk_fx() -> InstrumentSession:
    return InstrumentSession(
        epic="EURUSD",
        open=WeekdayTime(6, 22, 0),   # SUN 22:00
        close=WeekdayTime(4, 22, 0),  # FRI 22:00
        guards=[Guard(time(21, 55), time(22, 5))],
        continuous=False,
    )


def test_crypto_is_open_every_time():
    s = _mk_24_7()
    for wd in range(7):
        for hh in (0, 6, 13, 22):
            assert is_open(s, datetime(2026, 8, 10 + (wd - datetime(2026, 8, 10).weekday()) % 7, hh, 0))


def test_fx_closed_on_saturday():
    s = _mk_fx()
    # Sat 12:00 UTC — closed
    saturday = datetime(2026, 8, 15, 12, 0)
    assert saturday.weekday() == 5
    assert not is_open(s, saturday)


def test_fx_open_wed_noon():
    s = _mk_fx()
    wed = datetime(2026, 8, 12, 12, 0)
    assert wed.weekday() == 2
    assert is_open(s, wed)


def test_fx_closed_sunday_before_open():
    s = _mk_fx()
    sun_early = datetime(2026, 8, 16, 21, 0)
    assert sun_early.weekday() == 6
    assert not is_open(s, sun_early)


def test_fx_open_sunday_after_open():
    s = _mk_fx()
    sun_late = datetime(2026, 8, 16, 22, 30)
    assert sun_late.weekday() == 6
    assert is_open(s, sun_late)


def test_guard_daily_window():
    guards = [Guard(time(21, 55), time(22, 5))]
    assert is_in_guard(guards, datetime(2026, 8, 12, 21, 58))
    assert is_in_guard(guards, datetime(2026, 8, 12, 22, 4))
    assert not is_in_guard(guards, datetime(2026, 8, 12, 22, 6))


def test_edge_guard_around_session_open():
    s = _mk_fx()
    # 2 min before open (SUN 22:00) — within 5 min edge
    assert within_edge_guard(s, datetime(2026, 8, 16, 21, 58), edge_minutes=5)
    # 6 min before open — outside
    assert not within_edge_guard(s, datetime(2026, 8, 16, 21, 54), edge_minutes=5)


def test_next_open_for_closed_market():
    s = _mk_fx()
    # Sat noon → next open is Sun 22:00
    n = next_open(s, datetime(2026, 8, 15, 12, 0))
    assert n.weekday() == 6 and n.hour == 22 and n.minute == 0


def test_load_sessions_yaml(tmp_path):
    p = tmp_path / "sessions.yaml"
    p.write_text(
        "instruments:\n"
        "  BTCUSD: { open: 'SUN 00:00', close: 'SUN 00:00', guards: [] }\n"
        "  GOLD:   { open: 'SUN 22:00', close: 'FRI 21:00', guards: ['DAILY 21:00-22:00'] }\n"
        "global_guards:\n"
        "  session_edge_minutes: 3\n",
        encoding="utf-8",
    )
    cfg = load_sessions(p)
    assert cfg.session_edge_minutes == 3
    assert cfg.instruments["BTCUSD"].continuous is True
    assert cfg.instruments["GOLD"].continuous is False
    assert len(cfg.instruments["GOLD"].guards) == 1
