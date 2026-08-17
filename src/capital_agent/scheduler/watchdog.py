"""System sleep/freeze detector.

APScheduler fires EVENT_JOB_MISSED whenever a job's scheduled run time
has already passed by the time the scheduler gets around to checking it.
On a healthy always-on process this basically never happens. When a
Windows/Mac laptop suspends, every job scheduled during the suspended
window misses simultaneously — that's a strong, cheap signal that the
PC (not the bot) is the reason nothing ran, with no extra polling loop
needed.

We debounce because APScheduler emits ONE missed-event per job, and a
long sleep produces a burst of them (keepalive + reconcile + drawdown +
strategy, all at once) — without debouncing that's 4+ Telegram messages
for a single sleep episode.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apscheduler.events import JobExecutionEvent

from ..alerts import notify
from ..logging_config import get_logger

log = get_logger(__name__)

# A job running >3 min later than scheduled is not "the tick was slow" —
# our jobs finish in single-digit seconds — it's the process having been
# suspended (or the host under extreme load). Tune down if you add a
# legitimately slow job.
SLEEP_THRESHOLD_SECONDS = 180

# Only one Telegram alert per sleep episode, even though several jobs
# report EVENT_JOB_MISSED within the same second on wake.
DEBOUNCE_SECONDS = 300

_last_alert_at: datetime | None = None


def on_job_missed(event: JobExecutionEvent) -> None:
    """Registered via scheduler.add_listener(..., EVENT_JOB_MISSED).
    Synchronous callback (APScheduler's listener contract) — any async
    work is handed off via asyncio.create_task."""
    global _last_alert_at

    now = datetime.now(UTC).replace(tzinfo=None)
    scheduled = event.scheduled_run_time
    if scheduled.tzinfo is not None:
        scheduled = scheduled.astimezone(UTC).replace(tzinfo=None)
    gap_s = (now - scheduled).total_seconds()

    log.warning("watchdog.job_missed", job_id=event.job_id, missed_by_s=int(gap_s))

    if gap_s < SLEEP_THRESHOLD_SECONDS:
        return
    if _last_alert_at is not None and (now - _last_alert_at).total_seconds() < DEBOUNCE_SECONDS:
        return
    _last_alert_at = now

    minutes = gap_s / 60.0
    try:
        asyncio.create_task(notify(
            "system.possible_sleep_or_freeze",
            missed_job=event.job_id,
            gap_minutes=f"{minutes:.1f}",
            hint="PC likely slept or was overloaded; bot is catching up now",
        ))
    except RuntimeError:
        # No running loop reachable from this callback context — fall
        # back to the log line above, which still lands in agent.jsonl.
        log.error("watchdog.alert_dispatch_failed", missed_job=event.job_id)
