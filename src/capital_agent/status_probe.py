"""One-shot status probe — spawns MCP, prints session status + tool count,
exits. Useful sanity check without starting the scheduler."""

from __future__ import annotations

import json

from .logging_config import configure as configure_logging
from .mcp_client import lifespan_mcp
from .settings import get_settings


async def status_once() -> int:
    configure_logging(get_settings().capital_agent_log_dir)
    async with lifespan_mcp() as mcp:
        tools = await mcp.list_tools()
        status = await mcp.call("cap_session_status")
        print(json.dumps({
            "tool_count": len(tools),
            "session_status": status,
        }, indent=2, default=str))
    return 0
