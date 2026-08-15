"""Parameterized playbook runner. One code path for every strategy —
each strategy is a (prompt_file, allowed_tools, denied_tools, strategy_id)
tuple. Callers use `run_playbook_once("rsi_mean_reversion", epic)`.

Trading-tool safety layers, still all three:
1. `--allowedTools` whitelists exactly what each strategy needs.
2. `--disallowedTools` explicitly denies execute even when Claude Code
   might otherwise pick up a tool.
3. `CAP_DRY_RUN=true` in .env makes the MCP server itself refuse every
   execute call regardless of what our driver sent.

The runner also refuses to spawn the strategy if `CAP_DRY_RUN` is false
AND the strategy is marked `require_dry_run: True` — an accidental
config flip can't send a step-4 strategy to production.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys as _sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from ..settings import get_settings
from ..state import Signal
from ..state.db import session_scope

log = get_logger(__name__)

# ------------------------------------------------------------------
# Playbook registry
# ------------------------------------------------------------------

# Trading tools are ALWAYS denied at the driver level, even when a
# playbook doesn't need trading — belt over the strategy allowlist.
COMMON_DENIED = [
    "mcp__capital-com__cap_trade_execute_position",
    "mcp__capital-com__cap_trade_execute_working_order",
    "mcp__capital-com__cap_trade_execute_working_order_update",
    "mcp__capital-com__cap_trade_positions_close",
    "mcp__capital-com__cap_trade_orders_cancel",
]


@dataclass(frozen=True)
class PlaybookSpec:
    strategy_id: str
    prompt_file: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...] = ()
    require_dry_run: bool = False   # step 4 defaults; step 6 flips off

    @property
    def allowed_csv(self) -> str:
        return ",".join(self.allowed_tools)

    @property
    def denied_csv(self) -> str:
        return ",".join((*self.denied_tools, *COMMON_DENIED))


PLAYBOOKS: dict[str, PlaybookSpec] = {
    "readonly_analysis": PlaybookSpec(
        strategy_id="readonly_analysis",
        prompt_file="prompts/readonly_analysis.md",
        allowed_tools=(
            "mcp__capital-com__cap_market_prices",
            "mcp__capital-com__cap_market_sentiment",
        ),
        # Extra deny even the preview tools — this playbook is read-only.
        denied_tools=(
            "mcp__capital-com__cap_trade_preview_position",
            "mcp__capital-com__cap_trade_preview_working_order",
            "mcp__capital-com__cap_trade_preview_working_order_update",
        ),
    ),
    "rsi_mean_reversion": PlaybookSpec(
        strategy_id="rsi_mean_reversion",
        prompt_file="prompts/rsi_mean_reversion.md",
        allowed_tools=(
            "mcp__capital-com__cap_market_prices",
            "mcp__capital-com__cap_market_sentiment",
            "mcp__capital-com__cap_trade_preview_position",
        ),
        # No extra denies here; COMMON_DENIED already blocks execute.
        require_dry_run=True,
    ),
}


# ------------------------------------------------------------------
# Driver
# ------------------------------------------------------------------


async def run_playbook_once(strategy: str, epic: str) -> dict[str, Any]:
    if strategy not in PLAYBOOKS:
        log.error("playbook.unknown", strategy=strategy,
                  known=list(PLAYBOOKS.keys()))
        return {"_error": "unknown_playbook"}
    spec = PLAYBOOKS[strategy]

    if spec.require_dry_run and os.environ.get("CAP_DRY_RUN", "").lower() != "true":
        log.error("playbook.dry_run_required", strategy=strategy,
                  hint="set CAP_DRY_RUN=true in .env")
        return {"_error": "dry_run_required"}

    settings = get_settings()
    prompt_path = Path(spec.prompt_file)
    if not prompt_path.exists():
        log.error("playbook.missing_prompt", strategy=strategy, path=str(prompt_path))
        return {"_error": "missing_prompt"}

    _write_tick_context(epic=epic, strategy=strategy,
                        state_dir=settings.capital_agent_state_dir)
    prompt = prompt_path.read_text(encoding="utf-8").replace("{EPIC}", epic)

    claude_bin = _resolve_claude_binary()
    if claude_bin is None:
        log.error("playbook.claude_not_found",
                  hint="npm install -g @anthropic-ai/claude-code")
        return {"_error": "claude_not_found"}
    log.info("playbook.start", strategy=strategy, epic=epic, claude_bin=claude_bin,
             allowed=spec.allowed_csv, dry_run=os.environ.get("CAP_DRY_RUN"))

    await _ensure_mcp_registered(claude_bin)

    args = [
        claude_bin, "-p",
        "--allowedTools", spec.allowed_csv,
        "--disallowedTools", spec.denied_csv,
        "--max-turns", "15",
    ]

    started = datetime.now(UTC)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
            cwd=os.getcwd(),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=240,
        )
    except TimeoutError:
        log.error("playbook.timeout", strategy=strategy, epic=epic)
        return {"_error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.error("playbook.spawn_error", strategy=strategy,
                  epic=epic, error=str(exc)[:200])
        return {"_error": "spawn_error", "_message": str(exc)[:200]}

    latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    stdout_s = stdout.decode("utf-8", errors="replace")
    stderr_s = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        log.error("playbook.nonzero_exit", strategy=strategy, epic=epic,
                  code=proc.returncode, stderr=stderr_s[:400],
                  stdout_tail=stdout_s[-400:])
        return {"_error": "nonzero_exit", "_code": proc.returncode}

    verdict = _extract_verdict_json(stdout_s)
    if verdict is None:
        log.warning("playbook.parse_error", strategy=strategy, epic=epic,
                    stdout_tail=stdout_s[-500:])
        return {"_error": "parse_error"}

    verdict.setdefault("epic", epic)
    verdict["_latency_ms"] = latency_ms
    verdict["_strategy_id"] = strategy
    await _save_signal(strategy_id=strategy, epic=epic, verdict=verdict)
    log.info("playbook.ok", strategy=strategy, epic=epic,
             decision=verdict.get("decision") or verdict.get("verdict"),
             preview_id=verdict.get("preview_id"),
             latency_ms=latency_ms)
    return verdict


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _ensure_mcp_registered(claude_bin: str) -> None:
    add_args = [claude_bin, "mcp", "add", "capital-com", "--",
                _sys.executable, "-m", "capital_mcp"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *add_args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
        if proc.returncode == 0:
            log.info("mcp_register.ok", server="capital-com", tail=out[-160:])
        else:
            log.info("mcp_register.skipped", server="capital-com",
                     code=proc.returncode, tail=out[-160:])
    except Exception as exc:  # noqa: BLE001
        log.warning("mcp_register.error", error=str(exc)[:200])


def _resolve_claude_binary() -> str | None:
    from shutil import which
    if os.name == "nt":
        for candidate in (
            os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
            os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.exe"),
        ):
            if candidate and os.path.exists(candidate):
                return candidate
        for name in ("claude.cmd", "claude.exe"):
            p = which(name)
            if p:
                return p
    return which("claude")


def _write_tick_context(epic: str, strategy: str, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"epic": epic, "strategy": strategy,
           "ts_utc": datetime.now(UTC).isoformat(),
           "dry_run": os.environ.get("CAP_DRY_RUN", "false")}
    (state_dir / "tick-context.json").write_text(
        json.dumps(ctx, indent=2), encoding="utf-8")


def _iter_balanced_json_objects(s: str):
    n = len(s); i = 0
    while i < n:
        if s[i] != "{":
            i += 1; continue
        depth = 0; in_str = False; esc = False
        for j in range(i, n):
            c = s[j]
            if in_str:
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == '"': in_str = False
                continue
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield s[i:j + 1]
                    i = j + 1
                    break
        else:
            return
        continue


def _extract_verdict_json(claude_stdout: str) -> dict[str, Any] | None:
    try:
        envelope = json.loads(claude_stdout)
    except json.JSONDecodeError:
        return _greedy_scan(claude_stdout)
    if isinstance(envelope, dict) and "epic" in envelope:
        return envelope
    text = None
    if isinstance(envelope, dict):
        text = envelope.get("result")
        if text is None:
            msgs = envelope.get("messages") or envelope.get("transcript")
            if isinstance(msgs, list):
                for m in reversed(msgs):
                    if (m or {}).get("role") == "assistant":
                        c = m.get("content")
                        text = c if isinstance(c, str) else _stringify_content(c)
                        break
    if not text:
        return None
    return _greedy_scan(text)


def _stringify_content(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return str(content)


def _greedy_scan(s: str) -> dict[str, Any] | None:
    for chunk in _iter_balanced_json_objects(s):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "epic" in obj:
            return obj
    return None


async def _save_signal(strategy_id: str, epic: str, verdict: dict[str, Any]) -> None:
    async with session_scope() as s:
        s.add(Signal(
            strategy_id=strategy_id, epic=epic,
            decision=str(verdict.get("decision") or verdict.get("verdict") or "unknown"),
            reason=str(verdict.get("reason") or "")[:2000],
            model_output_json=verdict,
        ))
        await s.commit()
