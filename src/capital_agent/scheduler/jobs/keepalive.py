"""Session keepalive job. Calls cap_session_ping. If the token has expired,
re-logs in. Exits fast; never blocks other jobs (the MCP client lock is
short-held)."""

from __future__ import annotations

from ...logging_config import get_logger
from ...mcp_client import MCPClient

log = get_logger(__name__)


async def run_keepalive(mcp: MCPClient) -> None:
    try:
        ping = await mcp.call("cap_session_ping")
    except Exception as exc:  # noqa: BLE001
        log.warning("keepalive.ping_error", error=str(exc))
        ping = None

    status = await mcp.call("cap_session_status")
    logged_in = isinstance(status, dict) and bool(status.get("logged_in"))
    if not logged_in:
        log.info("keepalive.relogin")
        try:
            await mcp.call("cap_session_login")
        except Exception as exc:  # noqa: BLE001
            log.error("keepalive.relogin_failed", error=str(exc))
            return
        status = await mcp.call("cap_session_status")

    log.info(
        "keepalive.ok",
        logged_in=isinstance(status, dict) and bool(status.get("logged_in")),
        expires_in_s=status.get("expires_in_s_estimate") if isinstance(status, dict) else None,
        pinged=ping is not None,
    )
