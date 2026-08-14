"""Long-lived MCP client wrapper.

Spawns the upstream capital-mcp subprocess ONCE for the scheduler's
lifetime and multiplexes all tool calls through it under a single
asyncio lock. Rationale:

- The upstream server keeps a session token alive internally; sharing one
  connection avoids re-logging in on every job.
- Only one agent-driver may run against the same account at a time
  (Capital.com 10 req/s + trading rate limits).
- If the subprocess dies, we relaunch on next use — no state to lose.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .logging_config import get_logger
from .settings import get_settings

log = get_logger(__name__)


class MCPClient:
    """Async singleton-ish wrapper around one stdio MCP subprocess."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: ClientSession | None = None
        self._cm = None  # holds the enter'd async context managers
        self._started = False

    async def start(self) -> None:
        """Spawn the subprocess and initialize the MCP session."""
        if self._started:
            return
        env = dict(os.environ)
        # Keep the server quiet on our stderr so our own logs stay readable.
        env.setdefault("CAP_LOG_LEVEL", "WARNING")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "capital_mcp"], env=env,
        )
        err_log = get_settings().capital_agent_log_dir / "mcp-server.log"
        self._cm = _SessionHolder(params, err_log_path=err_log)
        self._session = await self._cm.__aenter__()
        init = await self._session.initialize()
        self._started = True
        log.info("mcp.started",
                 server=init.serverInfo.name, version=init.serverInfo.version)

    async def stop(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                log.warning("mcp.stop_error", error=str(exc))
        self._session = None
        self._cm = None
        self._started = False

    async def call(self, tool: str, args: dict[str, Any] | None = None,
                   timeout_s: float = 30.0) -> Any:
        """Serialized tool call with a hard timeout. Returns the parsed JSON
        payload (or text). Raises asyncio.TimeoutError if the upstream tool
        takes longer than timeout_s — callers must handle that."""
        if not self._started or self._session is None:
            await self.start()
        assert self._session is not None
        async with self._lock:
            result = await asyncio.wait_for(
                self._session.call_tool(tool, args or {}),
                timeout=timeout_s,
            )
        return _first_text_payload(result.content)

    async def list_tools(self) -> list[str]:
        if not self._started or self._session is None:
            await self.start()
        assert self._session is not None
        async with self._lock:
            r = await self._session.list_tools()
        return sorted(t.name for t in r.tools)


class _SessionHolder:
    """Nested async context managers held so start()/stop() can be flat.

    The MCP subprocess's stderr is redirected to STATE_DIR/mcp-server.log so
    the user's console stays clean of FastMCP framework chatter and rich
    tracebacks. Errors from tool calls still propagate over MCP as normal
    responses that our code sees and logs structurally."""

    def __init__(self, params: StdioServerParameters, err_log_path: Path) -> None:
        self._params = params
        self._stdio_cm = None
        self._session_cm = None
        err_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._err_fp = err_log_path.open("a", buffering=1, encoding="utf-8", errors="replace")

    async def __aenter__(self) -> ClientSession:
        self._stdio_cm = stdio_client(self._params, errlog=self._err_fp)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        return session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._session_cm is not None:
                await self._session_cm.__aexit__(exc_type, exc, tb)
            if self._stdio_cm is not None:
                await self._stdio_cm.__aexit__(exc_type, exc, tb)
        finally:
            try:
                self._err_fp.close()
            except Exception:  # noqa: BLE001
                pass


def _first_text_payload(content_blocks: list) -> Any:
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return None


@asynccontextmanager
async def lifespan_mcp():
    """`async with lifespan_mcp() as client:` for the scheduler main loop."""
    client = MCPClient()
    await client.start()
    try:
        yield client
    finally:
        await client.stop()
