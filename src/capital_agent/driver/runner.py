"""Parameterized playbook runner. One code path for every strategy.

Flow (step 5+):
  1. Load risk policy from config/risk.yaml.
  2. Preflight: kill switch, allowlist, max positions, cooldowns.
     Reject early with a Telegram alert if any check fails; no LLM call.
  3. If the playbook is a strategy (not read-only), fetch bars via
     the persistent MCPClient and pre-compute RSI/ATR in Python.
     Write pre-computed values to state/tick-context.json.
  4. Spawn Claude Code via stdin, allowedTools whitelist + disallowedTools
     denylist. Add `Read` to allowedTools for strategies so the LLM can
     read tick-context.json.
  5. Parse verdict. Postflight-validate any preview response.
  6. Save Signal, emit alert on non-hold decisions and on any rejection.

Triple-layer trade safety (unchanged from step 4):
  - --allowedTools whitelist
  - --disallowedTools blocklist
  - CAP_DRY_RUN=true at the MCP layer
"""

from __future__ import annotations

import asyncio
import json
import os
import sys as _sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..alerts import notify
from ..execute import execute_preview
from ..logging_config import get_logger
from ..mcp_client import MCPClient, lifespan_mcp
from ..risk import (
    RiskPolicy,
    ensure_killswitch_row,
    get_policy,
    postflight,
    preflight,
)
from ..settings import get_settings
from ..state import PositionsLocal, Signal, init_db
from ..state.db import session_scope
from ..strategy import prepare_tick_context

log = get_logger(__name__)


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
    require_dry_run: bool = False
    needs_preflight_context: bool = False    # Python-side indicators?
    resolution: str = "MINUTE_15"
    max_bars: int = 60
    # Live-mode-only fields.
    executes_after_preview: bool = False     # Python calls execute after Claude's preview
    require_live_fuse: bool = False          # I_UNDERSTAND_LIVE_RISK=YES required

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
            "Read",
            "mcp__capital-com__cap_trade_preview_position",
        ),
        require_dry_run=True,
        needs_preflight_context=True,
        resolution="MINUTE_15",
        max_bars=60,
    ),
    # Step-6 live variant. Same playbook prompt — Claude still only
    # PREVIEWS. Python calls execute after our postflight approves.
    "rsi_mean_reversion_live": PlaybookSpec(
        strategy_id="rsi_mean_reversion_live",
        prompt_file="prompts/rsi_mean_reversion.md",
        allowed_tools=(
            "Read",
            "mcp__capital-com__cap_trade_preview_position",
        ),
        require_dry_run=False,          # CAP_DRY_RUN must be "false"
        require_live_fuse=True,         # I_UNDERSTAND_LIVE_RISK must be "YES"
        needs_preflight_context=True,
        executes_after_preview=True,
        resolution="MINUTE_15",
        max_bars=60,
    ),
}


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
    if spec.require_live_fuse:
        if os.environ.get("CAP_DRY_RUN", "").lower() == "true":
            log.error("playbook.live_needs_dry_run_off", strategy=strategy,
                      hint="live strategy runs need CAP_DRY_RUN=false")
            return {"_error": "live_needs_dry_run_off"}
        if os.environ.get("I_UNDERSTAND_LIVE_RISK", "NO") != "YES":
            log.error("playbook.live_fuse_missing", strategy=strategy,
                      hint="set I_UNDERSTAND_LIVE_RISK=YES in .env only after go-live ceremony")
            return {"_error": "live_fuse_missing"}
        log.warning("playbook.LIVE_MODE_ACTIVE", strategy=strategy,
                    hint="Real orders WILL be placed if signal fires and postflight passes")

    settings = get_settings()
    await init_db(settings.capital_agent_state_dir)
    await ensure_killswitch_row()
    policy = get_policy(settings.capital_agent_config_dir)

    # ---------------- Pre-flight risk checks ---------------------
    pre = await preflight(epic=epic, strategy_id=strategy, policy=policy)
    if not pre.ok:
        await notify("playbook.rejected", strategy=strategy, epic=epic,
                     reasons=";".join(pre.reasons)[:200])
        return {"_error": "preflight_rejected", "reasons": pre.reasons,
                "fields": pre.fields}

    # ---------------- Prompt + Claude binary ---------------------
    prompt_path = Path(spec.prompt_file)
    if not prompt_path.exists():
        log.error("playbook.missing_prompt", strategy=strategy, path=str(prompt_path))
        return {"_error": "missing_prompt"}
    prompt = prompt_path.read_text(encoding="utf-8").replace("{EPIC}", epic)

    claude_bin = _resolve_claude_binary()
    if claude_bin is None:
        log.error("playbook.claude_not_found",
                  hint="npm install -g @anthropic-ai/claude-code")
        await notify("playbook.claude_not_found", strategy=strategy, epic=epic)
        return {"_error": "claude_not_found"}
    log.info("playbook.start", strategy=strategy, epic=epic, claude_bin=claude_bin,
             allowed=spec.allowed_csv, dry_run=os.environ.get("CAP_DRY_RUN"))

    await _ensure_mcp_registered(claude_bin)

    # ---------------- Python pre-compute -------------------------
    ctx = None
    if spec.needs_preflight_context:
        async with lifespan_mcp() as mcp:
            balance = await _fetch_active_balance(mcp)
            ctx = await prepare_tick_context(
                mcp=mcp, epic=epic, strategy_id=strategy,
                resolution=spec.resolution, max_bars=spec.max_bars,
                state_dir=settings.capital_agent_state_dir,
                account_balance=balance, policy=policy,
            )
        if ctx is None:
            await notify("playbook.context_prep_failed", strategy=strategy, epic=epic)
            return {"_error": "preflight_context_failed"}

    # ---------------- Spawn Claude -------------------------------
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
            timeout=180,
        )
    except TimeoutError:
        log.error("playbook.timeout", strategy=strategy, epic=epic)
        await notify("playbook.timeout", strategy=strategy, epic=epic)
        return {"_error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.error("playbook.spawn_error", strategy=strategy, epic=epic,
                  error=str(exc)[:200])
        return {"_error": "spawn_error", "_message": str(exc)[:200]}

    latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    stdout_s = stdout.decode("utf-8", errors="replace")
    stderr_s = stderr.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        log.error("playbook.nonzero_exit", strategy=strategy, epic=epic,
                  code=proc.returncode, stderr=stderr_s[:400],
                  stdout_tail=stdout_s[-400:])
        await notify("playbook.nonzero_exit", strategy=strategy, epic=epic,
                     code=proc.returncode)
        return {"_error": "nonzero_exit", "_code": proc.returncode}

    verdict = _extract_verdict_json(stdout_s)
    if verdict is None:
        log.warning("playbook.parse_error", strategy=strategy, epic=epic,
                    stdout_tail=stdout_s[-500:])
        await notify("playbook.parse_error", strategy=strategy, epic=epic)
        return {"_error": "parse_error"}

    verdict.setdefault("epic", epic)
    verdict["_latency_ms"] = latency_ms
    verdict["_strategy_id"] = strategy

    # ---------------- Post-flight validation ---------------------
    atr_used = None
    if isinstance(verdict.get("atr_14"), int | float):
        atr_used = float(verdict["atr_14"])
    elif ctx is not None:
        atr_used = ctx.get("atr_14")

    post = postflight(verdict, policy, atr_used=atr_used)
    verdict["_postflight_ok"] = post.ok
    verdict["_postflight_reasons"] = post.reasons

    decision = verdict.get("decision") or verdict.get("verdict") or "hold"

    # ---------------- Execute (LIVE ONLY) ------------------------
    # Only fired when the spec is a live variant AND postflight approved
    # AND the model returned a real preview_id AND decision != hold.
    if (spec.executes_after_preview and post.ok
            and decision in ("enter_long", "enter_short")
            and verdict.get("preview_id")):
        async with lifespan_mcp() as exec_mcp:
            exec_result = await execute_preview(exec_mcp,
                                                preview_id=str(verdict["preview_id"]))
        verdict["_execute"] = exec_result
        if exec_result.get("ok"):
            await _record_new_position(
                deal_id=exec_result.get("deal_id") or exec_result.get("deal_reference") or "",
                epic=epic, decision=decision, verdict=verdict,
                strategy_id=strategy, atr_used=atr_used, ctx=ctx,
            )
            await notify("execute.ok", strategy=strategy, epic=epic,
                         deal_id=str(exec_result.get("deal_id"))[:12],
                         decision=decision, latency_ms=latency_ms)
        else:
            await notify("execute.failed", strategy=strategy, epic=epic,
                         status=exec_result.get("status"),
                         reason=str(exec_result.get("reason") or "")[:120])

    await _save_signal(strategy_id=strategy, epic=epic, verdict=verdict)

    log.info("playbook.ok", strategy=strategy, epic=epic, decision=decision,
             preview_id=verdict.get("preview_id"), postflight_ok=post.ok,
             executed=bool(verdict.get("_execute", {}).get("ok")),
             latency_ms=latency_ms)

    if decision != "hold":
        await notify("playbook.decision", strategy=strategy, epic=epic,
                     decision=decision,
                     preview_id=str(verdict.get("preview_id"))[:16],
                     postflight_ok=post.ok, latency_ms=latency_ms)
    return verdict


