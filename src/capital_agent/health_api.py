"""Tiny FastAPI health/status server.

/healthz  → 200 OK if scheduler + MCP look alive.
/status   → snapshot: MCP session status, next jobs, local positions count.
/jobs     → list of scheduled jobs and their next fire time.
/kill     → activate the kill switch (auth via HEALTH_API_TOKEN).
/unlock   → clear the kill switch (auth via HEALTH_API_TOKEN).

Kill/unlock write to the DB kill_switch row; the scheduler consults it
before letting any trading-adjacent job proceed (step 3+)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select

from .logging_config import get_logger
from .mcp_client import MCPClient
from .settings import get_settings
from .state import KillSwitchRow, PositionsLocal
from .state.db import session_scope

log = get_logger(__name__)


def _require_token(x_auth_token: str | None = Header(default=None)) -> None:
    expected = get_settings().health_api_token
    if not expected:
        raise HTTPException(503, "health API token not configured")
    if x_auth_token != expected:
        raise HTTPException(401, "bad token")


def build_health_app(mcp: MCPClient, sched) -> FastAPI:
    app = FastAPI(title="capital-agent health API", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "ts": datetime.now(UTC).isoformat()}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        try:
            sess_status = await asyncio.wait_for(mcp.call("cap_session_status"), timeout=5)
        except Exception as exc:  # noqa: BLE001
            sess_status = {"_error": str(exc)}
        async with session_scope() as s:
            pos_count = (await s.execute(select(PositionsLocal))).scalars().all()
            ks = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
        return {
            "mcp_session": sess_status,
            "local_position_count": len(pos_count),
            "kill_switch": {
                "active": ks.active if ks else True,
                "reason": ks.reason if ks else "no row",
            },
            "jobs": [{"id": j.id, "next_run_time": str(j.next_run_time)}
                     for j in sched.get_jobs()],
        }

    @app.get("/jobs")
    async def jobs() -> list[dict[str, Any]]:
        return [{"id": j.id, "trigger": str(j.trigger),
                 "next_run_time": str(j.next_run_time)}
                for j in sched.get_jobs()]

    @app.post("/kill", dependencies=[Depends(_require_token)])
    async def kill(reason: str = "manual") -> dict[str, Any]:
        async with session_scope() as s:
            ks = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
            if ks is None:
                raise HTTPException(500, "kill switch row missing")
            ks.active = True
            ks.reason = reason
            ks.triggered_at = datetime.now(UTC).replace(tzinfo=None)
            ks.cleared_at = None
            await s.commit()
        log.warning("kill_switch.activated", reason=reason)
        return {"ok": True, "active": True, "reason": reason}

    @app.post("/unlock", dependencies=[Depends(_require_token)])
    async def unlock() -> dict[str, Any]:
        async with session_scope() as s:
            ks = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
            if ks is None:
                raise HTTPException(500, "kill switch row missing")
            ks.active = False
            ks.cleared_at = datetime.now(UTC).replace(tzinfo=None)
            await s.commit()
        log.warning("kill_switch.cleared")
        return {"ok": True, "active": False}

    return app


async def serve_health(app: FastAPI, host: str, port: int) -> None:
    """Serve the health API. If the port is busy, log and give up gracefully
    without killing the scheduler — a stale process is not a reason to lose
    keepalive + reconcile."""
    import socket

    import uvicorn

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        log.warning("health_api.port_in_use",
                    host=host, port=port, error=str(exc),
                    hint="another scheduler may still be running; check for stray python.exe")
        return
    finally:
        probe.close()

    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False, lifespan="off")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except Exception as exc:  # noqa: BLE001
        log.warning("health_api.server_error", error=str(exc)[:200])
