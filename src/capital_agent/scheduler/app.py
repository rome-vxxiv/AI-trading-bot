"""Scheduler wiring. AsyncIOScheduler in-process. Health API alongside."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..health_api import build_health_app, serve_health
from ..logging_config import configure as configure_logging
from ..logging_config import get_logger
from ..mcp_client import MCPClient, lifespan_mcp
from ..sessions import load_sessions
from ..settings import get_settings
from ..state import init_db
from .jobs.daily_reset import run_daily_reset
from .jobs.keepalive import run_keepalive
from .jobs.reconcile import run_reconcile
from .jobs.session_reconcile import audit_sessions

log = get_logger(__name__)


def _load_allowlist(path: Path) -> list[str]:
    if not path.exists():
        log.warning("allowlist.missing", path=str(path))
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(e).strip() for e in (raw.get("epics") or []) if str(e).strip()]


def build_scheduler(mcp: MCPClient) -> AsyncIOScheduler:
    s = get_settings()
    sched = AsyncIOScheduler(timezone="UTC")

    sched.add_job(
        run_keepalive, IntervalTrigger(seconds=s.keepalive_interval_seconds),
        args=[mcp], id="keepalive", replace_existing=True, max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        run_reconcile, IntervalTrigger(seconds=s.reconciliation_interval_seconds),
        args=[mcp], id="reconcile", replace_existing=True, max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        run_daily_reset, CronTrigger(hour=0, minute=0, timezone="UTC"),
        id="daily_reset", replace_existing=True, max_instances=1, coalesce=True,
    )
    return sched


async def _wait_forever(stop_event: asyncio.Event) -> None:
    await stop_event.wait()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop,
                             stop_event: asyncio.Event) -> None:
    def _cb() -> None:
        log.info("shutdown.signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _cb)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM/SIGINT
            # on the ProactorEventLoop for all signals. KeyboardInterrupt
            # still bubbles out of asyncio.run() below.
            pass


async def run() -> int:
    settings = get_settings()
    configure_logging(settings.capital_agent_log_dir)
    log.info("boot", state_dir=str(settings.capital_agent_state_dir),
             log_dir=str(settings.capital_agent_log_dir),
             config_dir=str(settings.capital_agent_config_dir))

    await init_db(settings.capital_agent_state_dir)
    sessions = load_sessions(settings.capital_agent_config_dir / "sessions.yaml")
    allowlist = _load_allowlist(settings.capital_agent_config_dir / "allowlist.yaml")
    log.info("config.loaded",
             instruments=len(sessions.instruments),
             allowlist=allowlist,
             session_edge_minutes=sessions.session_edge_minutes)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event)

    async with lifespan_mcp() as mcp:
        # One-shot startup audits
        tools = await mcp.list_tools()
        log.info("mcp.tools_ready", count=len(tools))
        await audit_sessions(mcp, sessions, allowlist)

        sched = build_scheduler(mcp)
        sched.start()
        log.info("scheduler.started",
                 keepalive_s=settings.keepalive_interval_seconds,
                 reconcile_s=settings.reconciliation_interval_seconds)

        # Health API
        app = build_health_app(mcp=mcp, sched=sched)
        health_task = asyncio.create_task(
            serve_health(app, settings.health_bind_host, settings.health_bind_port))

        try:
            await _wait_forever(stop_event)
        finally:
            log.info("shutdown.begin")
            sched.shutdown(wait=False)
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
    log.info("shutdown.done")
    return 0
