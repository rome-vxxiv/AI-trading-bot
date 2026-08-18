"""Tables create, kill switch seeds, session_scope commits."""

from pathlib import Path

import pytest
from sqlalchemy import select

from capital_agent.state import (
    KillSwitchRow,
    PositionsLocal,
    init_db,
    session_scope,
)
from capital_agent.state.db import get_engine


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path, monkeypatch):
    import capital_agent.state.db as db_mod
    monkeypatch.setattr(db_mod, "_engine", None)
    monkeypatch.setattr(db_mod, "_session_maker", None)
    get_engine(tmp_path)
    yield


async def test_init_creates_kill_switch_row(tmp_path):
    await init_db(tmp_path)
    async with session_scope() as s:
        ks = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
    assert ks is not None
    assert ks.active is False


async def test_positions_local_roundtrip(tmp_path):
    await init_db(tmp_path)
    async with session_scope() as s:
        s.add(PositionsLocal(
            deal_id="D1", epic="BTCUSD", direction="BUY", size=0.01,
            entry=60000.0, stop=59500.0, tp=61000.0,
        ))
        await s.commit()
    async with session_scope() as s:
        rows = (await s.execute(select(PositionsLocal))).scalars().all()
    assert len(rows) == 1
    assert rows[0].epic == "BTCUSD"
