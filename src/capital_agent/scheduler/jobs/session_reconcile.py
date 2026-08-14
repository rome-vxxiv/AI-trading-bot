"""Startup-only: for each configured epic, fetch cap_market_get and
reconcile openingHours with the local sessions.yaml. Capital.com wins.

For step (2) we only LOG differences; we don't rewrite the yaml. In a
later step we'll add a --sync-sessions CLI to actually merge."""

from __future__ import annotations

from typing import Any

from ...logging_config import get_logger
from ...mcp_client import MCPClient
from ...sessions import SessionsConfig

log = get_logger(__name__)


async def audit_sessions(mcp: MCPClient, sessions: SessionsConfig,
                         allowlist: list[str]) -> None:
    for epic in allowlist:
        try:
            details = await mcp.call("cap_market_get", {"epic": epic}, timeout_s=10.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("session_audit.error", epic=epic, error=str(exc)[:200])
            continue
        instrument = (details or {}).get("instrument") if isinstance(details, dict) else None
        opening_hours = (instrument or {}).get("openingHours") if instrument else None
        status = (details or {}).get("snapshot", {}).get("marketStatus") if isinstance(details, dict) else None
        local = sessions.instruments.get(epic)
        log.info(
            "session_audit.market",
            epic=epic,
            broker_status=status,
            broker_hours_keys=sorted(opening_hours.keys()) if isinstance(opening_hours, dict) else None,
            local_configured=local is not None,
            local_continuous=(local.continuous if local else None),
        )


def _summ(x: Any) -> str:
    s = str(x)
    return s if len(s) < 120 else s[:117] + "..."
