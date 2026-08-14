"""Async SQLite engine + tiny helpers. One DB file at STATE_DIR/state.db."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base, KillSwitchRow

_engine = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine(state_dir: Path):
    global _engine, _session_maker
    if _engine is None:
        state_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{(state_dir / 'state.db').as_posix()}"
        _engine = create_async_engine(url, echo=False, future=True)
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def init_db(state_dir: Path) -> None:
    """Create tables and seed the kill switch row if missing."""
    engine = get_engine(state_dir)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_scope() as s:
        exists = await s.scalar(select(KillSwitchRow).where(KillSwitchRow.row_id == 1))
        if exists is None:
            s.add(KillSwitchRow(row_id=1, active=False, reason=""))
            await s.commit()


@asynccontextmanager
async def session_scope():
    assert _session_maker is not None, "call get_engine() / init_db() first"
    async with _session_maker() as sess:
        yield sess
