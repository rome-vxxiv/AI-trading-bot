"""Parse config/sessions.yaml into typed structures.

Session strings look like:
    "SUN 22:00"        -> weekday SUN (=6 in Python's Monday-0 convention), 22:00 UTC
    "DAILY 21:55-22:05"-> every day, guard interval 21:55..22:05
    "13:30-15:30 MON-FRI" -> intraday window Mon-Fri
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import yaml

_WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
_ANCHOR_RE = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN)\s+(\d{2}):(\d{2})$")
_GUARD_DAILY_RE = re.compile(r"^DAILY\s+(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_ACTIVE_WIN_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})\s+([A-Z]{3})-([A-Z]{3})$")


@dataclass(frozen=True)
class WeekdayTime:
    weekday: int      # Monday=0
    hh: int
    mm: int

    def as_minutes(self) -> int:
        return self.weekday * 24 * 60 + self.hh * 60 + self.mm


@dataclass(frozen=True)
class Guard:
    """Time-of-day interval that applies every day (DAILY hh:mm-hh:mm)."""
    start: time
    end: time


@dataclass
class InstrumentSession:
    epic: str
    open: WeekdayTime            # for 24/7, open == close
    close: WeekdayTime
    guards: list[Guard] = field(default_factory=list)
    cash_session: str | None = None
    continuous: bool = False
    holiday_market: str | None = None   # e.g. "US" -> checked against holidays.yaml


@dataclass
class SessionsConfig:
    instruments: dict[str, InstrumentSession] = field(default_factory=dict)
    session_edge_minutes: int = 5


def _parse_anchor(s: str) -> WeekdayTime:
    m = _ANCHOR_RE.match(s.strip().upper())
    if not m:
        raise ValueError(f"bad session anchor: {s!r}")
    return WeekdayTime(_WEEKDAYS[m.group(1)], int(m.group(2)), int(m.group(3)))


def _parse_guard(s: str) -> Guard:
    m = _GUARD_DAILY_RE.match(s.strip().upper())
    if not m:
        raise ValueError(f"bad guard: {s!r} (only DAILY hh:mm-hh:mm supported)")
    return Guard(time(int(m.group(1)), int(m.group(2))),
                 time(int(m.group(3)), int(m.group(4))))


def load_sessions(path: Path) -> SessionsConfig:
    if not path.exists():
        raise FileNotFoundError(f"sessions file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = SessionsConfig()
    global_guards = (raw or {}).get("global_guards") or {}
    out.session_edge_minutes = int(global_guards.get("session_edge_minutes", 5))
    for epic, cfg in ((raw or {}).get("instruments") or {}).items():
        open_w = _parse_anchor(cfg["open"])
        close_w = _parse_anchor(cfg["close"])
        guards = [_parse_guard(g) for g in (cfg.get("guards") or [])]
        out.instruments[epic] = InstrumentSession(
            epic=epic,
            open=open_w,
            close=close_w,
            guards=guards,
            cash_session=cfg.get("cash_session"),
            continuous=(open_w == close_w),
            holiday_market=cfg.get("holiday_market"),
        )
    return out
