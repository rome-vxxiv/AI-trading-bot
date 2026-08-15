"""Pre-flight and post-flight risk checks orchestrator.

Preflight runs BEFORE we spawn the LLM. It answers: "should this
epic/strategy be allowed to make a decision right now?" If any check
fails we skip the whole invocation (no LLM call = no cost).

Postflight runs AFTER the LLM returns a preview response. It validates
the preview against our policy — sensible size, stop within ATR bounds,
we didn't blow through max positions, etc. If postflight rejects, we
discard the preview_id (the MCP server also expires it in 120s).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from ..logging_config import get_logger
from ..state import PositionsLocal
from ..state.db import session_scope
from .cooldowns import cooldown_status
from .killswitch import is_active as ks_is_active
from .policy import RiskPolicy

log = get_logger(__name__)


@dataclass
class CheckResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)

    def add(self, ok: bool, reason: str) -> None:
        if not ok:
            self.ok = False
            self.reasons.append(reason)


async def preflight(epic: str, strategy_id: str,
                    policy: RiskPolicy) -> CheckResult:
    result = CheckResult(ok=True)

    active, ks_reason = await ks_is_active()
    result.add(not active, f"kill_switch_active:{ks_reason}" if active else "")

    epic_u = epic.upper()
    if policy.allowlist and epic_u not in policy.allowlist:
        result.add(False, f"epic_not_in_allowlist:{epic_u}")

    async with session_scope() as s:
        rows = (await s.execute(select(PositionsLocal))).scalars().all()
    n_total = len(rows)
    n_epic = sum(1 for r in rows if (r.epic or "").upper() == epic_u)
    if n_total >= policy.max_positions_total:
        result.add(False, f"max_positions_total:{n_total}>={policy.max_positions_total}")
    if n_epic >= policy.max_positions_per_instrument:
        result.add(False, f"max_per_instrument:{n_epic}>={policy.max_positions_per_instrument}")

    cd = await cooldown_status(epic_u, policy)
    if cd.in_cooldown:
        result.add(False, cd.reason)

    result.fields.update({
        "kill_switch_active": active,
        "positions_total": n_total,
        "positions_this_epic": n_epic,
        "in_cooldown": cd.in_cooldown,
        "cooldown_unlock_at": cd.unlock_at_utc.isoformat() if cd.unlock_at_utc else None,
    })

    if not result.ok:
        log.info("preflight.rejected", epic=epic_u, strategy=strategy_id,
                 reasons=result.reasons)
    else:
        log.info("preflight.ok", epic=epic_u, strategy=strategy_id,
                 fields=result.fields)
    return result


def postflight(verdict: dict[str, Any], policy: RiskPolicy,
               atr_used: float | None) -> CheckResult:
    """Called after the LLM returns a preview response. Validates it
    against our stricter-than-broker policy before we consider it
    'live'. In step 5 we don't execute — this just tags the preview
    ACCEPTED or REJECTED in the signal record."""
    result = CheckResult(ok=True)

    decision = verdict.get("decision") or "hold"
    if decision == "hold":
        # nothing to validate
        result.fields["decision"] = "hold"
        return result

    preview_id = verdict.get("preview_id")
    result.add(bool(preview_id), "no_preview_id_from_llm" if not preview_id else "")

    preview_all_checks_passed = verdict.get("preview_all_checks_passed")
    if preview_all_checks_passed is False:
        result.add(False, "broker_preview_checks_failed")

    if policy.stop_required:
        # Playbook is required to include stop_distance in preview call.
        # We validate the ATR ratio if the LLM reported the ATR it used.
        if atr_used is not None and atr_used > 0:
            claimed_stop = verdict.get("stop_distance_used")
            if isinstance(claimed_stop, int | float) and claimed_stop > 0:
                ratio = claimed_stop / atr_used
                if ratio > policy.stop_max_atr_multiples:
                    result.add(False, f"stop_distance_{ratio:.2f}x_atr_exceeds_{policy.stop_max_atr_multiples}")

    result.fields.update({
        "decision": decision,
        "preview_id": preview_id,
        "preview_all_checks_passed": preview_all_checks_passed,
    })
    if not result.ok:
        log.warning("postflight.rejected", reasons=result.reasons,
                    fields=result.fields)
    else:
        log.info("postflight.ok", fields=result.fields)
    return result
