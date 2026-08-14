"""Position reconciliation. Pull open positions from broker, compare to
our local shadow. Broker is source of truth.

Behavior in step (2):
  - Broker has positions we don't → adopt them into local (strategy_id=null).
  - We have positions broker doesn't → delete from local + warn.
  - Fields differ (size/stop/tp) → update local + info-log the delta.

No trading side effects. This job is safe to run before any strategy exists."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ...logging_config import get_logger
from ...mcp_client import MCPClient
from ...state import PositionsLocal, session_scope

log = get_logger(__name__)


def _parse_position(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Capital.com wraps positions under keys `position` + `market`."""
    pos = raw.get("position") or {}
    market = raw.get("market") or {}
    deal_id = pos.get("dealId")
    if not deal_id:
        return None
    return {
        "deal_id": str(deal_id),
        "epic": str(market.get("epic") or ""),
        "direction": str(pos.get("direction") or ""),
        "size": float(pos.get("size") or 0.0),
        "entry": float(pos.get("level") or pos.get("openLevel") or 0.0),
        "stop": _f(pos.get("stopLevel")),
        "tp":   _f(pos.get("profitLevel")),
    }


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def run_reconcile(mcp: MCPClient) -> None:
    payload = await mcp.call("cap_trade_positions_list")
    if not isinstance(payload, dict):
        log.warning("reconcile.bad_payload", payload=str(payload)[:200])
        return
    remote_raw = payload.get("positions") or []
    remote = {p["deal_id"]: p for p in (_parse_position(r) for r in remote_raw) if p}

    async with session_scope() as s:
        rows = (await s.execute(select(PositionsLocal))).scalars().all()
        local = {r.deal_id: r for r in rows}

        # broker-only → adopt
        for deal_id, rp in remote.items():
            if deal_id not in local:
                s.add(PositionsLocal(
                    deal_id=deal_id, epic=rp["epic"], direction=rp["direction"],
                    size=rp["size"], entry=rp["entry"], stop=rp["stop"], tp=rp["tp"],
                    opened_at=datetime.now(UTC).replace(tzinfo=None), strategy_id=None,
                ))
                log.warning("reconcile.adopt_broker_only",
                            deal_id=deal_id, epic=rp["epic"],
                            direction=rp["direction"], size=rp["size"])
            else:
                r = local[deal_id]
                for k in ("size", "entry", "stop", "tp"):
                    if getattr(r, k) != rp[k]:
                        log.info("reconcile.field_updated",
                                 deal_id=deal_id, field=k,
                                 old=getattr(r, k), new=rp[k])
                        setattr(r, k, rp[k])

        # local-only → prune
        for deal_id, lr in list(local.items()):
            if deal_id not in remote:
                log.warning("reconcile.prune_local_only",
                            deal_id=deal_id, epic=lr.epic)
                await s.delete(lr)

        await s.commit()

    log.info("reconcile.ok", broker_positions=len(remote),
             local_positions=len(local))
