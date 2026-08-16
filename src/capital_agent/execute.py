"""Python-side execute. Called by the strategy runner AFTER Claude
returns a preview_id AND our postflight approves. Claude never has
cap_trade_execute_position in its allowedTools — the LLM's authority
tops out at preview.

Flow:
  1. runner.py validates the preview response via postflight.
  2. If postflight OK and live mode → this module is called.
  3. Calls cap_trade_execute_position(preview_id, confirm=True).
  4. Calls cap_trade_confirm_wait to get ACCEPTED/REJECTED from broker.
  5. Returns a rich result with deal_id, deal_reference, status, and any
     broker rejection reason. The runner records this into the Signal
     row and alerts Telegram.

Live-mode fuses (checked here even though the runner also checks):
  - CAP_DRY_RUN != "true" — else the MCP itself refuses execute.
  - I_UNDERSTAND_LIVE_RISK == "YES" — our own top-level fuse.
"""

from __future__ import annotations

import os
from typing import Any

from .logging_config import get_logger
from .mcp_client import MCPClient

log = get_logger(__name__)


async def execute_preview(mcp: MCPClient, preview_id: str,
                          wait_for_confirm: bool = True,
                          timeout_s: float = 20.0) -> dict[str, Any]:
    """Execute a preview_id. Returns:
      {ok: bool, deal_reference: str|None, deal_id: str|None,
       status: str, reason: str|None, raw: <full MCP response>}
    Never raises — errors surface as ok=False with a reason field."""
    if os.environ.get("CAP_DRY_RUN", "").lower() == "true":
        log.error("execute.refused_dry_run", preview_id=preview_id)
        return {"ok": False, "status": "REFUSED_DRY_RUN",
                "reason": "CAP_DRY_RUN=true; execute path blocked at driver",
                "deal_reference": None, "deal_id": None}

    if os.environ.get("I_UNDERSTAND_LIVE_RISK", "NO") != "YES":
        log.error("execute.refused_live_fuse", preview_id=preview_id)
        return {"ok": False, "status": "REFUSED_LIVE_FUSE",
                "reason": "I_UNDERSTAND_LIVE_RISK != YES",
                "deal_reference": None, "deal_id": None}

    log.info("execute.start", preview_id=preview_id)
    try:
        raw = await mcp.call(
            "cap_trade_execute_position",
            {"preview_id": preview_id, "confirm": True,
             "wait_for_confirm": wait_for_confirm, "timeout_s": timeout_s},
            timeout_s=timeout_s + 10,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("execute.mcp_error", preview_id=preview_id, error=str(exc)[:200])
        return {"ok": False, "status": "MCP_ERROR",
                "reason": str(exc)[:400],
                "deal_reference": None, "deal_id": None}

    if not isinstance(raw, dict):
        log.error("execute.bad_payload", raw=str(raw)[:200])
        return {"ok": False, "status": "BAD_PAYLOAD",
                "reason": f"expected dict, got {type(raw).__name__}",
                "raw": raw, "deal_reference": None, "deal_id": None}

    deal_reference = raw.get("dealReference")
    confirmation = raw.get("confirmation") or {}
    broker_status = confirmation.get("status") or raw.get("status") or "UNKNOWN"
    deal_id = confirmation.get("dealId") or raw.get("dealId")
    reject_reason = confirmation.get("reason") or confirmation.get("rejectReason")

    ok = broker_status in {"OPEN", "OPENED", "ACCEPTED"}
    log.info("execute.result", ok=ok, deal_reference=deal_reference,
             deal_id=deal_id, broker_status=broker_status,
             reason=reject_reason)

    return {"ok": ok, "status": broker_status, "reason": reject_reason,
            "deal_reference": deal_reference, "deal_id": deal_id,
            "raw": raw}
