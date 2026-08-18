"""Tests for step-6 additions: execute() live-fuse guards, drawdown
monitor, outcome tagger P&L parsing."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from capital_agent.execute import execute_preview
from capital_agent.scheduler.jobs.outcome_tagger import _parse_signed


# ---- Fake MCP for injection ------------------------------------


class FakeMCP:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict | None = None,
                   timeout_s: float = 30.0):
        self.calls.append((tool, args or {}))
        if self._raises:
            raise self._raises
        return self._response


@pytest.fixture(autouse=True)
def _clear_env_fuses(monkeypatch):
    monkeypatch.delenv("CAP_DRY_RUN", raising=False)
    monkeypatch.delenv("I_UNDERSTAND_LIVE_RISK", raising=False)
    yield


# ---- execute_preview() live-mode fuses -------------------------


async def test_execute_refuses_when_dry_run(monkeypatch):
    monkeypatch.setenv("CAP_DRY_RUN", "true")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "YES")
    mcp = FakeMCP(response={"dealReference": "DR1",
                            "confirmation": {"status": "OPEN"}})
    r = await execute_preview(mcp, preview_id="pv1")
    assert r["ok"] is False
    assert r["status"] == "REFUSED_DRY_RUN"
    assert mcp.calls == []


async def test_execute_refuses_without_live_fuse(monkeypatch):
    monkeypatch.setenv("CAP_DRY_RUN", "false")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "NO")
    mcp = FakeMCP(response={"dealReference": "DR1"})
    r = await execute_preview(mcp, preview_id="pv1")
    assert r["ok"] is False
    assert r["status"] == "REFUSED_LIVE_FUSE"
    assert mcp.calls == []


async def test_execute_success_returns_deal_id(monkeypatch):
    monkeypatch.setenv("CAP_DRY_RUN", "false")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "YES")
    mcp = FakeMCP(response={"dealReference": "DR1",
                            "confirmation": {"status": "OPEN",
                                             "dealId": "D-42"}})
    r = await execute_preview(mcp, preview_id="pv1")
    assert r["ok"] is True
    assert r["deal_reference"] == "DR1"
    assert r["deal_id"] == "D-42"
    assert mcp.calls[0][0] == "cap_trade_execute_position"
    assert mcp.calls[0][1]["preview_id"] == "pv1"
    assert mcp.calls[0][1]["confirm"] is True


async def test_execute_wraps_mcp_exception(monkeypatch):
    monkeypatch.setenv("CAP_DRY_RUN", "false")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "YES")
    mcp = FakeMCP(raises=RuntimeError("network down"))
    r = await execute_preview(mcp, preview_id="pv1")
    assert r["ok"] is False
    assert r["status"] == "MCP_ERROR"
    assert "network down" in r["reason"]


async def test_execute_reports_broker_rejection(monkeypatch):
    monkeypatch.setenv("CAP_DRY_RUN", "false")
    monkeypatch.setenv("I_UNDERSTAND_LIVE_RISK", "YES")
    mcp = FakeMCP(response={"dealReference": "DR1",
                            "confirmation": {"status": "REJECTED",
                                             "reason": "MARKET_CLOSED"}})
    r = await execute_preview(mcp, preview_id="pv1")
    assert r["ok"] is False
    assert r["status"] == "REJECTED"
    assert r["reason"] == "MARKET_CLOSED"


# ---- outcome tagger P&L parser --------------------------------


def test_parse_signed_plain_number():
    assert _parse_signed(1.23) == 1.23
    assert _parse_signed(-4.5) == -4.5
    assert _parse_signed("2.7") == 2.7


def test_parse_signed_strips_currency_prefix():
    assert _parse_signed("E-1.23") == -1.23
    assert _parse_signed("$5.00") == 5.0
    assert _parse_signed("€-2.5") == -2.5


# ---- drawdown snapshot flow -----------------------------------


@pytest.fixture
def _fresh_db(tmp_path: Path, monkeypatch):
    import capital_agent.state.db as db_mod
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_maker", None)
    from capital_agent.state.db import get_engine
    get_engine(tmp_path)
    yield tmp_path


async def test_drawdown_snapshots_and_computes_hwm(_fresh_db, monkeypatch):
    from capital_agent.scheduler.jobs.drawdown import run_drawdown_check
    from capital_agent.state import EquitySnapshot, init_db
    from capital_agent.state.db import session_scope
    from sqlalchemy import select

    await init_db(_fresh_db)

    # Fake settings + policy to point at the tmp_path
    from capital_agent import settings as st_mod
    monkeypatch.setattr(st_mod, "_settings", None)
    monkeypatch.setenv("CAPITAL_AGENT_STATE_DIR", str(_fresh_db))
    monkeypatch.setenv("CAPITAL_AGENT_LOG_DIR", str(_fresh_db))
    monkeypatch.setenv("CAPITAL_AGENT_CONFIG_DIR", str(_fresh_db))

    from capital_agent.risk import policy as pol
    monkeypatch.setattr(pol, "_policy", None)

    # Balance 1000 -> HWM should be 1000
    mcp1 = FakeMCP(response={"active_account_id": "A1",
                             "accounts": [{"accountId": "A1", "preferred": True,
                                           "balance": {"balance": 1000.0},
                                           "currency": "EUR"}]})
    await run_drawdown_check(mcp1)

    # Later balance 950 -> HWM still 1000, dd = 5%
    mcp2 = FakeMCP(response={"active_account_id": "A1",
                             "accounts": [{"accountId": "A1", "preferred": True,
                                           "balance": {"balance": 950.0},
                                           "currency": "EUR"}]})
    await run_drawdown_check(mcp2)

    async with session_scope() as s:
        snaps = (await s.execute(select(EquitySnapshot))).scalars().all()
    assert len(snaps) == 2
    assert snaps[-1].hwm == 1000.0
    assert snaps[-1].equity == 950.0
