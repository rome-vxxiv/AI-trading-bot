"""Invoke Claude Code non-interactively for a read-only analysis pass.

Windows/subprocess notes learned the hard way:
- claude.exe is exposed as claude.cmd on Windows (the npm shim). Passing a
  multi-line -p prompt through cmd.exe truncates at the first newline, and
  shell metacharacters (< > |) break argument parsing entirely. So we feed
  the prompt via STDIN.
- --mcp-config isn't a reliable channel for a spawned session either; the
  right way is to register once with `claude mcp add`, which persists in
  ~/.claude.json for the project.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from ..settings import get_settings
from ..state import Signal
from ..state.db import session_scope

log = get_logger(__name__)

ALLOWED_TOOLS = ",".join([
    "mcp__capital-com__cap_market_prices",
    "mcp__capital-com__cap_market_sentiment",
])
DENIED_TOOLS = ",".join([
    "mcp__capital-com__cap_trade_preview_position",
    "mcp__capital-com__cap_trade_execute_position",
    "mcp__capital-com__cap_trade_preview_working_order",
    "mcp__capital-com__cap_trade_execute_working_order",
    "mcp__capital-com__cap_trade_execute_working_order_update",
    "mcp__capital-com__cap_trade_positions_close",
    "mcp__capital-com__cap_trade_orders_cancel",
])


async def run_analysis_once(epic: str, strategy_id: str = "readonly_analysis") -> dict[str, Any]:
    settings = get_settings()
    prompt_path = Path("prompts/readonly_analysis.md")

    if not prompt_path.exists():
        log.error("analysis.missing_prompt", path=str(prompt_path))
        return {"_error": "missing_prompt"}

    _write_tick_context(epic=epic, state_dir=settings.capital_agent_state_dir)

    # Template substitution — the prompt file uses {EPIC} placeholders.
    prompt = prompt_path.read_text(encoding="utf-8").replace("{EPIC}", epic)

    claude_bin = _resolve_claude_binary()
    if claude_bin is None:
        log.error("analysis.claude_not_found",
                  hint="npm install -g @anthropic-ai/claude-code")
        return {"_error": "claude_not_found"}
    log.info("analysis.claude_bin", path=claude_bin, epic=epic)

    # Register capital-com once (idempotent on the same command).
    await _ensure_mcp_registered(claude_bin)

    args = [
        claude_bin, "-p",
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DENIED_TOOLS,
        "--max-turns", "8",
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
            timeout=180,
        )
    except TimeoutError:
        log.error("analysis.timeout", epic=epic, seconds=180)
        return {"_error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.error("analysis.spawn_error", epic=epic, error=str(exc)[:200])
        return {"_error": "spawn_error", "_message": str(exc)[:200]}

    latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    stdout_s = stdout.decode("utf-8", errors="replace")
    stderr_s = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        log.error("analysis.nonzero_exit", epic=epic, code=proc.returncode,
                  stderr=stderr_s[:400], stdout_tail=stdout_s[-400:])
        return {"_error": "nonzero_exit", "_code": proc.returncode}

    verdict = _extract_verdict_json(stdout_s)
    if verdict is None:
        log.warning("analysis.parse_error", epic=epic, stdout_tail=stdout_s[-500:])
        return {"_error": "parse_error"}

    verdict.setdefault("epic", epic)
    verdict["_latency_ms"] = latency_ms
    verdict["_strategy_id"] = strategy_id
    await _save_signal(strategy_id=strategy_id, epic=epic, verdict=verdict)
    log.info("analysis.ok", epic=epic, verdict=verdict.get("verdict"),
             last_close=verdict.get("last_close"), latency_ms=latency_ms)
    return verdict


async def _ensure_mcp_registered(claude_bin: str) -> None:
    """`claude mcp add capital-com -- <python> -m capital_mcp` — idempotent.
    Cached in ~/.claude.json per-project. If the server is already registered,
    the add call fails with a clean 'already exists' message which we ignore."""
    import sys as _sys
    python_exe = _sys.executable
    add_args = [claude_bin, "mcp", "add", "capital-com", "--",
                python_exe, "-m", "capital_mcp"]
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
            # "already exists" is fine; log as info and continue.
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


def _write_tick_context(epic: str, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"epic": epic, "ts_utc": datetime.now(UTC).isoformat(),
           "note": "Read-only analysis pass. Do NOT open or modify positions."}
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
            decision=str(verdict.get("verdict") or "unknown"),
            reason=str(verdict.get("reason") or "")[:2000],
            model_output_json=verdict,
        ))
        await s.commit()
