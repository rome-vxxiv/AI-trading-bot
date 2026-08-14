"""SQLAlchemy models. Broker is source of truth — this DB is for scheduling
memory, cooldown timers, kill-switch state, audits, and reporting."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    equity: Mapped[float] = mapped_column(Float)
    hwm: Mapped[float] = mapped_column(Float)
    daily_start_equity: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")


class PositionsLocal(Base):
    """Our shadow of broker positions. Reconciled every 60 s."""
    __tablename__ = "positions_local"
    deal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    epic: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    size: Mapped[float] = mapped_column(Float)
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    epic: Mapped[str] = mapped_column(String(32), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    model_output_json: Mapped[dict] = mapped_column(JSON, default=dict)


class KillSwitchRow(Base):
    """Single-row switch. row_id=1 always. Starts inactive, but any code
    path that isn't sure should assume active until proven otherwise."""
    __tablename__ = "kill_switch"
    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DailyStats(Base):
    __tablename__ = "daily_stats"
    utc_date: Mapped[str] = mapped_column(String(10), primary_key=True)   # YYYY-MM-DD
    pnl_realized: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_unrealized_close: Mapped[float] = mapped_column(Float, default=0.0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)


class JobsAudit(Base):
    __tablename__ = "jobs_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    ts_start: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ts_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
