"""Per-instrument cooldowns. Sourced from the `signals` table — we
consider any signal with decision in {"enter_long","enter_short"} as a
trade attempt and enforce a cooldown after it. A losing outcome gets a
longer cooldown (recorded in the model_output_json.outcome field once
step 6 wires trade P&L into it).

For step 5 (dry-run, no execute), we only enforce the "after any trade"
cooldown; the "losing trade" branch is coded but won't trigger because
no signals have outcomes yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..logging_config import get_logger
from ..state import Signal
from ..state.db import session_scope
from .policy import RiskPolicy

log = get_logger(__name__)


@dataclass(frozen=True)
class CooldownStatus:
    in_cooldown: bool
    reason: str
    unlock_at_utc: datetime | None


async def cooldown_status(epic: str, policy: RiskPolicy,
                          now_utc: datetime | None = None) -> CooldownStatus:
    now = now_utc or datetime.now(UTC).replace(tzinfo=None)
    any_min = policy.cooldown_after_any_trade_minutes
    lose_min = policy.cooldown_after_losing_trade_minutes

    horizon = now - timedelta(minutes=max(any_min, lose_min))
    async with session_scope() as s:
        rows = (await s.execute(
            select(Signal)
            .where(Signal.epic == epic)
            .where(Signal.ts >= horizon)
            .order_by(Signal.ts.desc())
        )).scalars().all()

    for r in rows:
        if r.decision not in ("enter_long", "enter_short"):
            continue
        outcome = (r.model_output_json or {}).get("outcome") if isinstance(r.model_output_json, dict) else None
        if outcome == "loss":
            unlock = r.ts + timedelta(minutes=lose_min)
            if now < unlock:
                return CooldownStatus(True, f"loss_cooldown_until_{unlock.isoformat()}",
                                      unlock)
        unlock = r.ts + timedelta(minutes=any_min)
        if now < unlock:
            return CooldownStatus(True, f"any_trade_cooldown_until_{unlock.isoformat()}",
                                  unlock)
    return CooldownStatus(False, "", None)


async def mark_trade(epic: str, decision: str,
                     strategy_id: str = "manual") -> None:
    """Used by tests. In production the strategy runner writes a Signal
    row directly which cooldown_status will read."""
    async with session_scope() as s:
        s.add(Signal(epic=epic, decision=decision, strategy_id=strategy_id,
                     reason="test", model_output_json={}))
        await s.commit()
