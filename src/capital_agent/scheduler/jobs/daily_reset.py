"""Daily reset job at 00:00 UTC. Rolls the daily-stats row and logs
yesterday's summary. Trading counters live in the MCP server (not us) —
those reset there separately."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ...logging_config import get_logger
from ...state import DailyStats
from ...state.db import session_scope

log = get_logger(__name__)


async def run_daily_reset() -> None:
    now = datetime.now(UTC)
    y = (now - timedelta(days=1)).date().isoformat()
    today = now.date().isoformat()
    async with session_scope() as s:
        prev = await s.scalar(select(DailyStats).where(DailyStats.utc_date == y))
        log.info("daily_reset.yesterday_summary",
                 date=y,
                 pnl_realized=(prev.pnl_realized if prev else None),
                 trades_count=(prev.trades_count if prev else None))
        curr = await s.scalar(select(DailyStats).where(DailyStats.utc_date == today))
        if curr is None:
            s.add(DailyStats(utc_date=today))
            await s.commit()
