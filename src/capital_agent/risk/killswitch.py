"""Kill-switch state accessor. The switch row lives in state.db and is
mutated by the health API (POST /kill, POST /unlock) or by the drawdown
monitor. Fail-safe: if the row is missing OR the DB is unreachable, we
treat the switch as ACTIVE."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from ..logging_config import get_logger
from ..state import KillSwitchRow
from ..state.db import session_scope

log = get_logger(__name__)


async def ensure_killswitch_row() -> None:
    async with session_scope() as s:
        row = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
        if row is None:
            s.add(KillSwitchRow(row_id=1, active=False, reason=""))
            await s.commit()


async def is_active() -> tuple[bool, str]:
    """Return (active, reason). Fails-safe to (True, 'db_error')."""
    try:
        async with session_scope() as s:
            row = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
    except Exception as exc:  # noqa: BLE001
        log.error("killswitch.db_error", error=str(exc)[:200])
        return True, "db_error"
    if row is None:
        return True, "row_missing"
    return bool(row.active), (row.reason or "")


async def set_active(active: bool, reason: str) -> None:
    async with session_scope() as s:
        row = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
        if row is None:
            row = KillSwitchRow(row_id=1)
            s.add(row)
        row.active = active
        row.reason = reason if active else ""
        now = datetime.now(UTC).replace(tzinfo=None)
        if active:
            row.triggered_at = now
            row.cleared_at = None
        else:
            row.cleared_at = now
        await s.commit()
    log.warning("killswitch.set", active=active, reason=reason)
