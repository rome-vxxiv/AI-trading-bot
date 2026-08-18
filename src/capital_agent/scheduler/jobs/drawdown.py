"""Drawdown monitor. Every 60 s:
  1. Snapshot equity via cap_account_list.
  2. Update HWM (running max) in equity_snapshots.
  3. Update daily_start_equity (first snapshot each UTC day).
  4. If equity < HWM * (1 - max_drawdown_pct) → kill switch on + alert.
  5. (Step 6 leaves the daily-loss check as a log-only warning; the
     losing-trade cooldown from step 5 already reacts per-trade.)

Snapshotting is cheap and doesn't touch trading endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select

from ...alerts import notify
from ...logging_config import get_logger
from ...mcp_client import MCPClient
from ...risk import get_policy, is_active, set_active
from ...settings import get_settings
from ...state import EquitySnapshot
from ...state.db import session_scope

log = get_logger(__name__)


async def run_drawdown_check(mcp: MCPClient) -> None:
    settings = get_settings()
    policy = get_policy(settings.capital_agent_config_dir)

    equity, currency = await _fetch_equity(mcp)
    if equity is None:
        log.warning("drawdown.no_equity")
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    today_str = now.date().isoformat()

    async with session_scope() as s:
        prev = (await s.execute(
            select(EquitySnapshot).order_by(desc(EquitySnapshot.ts)).limit(1)
        )).scalar_one_or_none()

        hwm = max(equity, prev.hwm) if prev else equity
        if prev and prev.ts.date().isoformat() == today_str:
            daily_start = prev.daily_start_equity
        else:
            daily_start = equity

        s.add(EquitySnapshot(ts=now, equity=equity, hwm=hwm,
                             daily_start_equity=daily_start,
                             currency=currency or "EUR"))
        await s.commit()

    drawdown_pct = (hwm - equity) / hwm if hwm > 0 else 0.0
    day_pnl_pct = (equity - daily_start) / daily_start if daily_start > 0 else 0.0

    log.info("drawdown.snapshot", equity=equity, hwm=hwm,
             daily_start=daily_start, drawdown_pct=round(drawdown_pct, 4),
             day_pnl_pct=round(day_pnl_pct, 4))

    if drawdown_pct >= policy.max_drawdown_pct:
        active, _ = await is_active()
        if not active:
            reason = f"drawdown_{drawdown_pct:.2%}_>=_{policy.max_drawdown_pct:.2%}"
            await set_active(True, reason)
            await notify("kill_switch.drawdown_triggered",
                         drawdown_pct=f"{drawdown_pct:.2%}",
                         hwm=hwm, equity=equity, reason=reason)

    if day_pnl_pct <= -policy.max_daily_loss_pct:
        # Not a hard kill (a bad day recovers); log a warning + alert.
        log.warning("drawdown.daily_cap_hit",
                    day_pnl_pct=round(day_pnl_pct, 4),
                    cap=policy.max_daily_loss_pct)
        await notify("risk.daily_loss_cap",
                     day_pnl_pct=f"{day_pnl_pct:.2%}",
                     cap=f"{policy.max_daily_loss_pct:.2%}")


async def _fetch_equity(mcp: MCPClient) -> tuple[float | None, str | None]:
    try:
        accts = await mcp.call("cap_account_list", timeout_s=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("drawdown.fetch_error", error=str(exc)[:200])
        return None, None
    if not isinstance(accts, dict):
        return None, None
    active_id = accts.get("active_account_id")
    for a in (accts.get("accounts") or []):
        if not isinstance(a, dict):
            continue
        if a.get("accountId") == active_id or a.get("preferred"):
            bal = (a.get("balance") or {}).get("balance")
            cur = a.get("currency")
            if isinstance(bal, int | float):
                return float(bal), cur
    for a in (accts.get("accounts") or []):
        if isinstance(a, dict):
            bal = (a.get("balance") or {}).get("balance")
            if isinstance(bal, int | float):
                return float(bal), a.get("currency")
    return None, None
