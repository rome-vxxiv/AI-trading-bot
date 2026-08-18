"""Exchange holiday calendars, loaded from config/holidays.yaml.

Crypto trades 24/7 and FX/commodities/CFD-indices follow Capital.com's
own continuous-week schedule — neither observes exchange holidays.
Individual equities do: NYSE closes for Thanksgiving, Christmas, etc.
even though Capital.com might otherwise show the CFD as "tradeable."

Each instrument in sessions.yaml can set `holiday_market: US` (or UK,
DE, ...) to opt into this check. Instruments without that field are
never affected — this is additive, not a behavior change for anything
that worked before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass
class HolidayCalendar:
    markets: dict[str, set[date]] = field(default_factory=dict)

    def is_holiday(self, market: str | None, day: date) -> bool:
        if not market:
            return False
        return day in self.markets.get(market, set())


def load_holidays(path: Path) -> HolidayCalendar:
    if not path.exists():
        log.warning("holidays_yaml.missing", path=str(path))
        return HolidayCalendar()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cal = HolidayCalendar()
    for market, entries in raw.items():
        if market == "year" or not isinstance(entries, list):
            continue
        parsed: set[date] = set()
        for entry in entries:
            # PyYAML auto-parses unquoted ISO dates (2026-01-01) into
            # `date` objects already; handle both that and plain strings
            # defensively in case someone quotes an entry.
            if isinstance(entry, date):
                parsed.add(entry)
                continue
            try:
                parsed.add(date.fromisoformat(str(entry).split("#")[0].strip()))
            except ValueError:
                log.warning("holidays_yaml.bad_date", market=market, raw=str(entry))
        cal.markets[market] = parsed
    return cal
