"""Invoke Claude Code non-interactively for a read-only analysis pass.

Design notes:
- We spawn `claude -p` per tick. Each invocation opens its own MCP
  subprocess (Claude Code launches capital-mcp via config/mcp.json),
  authenticates, calls the two whitelisted tools, and returns.
- `--allowedTools` is the real safety layer: even if the prompt is
  compromised or the model hallucinates a trade call, Claude Code
  refuses any tool not in the list. We ONLY allow the two read-only
  market tools and the Read filesystem tool.
- Output is JSON per `--output-format json`. We parse the transcript's
  final assistant message and pull the JSON verdict out. Malformed
  output is logged as `analysis.parse_error` and no signal is stored.
- Cost is bounded: `--max-turns 8` puts a hard ceiling on tool-call
  ping-pong; a single sensible analysis run needs ~4 turns.
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

# Tools that are allowed to be called by the analysis driver. The prompt
# also forbids trading tools, but --allowedTools is the enforcement.
ALLOWED_TOOLS = ",".join([
    "Read",
    "mcp__capital-com__cap_market_prices",
    "mcp__capital-com__cap_market_sentiment",
])

# Explicitly denied — never trade in the analysis driver, even if the
# model or a subverted prompt tries to. Belt over allowedTools.
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
    """Fire one Claude Code invocation for `epic`. Returns a summary dict
    with `verdict` on success or `_error` on failure. Always non-raising."""
    settings = get_settings()
    prompt_path = Path("prompts/readonly_analysis.md")
    mcp_config = Path("config/mcp.json")

    if not prompt_path.exists():
        log.error("analysis.missing_prompt", path=str(prompt_path))
        return {"_error": "missing_prompt"}
    if not mcp_config.exists():
        log.error("analysis.missing_mcp_config", path=str(mcp_config))
        return {"_error": "missing_mcp_config"}

    _write_tick_context(epic=epic, state_dir=settings.capital_agent_state_dir)

    prompt = prompt_path.read_text(encoding="utf-8")
    # Nudge Claude toward this specific epic. The prompt tells it to
    # read tick-context.json for the epic; this is a belt on top.
    prompt_with_target = f"{prompt}\n\n---\nTarget epic for THIS invocation: **{epic}**\n"

    # Locate the claude CLI. On Windows npm global bin is on PATH.
    claude_bin = _resolve_claude_binary()
    if claude_bin is None:
        log.error("analysis.claude_not_found",
                  hint="npm install -g @anthropic-ai/claude-code")
        return {"_error": "claude_not_found"}

    args = [
        claude_bin, "-p", prompt_with_target,
        "--mcp-config", str(mcp_config),
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DENIED_TOOLS,
        "--output-format", "json",
        "--max-turns", "8",
    ]

    started = datetime.now(UTC)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except TimeoutError:
        log.error("analysis.timeout", epic=epic, seconds=180)
        return {"_error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.error("analysis.spawn_error", epic=epic, error=str(exc)[:200])
        return {"_error": "spawn_error", "_message": str(exc)[:200]}

    latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    if proc.returncode != 0:
        log.error("analysis.nonzero_exit",
                  epic=epic, code=proc.returncode,
                  stderr=stderr.decode("utf-8", errors="replace")[:400])
        return {"_error": "nonzero_exit", "_code": proc.returncode}

    verdict = _extract_verdict_json(stdout.decode("utf-8", errors="replace"))
    if verdict is None:
        log.warning("analysis.parse_error", epic=epic,
                    stdout_tail=stdout.decode("utf-8", errors="replace")[-500:])
        return {"_error": "parse_error"}

    verdict.setdefault("epic", epic)
    verdict["_latency_ms"] = latency_ms
    verdict["_strategy_id"] = strategy_id

    await _save_signal(strategy_id=strategy_id, epic=epic, verdict=verdict)
    log.info("analysis.ok",
             epic=epic,
             verdict=verdict.get("verdict"),
             rsi_14=verdict.get("rsi_14"),
             last_close=verdict.get("last_close"),
             latency_ms=latency_ms)
    return verdict


def _resolve_claude_binary() -> str | None:
    """Return the claude executable path, or None if not on PATH."""
    from shutil import which
    for name in ("claude", "claude.cmd", "claude.exe"):
        p = which(name)
        if p:
            return p
    return None


def _write_tick_context(epic: str, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    ctx = {
        "epic": epic,
        "ts_utc": datetime.now(UTC).isoformat(),
        "note": ("Read-only analysis pass. Do NOT open or modify "
                 "positions. See prompts/readonly_analysis.md."),
    }
    (state_dir / "tick-context.json").write_text(
        json.dumps(ctx, indent=2), encoding="utf-8",
    )


def _iter_balanced_json_objects(s: str):
    """Yield substrings of s that are balanced `{...}` (naive bracket
    balancer, respects strings and escapes). Enough for our use — the
    model produces one small verdict object at the end."""
    n = len(s)
    i = 0
    while i < n:
        if s[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
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
    """Parse `claude -p --output-format json` output and pull out the
    final assistant message's JSON verdict."""
    try:
        envelope = json.loads(claude_stdout)
    except json.JSONDecodeError:
        return _greedy_scan(claude_stdout)

    # If the envelope IS the verdict (raw JSON, no wrapper), return it.
    if isinstance(envelope, dict) and "epic" in envelope:
        return envelope

    # Claude Code's --output-format json envelope has a `result` field
    # containing the final assistant text (or a transcript array on some
    # versions). We try both shapes.
    text = None
    if isinstance(envelope, dict):
        text = envelope.get("result")
        if text is None:
            # Older shape: {"messages": [{"role": "assistant", "content": ...}]}
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
    """Return the first balanced JSON object with an `epic` key."""
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
            strategy_id=strategy_id,
            epic=epic,
            decision=str(verdict.get("verdict") or "unknown"),
            reason=str(verdict.get("reason") or "")[:2000],
            model_output_json=verdict,
        ))
        await s.commit()
