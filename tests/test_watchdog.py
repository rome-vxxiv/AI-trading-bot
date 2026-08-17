"""Sleep/freeze watchdog: threshold and debounce behavior.

on_job_missed() is a synchronous APScheduler listener callback that
hands off alerting via asyncio.create_task() — exactly how APScheduler
calls it in production (from inside the running event loop). Tests
must therefore run inside an event loop too (async def, pytest-asyncio
auto mode) and yield once (`await asyncio.sleep(0)`) so the scheduled
task actually executes before we assert on it.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import capital_agent.scheduler.watchdog as wd


class _FakeEvent:
    def __init__(self, job_id: str, scheduled_run_time: datetime) -> None:
        self.job_id = job_id
        self.scheduled_run_time = scheduled_run_time


@pytest.fixture(autouse=True)
def _reset_debounce(monkeypatch):
    monkeypatch.setattr(wd, "_last_alert_at", None)
    yield


@pytest.fixture(autouse=True)
def _capture_notify(monkeypatch):
    calls = []

    async def _fake_notify(event, **fields):
        calls.append((event, fields))

    monkeypatch.setattr(wd, "notify", _fake_notify)
    return calls


async def test_small_delay_does_not_alert(_capture_notify):
    # scheduled 30s ago -> well under the 180s threshold
    scheduled = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    wd.on_job_missed(_FakeEvent("reconcile", scheduled))
    await asyncio.sleep(0)
    assert _capture_notify == []


async def test_large_delay_alerts(_capture_notify):
    scheduled = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    wd.on_job_missed(_FakeEvent("keepalive", scheduled))
    await asyncio.sleep(0)
    assert len(_capture_notify) == 1
    event, fields = _capture_notify[0]
    assert event == "system.possible_sleep_or_freeze"
    assert fields["missed_job"] == "keepalive"


async def test_burst_of_misses_debounced_to_one_alert(_capture_notify):
    scheduled = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    for job_id in ("keepalive", "reconcile", "drawdown", "rsi_gold_15m"):
        wd.on_job_missed(_FakeEvent(job_id, scheduled))
    await asyncio.sleep(0)
    assert len(_capture_notify) == 1


async def test_handles_tz_aware_scheduled_time(_capture_notify):
    scheduled = datetime.now(UTC) - timedelta(minutes=10)
    assert scheduled.tzinfo is not None
    wd.on_job_missed(_FakeEvent("keepalive", scheduled))
    await asyncio.sleep(0)
    assert len(_capture_notify) == 1
