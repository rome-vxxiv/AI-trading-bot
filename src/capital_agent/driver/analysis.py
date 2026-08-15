"""Backwards-compatible shim. The real logic now lives in `runner.py`
which handles both the read-only analysis and the strategy playbooks."""

from __future__ import annotations

from typing import Any

from .runner import run_playbook_once


async def run_analysis_once(epic: str,
                            strategy_id: str = "readonly_analysis") -> dict[str, Any]:
    """Kept for callers that used the step-3 name; forwards to the runner."""
    # strategy_id is a caller-supplied label but we always use the
    # read-only playbook here — the runner picks the prompt + tool set
    # by strategy name.
    _ = strategy_id
    return await run_playbook_once("readonly_analysis", epic)
