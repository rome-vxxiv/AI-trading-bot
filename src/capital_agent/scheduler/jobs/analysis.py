"""Scheduler job: run the read-only analysis driver against one epic,
respecting the instrument's session window and any DAILY guards.

Skipped ticks are logged (analysis.skipped) so operators can see the
session logic working without opening the DB. This is important for
GOLD in particular — it's closed all weekend, so the first ~40 hours
of a Saturday-morning start will be nothing but skips."""

from __future__ import annotations

from datetime import UTC, datetime

from ...driver import run_analysis_once
from ...logging_config import get_logger
from ...sessions import SessionsConfig, is_in_guard, is_open, next_open
from ...sessions.window import within_edge_guard
from ...state import KillSwitchRow
from ...state.db import session_scope

log = get_logger(__name__)


async def run_analysis_job(epic: str, sessions: SessionsConfig, strategy_id: str) -> None:
    now = datetime.now(UTC)

    async with session_scope() as s:
        from sqlalchemy import select
        ks = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
        if ks is not None and ks.active:
            log.info("analysis.skipped", epic=epic, reason="kill_switch_active",
                     kill_reason=ks.reason)
            return

    sess = sessions.instruments.get(epic)
    if sess is None:
        log.warning("analysis.skipped", epic=epic, reason="epic_not_in_sessions_yaml")
        return

    if not is_open(sess, now):
        try:
            nxt = next_open(sess, now).isoformat()
        except Exception:  # noqa: BLE001
            nxt = "unknown"
        log.info("analysis.skipped", epic=epic, reason="session_closed", next_open_at=nxt)
        return

    if is_in_guard(sess.guards, now):
        log.info("analysis.skipped", epic=epic, reason="in_daily_guard")
        return

    if within_edge_guard(sess, now, edge_minutes=sessions.session_edge_minutes):
        log.info("analysis.skipped", epic=epic, reason="within_session_edge")
        return

    log.info("analysis.start", epic=epic, strategy_id=strategy_id)
    verdict = await run_analysis_once(epic=epic, strategy_id=strategy_id)
    if "_error" in verdict:
        log.warning("analysis.error", epic=epic, error=verdict.get("_error"))
