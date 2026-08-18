"""Trade-outcome tagger. Called by reconcile when a local position
disappears from the broker (position closed). Fetches the recent
activity/transactions for that deal_id, computes realized P&L, and
updates the originating Signal's model_output_json with:
    outcome: "win" | "loss" | "flat"
    pnl_realized: <float>
    closed_at: <iso>

The `outcome` field is what cooldowns.py reads for the "after losing
trade" longer cooldown window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from ...alerts import notify
from ...logging_config import get_logger
from ...mcp_client import MCPClient
from ...state import Signal
from ...state.db import session_scope

log = get_logger(__name__)


async def tag_closed_position(mcp: MCPClient, deal_id: str, epic: str,
                              strategy_id: str | None) -> None:
    """Look up the transaction for `deal_id`, tag the last matching
    Signal with outcome + pnl. Non-raising on any error."""
    pnl = await _pnl_for_deal(mcp, deal_id)
    outcome = "flat"
    if pnl is not None:
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"

    async with session_scope() as s:
        q = select(Signal).where(Signal.epic == epic).order_by(desc(Signal.ts))
        if strategy_id:
            q = q.where(Signal.strategy_id == strategy_id)
        candidates = (await s.execute(q.limit(20))).scalars().all()
        # Find the signal whose model_output_json references this deal_id
        # (recorded when execute succeeded), else fall back to the most
        # recent entry_decision signal for this epic.
        target = None
        for row in candidates:
            payload = row.model_output_json if isinstance(row.model_output_json, dict) else {}
            executed = payload.get("_execute")
            if isinstance(executed, dict) and executed.get("deal_id") == deal_id:
                target = row
                break
        if target is None:
            for row in candidates:
                if row.decision in ("enter_long", "enter_short") and (
                    isinstance(row.model_output_json, dict)
                    and not (row.model_output_json.get("_execute") or {}).get("closed_at")
                ):
                    target = row
                    break
        if target is None:
            log.warning("outcome_tag.no_target", deal_id=deal_id, epic=epic)
            return

        payload = dict(target.model_output_json or {})
        exec_block = dict(payload.get("_execute") or {})
        exec_block["deal_id"] = deal_id
        exec_block["closed_at"] = datetime.now(UTC).isoformat()
        exec_block["pnl_realized"] = pnl
        payload["_execute"] = exec_block
        payload["outcome"] = outcome
        target.model_output_json = payload
        await s.commit()

    log.info("outcome_tagged", deal_id=deal_id, epic=epic, outcome=outcome, pnl=pnl)
    await notify("position.closed", epic=epic, deal_id=deal_id[:12],
                 outcome=outcome, pnl=f"{pnl:.2f}" if pnl is not None else "unknown")


async def _pnl_for_deal(mcp: MCPClient, deal_id: str) -> float | None:
    """Ask the broker for the transactions on this deal in the last 24h.
    Return the summed profit/loss if present."""
    now = datetime.now(UTC)
    frm = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    to = now.isoformat().replace("+00:00", "Z")
    try:
        txs = await mcp.call(
            "cap_account_history_transactions",
            {"from_date": frm, "to_date": to},
            timeout_s=10,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("pnl.tx_fetch_error", deal_id=deal_id, error=str(exc)[:200])
        return None
    if not isinstance(txs, dict):
        return None
    transactions = txs.get("transactions") or []
    pnl_total: float | None = None
    for t in transactions:
        if not isinstance(t, dict):
            continue
        ref = str(t.get("reference") or t.get("dealId") or "")
        if deal_id in ref or ref in deal_id:
            v = t.get("profitAndLoss") or t.get("size") or 0
            try:
                # profitAndLoss may be a currency-prefixed string like "E-1.23"
                pnl_val = _parse_signed(v)
                pnl_total = (pnl_total or 0.0) + pnl_val
            except (ValueError, TypeError):
                continue
    return pnl_total


def _parse_signed(v: Any) -> float:
    if isinstance(v, int | float):
        return float(v)
    s = str(v).strip()
    # Capital.com sometimes formats "E-1.23" or "$-1.23"
    for ch in ("E", "$", "£", "€"):
        if s.startswith(ch):
            s = s[len(ch):]
    return float(s)
