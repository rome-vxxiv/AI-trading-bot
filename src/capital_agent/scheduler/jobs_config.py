"""Loads config/jobs.yaml into a list of JobDef the scheduler registers
at boot. Adding a new instrument to an existing strategy/cadence is a
config-only change — no new Python function needed, and the `tiers:`
block in the yaml is the real source of cadence (not a hardcoded
Python mapping the file merely documents).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..logging_config import get_logger

log = get_logger(__name__)

_DEFAULT_TIER_MINUTES = {"fast": 5, "medium": 15, "slow": 60}


@dataclass(frozen=True)
class JobDef:
    id: str
    strategy: str
    epic: str
    tier: str
    cron_minutes: str


def _cron_minutes_from_interval(minutes: int) -> str:
    if minutes <= 0 or minutes > 60:
        raise ValueError(f"tier minutes must be 1-60, got {minutes}")
    return ",".join(str(m) for m in range(0, 60, minutes))


def load_jobs(path: Path) -> list[JobDef]:
    if not path.exists():
        log.warning("jobs_yaml.missing", path=str(path))
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    tier_minutes = dict(_DEFAULT_TIER_MINUTES)
    for name, cfg in (raw.get("tiers") or {}).items():
        if isinstance(cfg, dict) and "minutes" in cfg:
            try:
                tier_minutes[name] = int(cfg["minutes"])
            except (TypeError, ValueError):
                log.warning("jobs_yaml.bad_tier_minutes", tier=name, raw=cfg)

    out: list[JobDef] = []
    for entry in raw.get("jobs") or []:
        if not entry.get("enabled", False):
            continue
        job_id = str(entry.get("id") or "")
        strategy = str(entry.get("strategy") or "")
        epic = str(entry.get("epic") or "")
        tier = str(entry.get("tier") or "medium")
        if not job_id or not strategy or not epic:
            log.warning("jobs_yaml.incomplete_entry", entry=entry)
            continue

        minutes = tier_minutes.get(tier)
        if minutes is None:
            log.warning("jobs_yaml.unknown_tier", id=job_id, tier=tier,
                       known=list(tier_minutes.keys()))
            continue
        try:
            cron_minutes = _cron_minutes_from_interval(minutes)
        except ValueError as exc:
            log.warning("jobs_yaml.bad_tier_minutes", id=job_id, tier=tier, error=str(exc))
            continue

        out.append(JobDef(id=job_id, strategy=strategy, epic=epic, tier=tier,
                          cron_minutes=cron_minutes))
    return out
