"""Adversarial risk-layer tests. Every check must reject bad inputs even
when the caller lies or misconfigures."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from capital_agent.risk.checks import postflight, preflight
from capital_agent.risk.cooldowns import cooldown_status
from capital_agent.risk.killswitch import ensure_killswitch_row, is_active, set_active
from capital_agent.risk.policy import RiskPolicy, get_policy, reset
from capital_agent.risk.sizing import compute_size
from capital_agent.state import PositionsLocal, init_db
from capital_agent.state.db import get_engine, session_scope


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path, monkeypatch):
    import capital_agent.state.db as db_mod
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_maker", None)
    get_engine(tmp_path)
    yield


def _default_policy(**over) -> RiskPolicy:
    p = RiskPolicy()
    for k, v in over.items():
        setattr(p, k, v)
    return p


# ---- Sizing ----------------------------------------------------


def test_sizing_ok():
    r = compute_size(equity=1000, risk_pct=0.0025, stop_distance=100,
                     min_size=0.01, step_size=0.01, max_size=1.0)
    assert r.ok and r.size >= 0.01


def test_sizing_rejects_zero_stop():
    r = compute_size(equity=1000, risk_pct=0.0025, stop_distance=0,
                     min_size=0.01, step_size=0.01)
    assert not r.ok and "stop_distance" in r.reason


def test_sizing_rejects_zero_equity():
    r = compute_size(equity=0, risk_pct=0.0025, stop_distance=100,
                     min_size=0.01, step_size=0.01)
    assert not r.ok and "equity" in r.reason


def test_sizing_rejects_absurd_risk_pct():
    r = compute_size(equity=1000, risk_pct=0.5, stop_distance=100,
                     min_size=0.01, step_size=0.01)
    assert not r.ok and "risk_pct" in r.reason


def test_sizing_caps_at_max():
    # equity so big that raw_size would exceed max
    r = compute_size(equity=1_000_000, risk_pct=0.01, stop_distance=1,
                     min_size=0.01, step_size=0.01, max_size=5.0)
    assert r.ok and r.size == 5.0 and "capped" in r.reason


def test_sizing_none_below_min():
    r = compute_size(equity=100, risk_pct=0.0001, stop_distance=1000,
                     min_size=0.01, step_size=0.01)
    assert not r.ok and "below_min" in r.reason


# ---- Kill switch -----------------------------------------------


async def test_killswitch_defaults_inactive(tmp_path):
    await init_db(tmp_path)
    active, reason = await is_active()
    assert active is False
    assert reason == ""


async def test_killswitch_set_then_read(tmp_path):
    await init_db(tmp_path)
    await ensure_killswitch_row()
    await set_active(True, "manual")
    active, reason = await is_active()
    assert active is True
    assert reason == "manual"
    await set_active(False, "")
    active, _ = await is_active()
    assert active is False


# ---- Preflight orchestration -----------------------------------


async def test_preflight_ok_when_all_green(tmp_path):
    await init_db(tmp_path)
    policy = _default_policy(allowlist=["BTCUSD"])
    r = await preflight(epic="BTCUSD", strategy_id="rsi", policy=policy)
    assert r.ok, r.reasons


async def test_preflight_rejects_when_kill_switch_active(tmp_path):
    await init_db(tmp_path)
    await set_active(True, "test")
    policy = _default_policy(allowlist=["BTCUSD"])
    r = await preflight(epic="BTCUSD", strategy_id="rsi", policy=policy)
    assert not r.ok
    assert any("kill_switch" in x for x in r.reasons)


async def test_preflight_rejects_epic_off_allowlist(tmp_path):
    await init_db(tmp_path)
    policy = _default_policy(allowlist=["GOLD"])
    r = await preflight(epic="BTCUSD", strategy_id="rsi", policy=policy)
    assert not r.ok
    assert any("allowlist" in x for x in r.reasons)


async def test_preflight_rejects_max_positions_reached(tmp_path):
    await init_db(tmp_path)
    async with session_scope() as s:
        s.add(PositionsLocal(deal_id="D1", epic="BTCUSD", direction="BUY",
                             size=0.01, entry=60000, stop=59000, tp=None))
        await s.commit()
    policy = _default_policy(allowlist=["BTCUSD"],
                             max_positions_total=1,
                             max_positions_per_instrument=1)
    r = await preflight(epic="BTCUSD", strategy_id="rsi", policy=policy)
    assert not r.ok
    assert any("max" in x for x in r.reasons)


# ---- Postflight ------------------------------------------------


def test_postflight_ok_on_hold():
    r = postflight({"decision": "hold"}, _default_policy(), atr_used=1.0)
    assert r.ok


def test_postflight_rejects_missing_preview_id():
    r = postflight({"decision": "enter_long", "preview_id": None},
                   _default_policy(), atr_used=1.0)
    assert not r.ok
    assert any("preview_id" in x for x in r.reasons)


def test_postflight_rejects_stop_too_wide():
    p = _default_policy(stop_max_atr_multiples=5.0)
    v = {"decision": "enter_long", "preview_id": "abc",
         "stop_distance_used": 12.0}
    r = postflight(v, p, atr_used=1.0)   # ratio = 12
    assert not r.ok
    assert any("stop_distance" in x for x in r.reasons)


def test_postflight_rejects_broker_check_failed():
    v = {"decision": "enter_long", "preview_id": "abc",
         "preview_all_checks_passed": False}
    r = postflight(v, _default_policy(), atr_used=1.0)
    assert not r.ok
    assert any("broker_preview_checks_failed" in x for x in r.reasons)


# ---- Policy loader --------------------------------------------


def test_policy_loader_uses_conservative_defaults_when_yaml_missing(tmp_path):
    reset()
    p = get_policy(tmp_path)
    assert p.dry_run is True
    assert p.risk_pct_per_trade == 0.0025
    assert p.max_positions_total == 1


def test_policy_loader_reads_yaml(tmp_path):
    (tmp_path / "risk.yaml").write_text(yaml.safe_dump({
        "dry_run": False,
        "risk_pct_per_trade": 0.005,
        "max_positions_total": 3,
        "stop": {"required": True, "max_atr_multiples": 4.0},
    }), encoding="utf-8")
    reset()
    p = get_policy(tmp_path)
    assert p.dry_run is False
    assert p.risk_pct_per_trade == 0.005
    assert p.max_positions_total == 3
    assert p.stop_max_atr_multiples == 4.0
