"""Step-1 verification: spawn upstream capital-mcp over stdio, list tools,
call cap_session_status. Fails loud on any protocol error. Prints machine-
readable summary at the end so this script is usable as a health probe.

Reads .env in project root for the CAP_* credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def load_env() -> dict[str, str]:
    """Minimal .env loader — pydantic-settings only runs inside the server."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        sys.exit(f"missing {env_path} — populate CAP_* credentials first")
    out: dict[str, str] = {}
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    for required in ("CAP_API_KEY", "CAP_IDENTIFIER", "CAP_API_PASSWORD"):
        if not out.get(required):
            sys.exit(f".env missing {required}")
    return out


EXPECTED_TOOL_COUNT = 38
CORE_TOOLS = {
    "cap_session_status",
    "cap_session_login",
    "cap_session_ping",
    "cap_market_get",
    "cap_market_prices",
    "cap_market_sentiment",
    "cap_trade_preview_position",
    "cap_trade_execute_position",
    "cap_trade_confirm_wait",
    "cap_account_list",
}


async def _safe_call(session: ClientSession, name: str, args: dict) -> object:
    try:
        result = await session.call_tool(name, args)
        return _first_text_payload(result.content)
    except Exception as exc:  # noqa: BLE001
        return {"_error": type(exc).__name__, "_message": str(exc)[:400]}


async def main() -> int:
    env = {**os.environ, **load_env()}
    env.setdefault("CAP_LOG_LEVEL", "WARNING")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "capital_mcp"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            server_name = init.serverInfo.name
            server_version = init.serverInfo.version

            tools_result = await session.list_tools()
            tool_names = sorted(t.name for t in tools_result.tools)

            before = await _safe_call(session, "cap_session_status", {})
            login = await _safe_call(session, "cap_session_login", {})
            after = await _safe_call(session, "cap_session_status", {})
            accounts = await _safe_call(session, "cap_account_list", {})

    checks = {
        "tool_count_matches": len(tool_names) == EXPECTED_TOOL_COUNT,
        "core_tools_present": CORE_TOOLS.issubset(set(tool_names)),
        "session_status_reads_locally": isinstance(before, dict)
            and before.get("env") == "demo",
        "login_reached_broker": isinstance(login, dict) and "_error" not in login,
        "logged_in_after_login": isinstance(after, dict) and bool(after.get("logged_in")),
        "accounts_returned": isinstance(accounts, dict)
            and isinstance(accounts.get("accounts"), list)
            and len(accounts["accounts"]) > 0,
    }
    all_ok = all(checks.values())

    summary = {
        "server": {"name": server_name, "version": server_version},
        "tool_count": len(tool_names),
        "tools": tool_names,
        "checks": checks,
        "cap_session_status_before_login": before,
        "cap_session_login": _redact_secrets(login),
        "cap_session_status_after_login": after,
        "cap_account_list": _redact_accounts(accounts),
        "verdict": "PASS" if all_ok else "PARTIAL",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all_ok else 2


_SECRET_KEYS = {"cst", "x_security_token", "x-security-token", "securityToken", "clientId"}


def _redact(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in _SECRET_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _redact_secrets(login_payload: object) -> object:
    return _redact(login_payload)


def _redact_accounts(accounts_payload: object) -> object:
    """Keep balance/currency/status but drop account IDs."""
    if not isinstance(accounts_payload, dict):
        return _redact(accounts_payload)
    out = dict(accounts_payload)
    if "accounts" in out and isinstance(out["accounts"], list):
        out["accounts"] = [
            {
                "accountName": a.get("accountName"),
                "accountType": a.get("accountType"),
                "preferred": a.get("preferred"),
                "status": a.get("status"),
                "currency": a.get("currency"),
                "balance": a.get("balance"),
            }
            for a in out["accounts"]
            if isinstance(a, dict)
        ]
    if "active_account_id" in out:
        out["active_account_id"] = "<redacted>"
    return out


def _first_text_payload(content_blocks: list) -> object:
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