async def _record_new_position(*, deal_id: str, epic: str, decision: str,
                               verdict: dict, strategy_id: str,
                               atr_used: float | None, ctx: dict | None) -> None:
    """Insert a PositionsLocal row for the newly-opened position so
    reconcile can track it and outcome_tagger can tag P&L when it closes."""
    if not deal_id:
        log.warning("record.no_deal_id", verdict=str(verdict)[:200])
        return
    direction = "BUY" if decision == "enter_long" else "SELL"
    entry = (ctx or {}).get("last_close") or 0.0
    stop = None
    tp = None
    if ctx:
        sd = ctx.get("stop_distance")
        pd = ctx.get("profit_distance")
        if isinstance(sd, int | float) and entry:
            stop = entry - sd if direction == "BUY" else entry + sd
        if isinstance(pd, int | float) and entry:
            tp = entry + pd if direction == "BUY" else entry - pd
    async with session_scope() as s:
        s.add(PositionsLocal(
            deal_id=deal_id, epic=epic, direction=direction,
            size=float((ctx or {}).get("suggested_size") or 0.0),
            entry=float(entry), stop=stop, tp=tp,
            strategy_id=strategy_id,
        ))
        await s.commit()
    log.info("position.recorded", deal_id=deal_id, epic=epic,
             direction=direction, entry=entry, stop=stop, tp=tp)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _fetch_active_balance(mcp: MCPClient) -> float | None:
    try:
        accts = await mcp.call("cap_account_list", timeout_s=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("balance.fetch_error", error=str(exc)[:200])
        return None
    if not isinstance(accts, dict):
        return None
    active_id = accts.get("active_account_id")
    for a in (accts.get("accounts") or []):
        if isinstance(a, dict) and (a.get("accountId") == active_id or a.get("preferred")):
            bal = (a.get("balance") or {}).get("balance")
            if isinstance(bal, int | float):
                return float(bal)
    # Fallback to any first account.
    for a in (accts.get("accounts") or []):
        if isinstance(a, dict):
            bal = (a.get("balance") or {}).get("balance")
            if isinstance(bal, int | float):
                return float(bal)
    return None


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
