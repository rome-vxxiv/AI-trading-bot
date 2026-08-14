"""Our-side settings. Distinct from the upstream CAP_* env vars.

CAP_* is read by the MCP subprocess. We reach it through env-inheritance,
never through this module. This keeps two clean layers:
  - Broker credentials live in CAP_*, only the MCP subprocess sees them.
  - Our own behavior (paths, ports, guards) lives in AGENT_*.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Runtime configuration for the capital_agent process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Live-trading fuse (belt over the MCP's own gate) -----------
    i_understand_live_risk: str = Field(default="NO")

    # ---- Alerts ------------------------------------------------------
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # ---- Health API --------------------------------------------------
    health_api_token: str = Field(default="")
    health_api_bind: str = Field(default="127.0.0.1:8080")

    # ---- Paths -------------------------------------------------------
    capital_agent_state_dir: Path = Field(default=Path("./state"))
    capital_agent_log_dir: Path = Field(default=Path("./logs"))
    capital_agent_config_dir: Path = Field(default=Path("./config"))

    # ---- Cadence (safe defaults) -------------------------------------
    keepalive_interval_seconds: int = Field(default=300)
    reconciliation_interval_seconds: int = Field(default=60)

    @property
    def health_bind_host(self) -> str:
        return self.health_api_bind.rsplit(":", 1)[0]

    @property
    def health_bind_port(self) -> int:
        return int(self.health_api_bind.rsplit(":", 1)[1])

    def ensure_dirs(self) -> None:
        for p in (self.capital_agent_state_dir, self.capital_agent_log_dir):
            p.mkdir(parents=True, exist_ok=True)


_settings: AgentSettings | None = None


def get_settings() -> AgentSettings:
    global _settings
    if _settings is None:
        _settings = AgentSettings()
        _settings.ensure_dirs()
    return _settings
