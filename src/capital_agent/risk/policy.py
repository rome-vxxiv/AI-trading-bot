"""Our-driver risk policy loaded from config/risk.yaml. Values here CAN
ONLY be equal to or stricter than the upstream MCP's built-in caps
(CAP_MAX_POSITION_SIZE, CAP_MAX_OPEN_POSITIONS, CAP_MAX_ORDERS_PER_DAY,
CAP_DRY_RUN). If the yaml relaxes something below the MCP cap, that's
harmless — the MCP still enforces its own cap — but it's a warning
worth flagging so operators know.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass
class RiskPolicy:
    dry_run: bool = True
    risk_pct_per_trade: float = 0.0025      # 0.25%
    max_positions_total: int = 1
    max_positions_per_instrument: int = 1
    max_daily_loss_pct: float = 0.01        # 1%
    max_drawdown_pct: float = 0.05          # 5% -> kill switch
    stop_required: bool = True
    stop_max_atr_multiples: float = 5.0
    stop_atr_period: int = 14
    cooldown_after_any_trade_minutes: int = 15
    cooldown_after_losing_trade_minutes: int = 60
    spread_max_multiple_of_median_1h: float = 2.0
    allowlist: list[str] = field(default_factory=list)


_policy: RiskPolicy | None = None


def get_policy(config_dir: Path | None = None) -> RiskPolicy:
    """Lazily load config/risk.yaml + config/allowlist.yaml. Reset with
    get_policy.cache_clear() equivalent — pass a fresh config_dir to
    force reload (only used by tests)."""
    global _policy
    if _policy is not None and config_dir is None:
        return _policy
    cd = config_dir or Path("./config")
    risk_path = cd / "risk.yaml"
    allow_path = cd / "allowlist.yaml"

    raw: dict = {}
    if risk_path.exists():
        raw = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    else:
        log.warning("risk.yaml_missing", path=str(risk_path),
                    action="using conservative defaults")

    stop_block = raw.get("stop") or {}
    cd_block = raw.get("cooldowns") or {}
    spread_block = raw.get("spread") or {}

    p = RiskPolicy(
        dry_run=bool(raw.get("dry_run", True)),
        risk_pct_per_trade=float(raw.get("risk_pct_per_trade", 0.0025)),
        max_positions_total=int(raw.get("max_positions_total", 1)),
        max_positions_per_instrument=int(raw.get("max_positions_per_instrument", 1)),
        max_daily_loss_pct=float(raw.get("max_daily_loss_pct", 0.01)),
        max_drawdown_pct=float(raw.get("max_drawdown_pct", 0.05)),
        stop_required=bool(stop_block.get("required", True)),
        stop_max_atr_multiples=float(stop_block.get("max_atr_multiples", 5.0)),
        stop_atr_period=int(stop_block.get("atr_period", 14)),
        cooldown_after_any_trade_minutes=int(cd_block.get("after_any_trade_minutes", 15)),
        cooldown_after_losing_trade_minutes=int(cd_block.get("after_losing_trade_minutes", 60)),
        spread_max_multiple_of_median_1h=float(spread_block.get("max_multiple_of_median_1h", 2.0)),
    )

    if allow_path.exists():
        allow_raw = yaml.safe_load(allow_path.read_text(encoding="utf-8")) or {}
        p.allowlist = [str(e).strip().upper() for e in (allow_raw.get("epics") or [])]

    _policy = p
    return p


def reset() -> None:
    """Test-only: clear the singleton so tests can load with different config_dir."""
    global _policy
    _policy = None
