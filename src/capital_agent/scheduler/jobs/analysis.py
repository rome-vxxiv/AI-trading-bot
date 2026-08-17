"""Scheduler job wrapper for playbook invocations. Session-gates first,
then delegates to the parameterized runner in driver/runner.py.
Preflight risk checks live inside the runner — kill switch, cooldowns,
allowlist, and max-positions gate the LLM call itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...driver import run_playbook_once
from ...logging_config import get_logger
from ...sessions import HolidayCalendar, SessionsConfig, is_in_guard, is_open, next_open
from ...sessions.window import within_edge_guard

log = get_logger(__name__)


async def run_analysis_job(epic: str, sessions: SessionsConfig, strategy_id: str,
                           holidays: HolidayCalendar | None = None) -> None:
    """Backwards-compat wrapper. `strategy_id` is used to pick the playbook."""
    await _run_gated(epic=epic, sessions=sessions, strategy=strategy_id, holidays=holidays)


async def run_strategy_job(epic: str, sessions: SessionsConfig, strategy_id: str,
                           holidays: HolidayCalendar | None = None) -> None:
    await _run_gated(epic=epic, sessions=sessions, strategy=strategy_id, holidays=holidays)


async def _run_gated(*, epic: str, sessions: SessionsConfig, strategy: str,
                     holidays: HolidayCalendar | None = None) -> None:
    now = datetime.now(UTC)

    sess = sessions.instruments.get(epic)
    if sess is None:
        log.warning("job.skipped", epic=epic, strategy=strategy,
                    reason="epic_not_in_sessions_yaml")
        return

    if not is_open(sess, now):
        try:
            nxt = next_open(sess, now).isoformat()
        except Exception:  # noqa: BLE001
            nxt = "unknown"
        log.info("job.skipped", epic=epic, strategy=strategy,
                 reason="session_closed", next_open_at=nxt)
        return

    if holidays is not None and holidays.is_holiday(sess.holiday_market, now.date()):
        log.info("job.skipped", epic=epic, strategy=strategy,
                 reason="exchange_holiday", market=sess.holiday_market)
        return

    if is_in_guard(sess.guards, now):
        log.info("job.skipped", epic=epic, strategy=strategy,
                 reason="in_daily_guard")
        return

    if within_edge_guard(sess, now, edge_minutes=sessions.session_edge_minutes):
        log.info("job.skipped", epic=epic, strategy=strategy,
                 reason="within_session_edge")
        return

    log.info("job.start", epic=epic, strategy=strategy)
    verdict = await run_playbook_once(strategy=strategy, epic=epic)
    if "_error" in verdict:
        log.warning("job.error", epic=epic, strategy=strategy,
                    error=verdict.get("_error"),
                    reasons=verdict.get("reasons") or verdict.get("_message"))
