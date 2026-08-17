"""config/jobs.yaml loading -> JobDef list. `tiers:` must be genuinely
read (cadence source of truth), not decorative."""

import pytest

from capital_agent.scheduler.jobs_config import (
    JobDef,
    _cron_minutes_from_interval,
    load_jobs,
)


def test_cron_minutes_from_interval():
    assert _cron_minutes_from_interval(15) == "0,15,30,45"
    assert _cron_minutes_from_interval(60) == "0"
    assert _cron_minutes_from_interval(5) == "0,5,10,15,20,25,30,35,40,45,50,55"


def test_cron_minutes_from_interval_rejects_out_of_range():
    with pytest.raises(ValueError):
        _cron_minutes_from_interval(0)
    with pytest.raises(ValueError):
        _cron_minutes_from_interval(61)


def test_missing_file_returns_empty_list(tmp_path):
    assert load_jobs(tmp_path / "nope.yaml") == []


def test_load_jobs_basic(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "tiers:\n"
        "  medium: { minutes: 15 }\n"
        "jobs:\n"
        "  - id: rsi_gold_15m\n"
        "    enabled: true\n"
        "    strategy: rsi_mean_reversion\n"
        "    epic: GOLD\n"
        "    tier: medium\n",
        encoding="utf-8",
    )
    jobs = load_jobs(p)
    assert jobs == [
        JobDef(id="rsi_gold_15m", strategy="rsi_mean_reversion", epic="GOLD",
               tier="medium", cron_minutes="0,15,30,45")
    ]


def test_load_jobs_skips_disabled(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "jobs:\n"
        "  - id: disabled_job\n"
        "    enabled: false\n"
        "    strategy: rsi_mean_reversion\n"
        "    epic: GOLD\n"
        "    tier: medium\n",
        encoding="utf-8",
    )
    assert load_jobs(p) == []


def test_load_jobs_skips_incomplete_entry(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "jobs:\n"
        "  - id: no_epic\n"
        "    enabled: true\n"
        "    strategy: rsi_mean_reversion\n"
        "    tier: medium\n",
        encoding="utf-8",
    )
    assert load_jobs(p) == []


def test_load_jobs_unknown_tier_skipped(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "jobs:\n"
        "  - id: weird_tier\n"
        "    enabled: true\n"
        "    strategy: rsi_mean_reversion\n"
        "    epic: GOLD\n"
        "    tier: glacial\n",
        encoding="utf-8",
    )
    assert load_jobs(p) == []


def test_load_jobs_defaults_tier_to_medium(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "jobs:\n"
        "  - id: no_tier\n"
        "    enabled: true\n"
        "    strategy: rsi_mean_reversion\n"
        "    epic: GOLD\n",
        encoding="utf-8",
    )
    jobs = load_jobs(p)
    assert len(jobs) == 1
    assert jobs[0].tier == "medium"
    assert jobs[0].cron_minutes == "0,15,30,45"


def test_load_jobs_custom_tier_minutes_override(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "tiers:\n"
        "  fast: { minutes: 10 }\n"
        "jobs:\n"
        "  - id: fast_job\n"
        "    enabled: true\n"
        "    strategy: rsi_mean_reversion\n"
        "    epic: BTCUSD\n"
        "    tier: fast\n",
        encoding="utf-8",
    )
    jobs = load_jobs(p)
    assert jobs[0].cron_minutes == "0,10,20,30,40,50"


def test_load_jobs_multiple_instruments_preserve_order(tmp_path):
    p = tmp_path / "jobs.yaml"
    p.write_text(
        "jobs:\n"
        "  - id: a\n    enabled: true\n    strategy: rsi_mean_reversion\n    epic: GOLD\n    tier: medium\n"
        "  - id: b\n    enabled: true\n    strategy: rsi_mean_reversion\n    epic: BTCUSD\n    tier: medium\n",
        encoding="utf-8",
    )
    jobs = load_jobs(p)
    assert [j.epic for j in jobs] == ["GOLD", "BTCUSD"]
