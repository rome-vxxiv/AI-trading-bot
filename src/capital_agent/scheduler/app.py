"""Scheduler wiring. AsyncIOScheduler in-process. Health API alongside."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path

import yaml
from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..alerts import notify
from ..health_api import build_health_app, serve_health
from ..logging_config import configure as configure_logging
from ..logging_config import get_logger
from ..mcp_client import MCPClient, lifespan_mcp
from ..risk import ensure_killswitch_row, get_policy
from ..sessions import HolidayCalendar, SessionsConfig, load_holidays, load_sessions
from ..settings import get_settings
from ..state import init_db
from .jobs.analysis import run_analysis_job
from .jobs.daily_reset import run_daily_reset
from .jobs.drawdown import run_drawdown_check
from .jobs.keepalive import run_keepalive
from .jobs.reconcile import run_reconcile
from .jobs.session_reconcile import audit_sessions
from .jobs_config import JobDef, load_jobs
from .watchdog import on_job_missed

log = get_logger(__name__)


def _load_allowlist(path: Path) -> list[str]:
    if not path.exists():
        log.warning("allowlist.missing", path=str(path))
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(e).strip() for e in (raw.get("epics") or []) if str(e).strip()]


def build_scheduler(mcp: MCPClient, sessions: SessionsConfig,
                    holidays: HolidayCalendar, job_defs: list[JobDef]) -> AsyncIOScheduler:
    s = get_settings()
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_listener(on_job_missed, EVENT_JOB_MISSED)

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
    # Equity snapshot + drawdown check every 60 s. Auto-triggers the
    # kill switch on drawdown breach and alerts on daily-loss-cap breach.
    sched.add_job(
        run_drawdown_check, IntervalTrigger(seconds=60),
        args=[mcp], id="drawdown", replace_existing=True, max_instances=1,
        coalesce=True,
    )

    # Strategy jobs driven entirely by config/jobs.yaml -- adding an
    # instrument to an existing strategy/tier is a config-only change.
    # Session gating (open/closed, guards, holidays) is evaluated INSIDE
    # each tick, not here -- an enabled job still correctly no-ops most
    # ticks when its instrument's market is shut. Preflight risk checks
    # (kill switch, cooldowns, allowlist, max positions) + postflight
    # validation live inside run_playbook_once via runner.py.
    # CAP_DRY_RUN=true is enforced -- preview only, execute impossible.
    for jd in job_defs:
        sched.add_job(
            run_analysis_job,
            CronTrigger(minute=jd.cron_minutes, timezone="UTC"),
            args=[jd.epic, sessions, jd.strategy, holidays],
            id=jd.id, replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=120,
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
    await ensure_killswitch_row()
    policy = get_policy(settings.capital_agent_config_dir)
    sessions = load_sessions(settings.capital_agent_config_dir / "sessions.yaml")
    holidays = load_holidays(settings.capital_agent_config_dir / "holidays.yaml")
    job_defs = load_jobs(settings.capital_agent_config_dir / "jobs.yaml")
    allowlist = _load_allowlist(settings.capital_agent_config_dir / "allowlist.yaml")
    log.info("config.loaded",
             instruments=len(sessions.instruments),
             allowlist=allowlist,
             session_edge_minutes=sessions.session_edge_minutes,
             dry_run=policy.dry_run,
             risk_pct=policy.risk_pct_per_trade,
             max_positions_total=policy.max_positions_total,
             holiday_markets=list(holidays.markets.keys()),
             scheduled_jobs=[jd.id for jd in job_defs])
    await notify("scheduler.starting",
                 dry_run=policy.dry_run, allowlist=",".join(allowlist))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, stop_event)

    async with lifespan_mcp() as mcp:
        # One-shot startup audits
        tools = await mcp.list_tools()
        log.info("mcp.tools_ready", count=len(tools))
        await audit_sessions(mcp, sessions, allowlist)

        sched = build_scheduler(mcp, sessions, holidays, job_defs)
        sched.start()
        log.info("scheduler.started",
                 keepalive_s=settings.keepalive_interval_seconds,
                 reconcile_s=settings.reconciliation_interval_seconds,
                 jobs=[{"id": jd.id, "epic": jd.epic, "tier": jd.tier} for jd in job_defs])

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
    await notify("scheduler.stopped")
    return 0
