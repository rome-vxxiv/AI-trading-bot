"""Is-open / is-in-guard / next-open logic for instrument sessions.

All times UTC. Weekday convention: Monday=0 (Python datetime).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .parser import Guard, InstrumentSession


def _minute_of_week(dt: datetime) -> int:
    return dt.weekday() * 24 * 60 + dt.hour * 60 + dt.minute


def is_open(sess: InstrumentSession, now_utc: datetime) -> bool:
    """True if the instrument's session window contains now_utc.

    Continuous instruments (open==close) are always open. Otherwise the
    window wraps from open to close within a week.
    """
    if sess.continuous:
        return True
    now_m = _minute_of_week(now_utc)
    open_m = sess.open.as_minutes()
    close_m = sess.close.as_minutes()
    if open_m <= close_m:
        return open_m <= now_m < close_m
    # wraps across Sunday midnight (rare) — outside close..open is open
    return not (close_m <= now_m < open_m)


def is_in_guard(guards: list[Guard], now_utc: datetime) -> bool:
    tod = now_utc.time().replace(second=0, microsecond=0)
    for g in guards:
        if g.start <= g.end:
            if g.start <= tod < g.end:
                return True
        else:
            # crosses midnight
            if tod >= g.start or tod < g.end:
                return True
    return False


def within_edge_guard(sess: InstrumentSession, now_utc: datetime, edge_minutes: int) -> bool:
    """True if within edge_minutes of the session open or close."""
    if sess.continuous or edge_minutes <= 0:
        return False
    now_m = _minute_of_week(now_utc)
    open_m = sess.open.as_minutes()
    close_m = sess.close.as_minutes()
    for anchor in (open_m, close_m):
        if abs(now_m - anchor) < edge_minutes:
            return True
    return False


def next_open(sess: InstrumentSession, now_utc: datetime, horizon_days: int = 8) -> datetime:
    """Next minute at which is_open() would be True. If already open, returns now."""
    if is_open(sess, now_utc):
        return now_utc
    probe = now_utc.replace(second=0, microsecond=0)
    for _ in range(horizon_days * 24 * 60):
        probe += timedelta(minutes=1)
        if is_open(sess, probe):
            return probe
    raise RuntimeError(f"no open window for {sess.epic} within {horizon_days}d")
