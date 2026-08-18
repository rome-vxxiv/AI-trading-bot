"""Real trade-outcome reporting. Unlike backtest.simulate (a historical
replay), this reads what ACTUALLY happened: Signal rows that
outcome_tagger.py tagged with a realized win/loss/flat + P&L when the
position genuinely closed at the broker. This is the honest answer to
"is this working", built from lived results rather than simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select

from .state import Signal
from .state.db import session_scope

_ENTRY_DECISIONS = ("enter_long", "enter_short")
_OUTCOMES = ("win", "loss", "flat")


@dataclass(frozen=True)
class PerformanceReport:
    trade_count: int           # tagged (closed) trades only
    wins: int
    losses: int
    flats: int
    win_rate: float | None
    total_pnl: float
    avg_win: float | None
    avg_loss: float | None
    profit_factor: float | None
    by_epic: dict[str, dict[str, Any]]
    by_strategy: dict[str, dict[str, Any]]
    recent: list[dict[str, Any]]


async def build_performance_report(
    *, epic: str | None = None, strategy_id: str | None = None, limit: int = 5000,
) -> PerformanceReport:
    async with session_scope() as s:
        q = (select(Signal)
             .where(Signal.decision.in_(_ENTRY_DECISIONS))
             .order_by(desc(Signal.ts)))
        if epic:
            q = q.where(Signal.epic == epic)
        if strategy_id:
            q = q.where(Signal.strategy_id == strategy_id)
        rows = (await s.execute(q.limit(limit))).scalars().all()

    tagged: list[dict[str, Any]] = []
    for r in rows:
        payload = r.model_output_json if isinstance(r.model_output_json, dict) else {}
        outcome = payload.get("outcome")
        if outcome not in _OUTCOMES:
            continue  # still open, or never got a broker fill
        exec_block = payload.get("_execute") or {}
        pnl = exec_block.get("pnl_realized")
        tagged.append({
            "ts": r.ts.isoformat() if r.ts else None,
            "epic": r.epic,
            "strategy_id": r.strategy_id,
            "decision": r.decision,
            "outcome": outcome,
            "pnl": float(pnl) if isinstance(pnl, int | float) else None,
        })

    return _summarize(tagged)


def _summarize(tagged: list[dict[str, Any]]) -> PerformanceReport:
    priced = [t for t in tagged if t["pnl"] is not None]
    wins = [t for t in priced if t["pnl"] > 0]
    losses = [t for t in priced if t["pnl"] < 0]

    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    trade_count = len(tagged)

    return PerformanceReport(
        trade_count=trade_count,
        wins=sum(1 for t in tagged if t["outcome"] == "win"),
        losses=sum(1 for t in tagged if t["outcome"] == "loss"),
        flats=sum(1 for t in tagged if t["outcome"] == "flat"),
        win_rate=(sum(1 for t in tagged if t["outcome"] == "win") / trade_count) if trade_count else None,
        total_pnl=sum(t["pnl"] for t in priced),
        avg_win=(gross_win / len(wins)) if wins else None,
        avg_loss=(-gross_loss / len(losses)) if losses else None,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        by_epic=_group_by(tagged, "epic"),
        by_strategy=_group_by(tagged, "strategy_id"),
        # tagged is newest-first; show the recent slice oldest-to-newest.
        recent=list(reversed(tagged[:20])),
    )


def _group_by(tagged: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in tagged:
        groups.setdefault(t.get(key) or "unknown", []).append(t)

    out: dict[str, dict[str, Any]] = {}
    for k, items in groups.items():
        priced = [t for t in items if t["pnl"] is not None]
        wins = sum(1 for t in items if t["outcome"] == "win")
        out[k] = {
            "trade_count": len(items),
            "wins": wins,
            "win_rate": round(wins / len(items), 3) if items else None,
            "total_pnl": round(sum(t["pnl"] for t in priced), 2) if priced else 0.0,
        }
    return out
