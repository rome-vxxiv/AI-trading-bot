"""Entrypoint. `python -m capital_agent` starts the scheduler in the
foreground. Ctrl+C shuts down cleanly."""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from .scheduler import run

# Populate os.environ from .env so the MCP subprocess inherits CAP_* creds.
# pydantic-settings feeds our own layer separately; this line is the bridge
# that makes the upstream server (a separate process) see the credentials.
load_dotenv(override=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="capital-agent")
    parser.add_argument("command", nargs="?", default="run",
                        choices=("run", "status", "analyze-once",
                                 "strategy-once", "backtest"),
                        help="run | status | analyze-once (read-only) | "
                             "strategy-once (preview flow, needs CAP_DRY_RUN=true) | "
                             "backtest (replay strategy on historical bars)")
    parser.add_argument("--epic", default="GOLD",
                        help="Epic for analyze/strategy/backtest (default: GOLD)")
    parser.add_argument("--strategy", default="rsi_mean_reversion",
                        help="Playbook id (see driver.PLAYBOOKS)")
    parser.add_argument("--resolution", default="MINUTE_15",
                        help="Backtest bar resolution")
    parser.add_argument("--max-bars", type=int, default=400,
                        help="Backtest bar count")
    parser.add_argument("--from-iso", default=None)
    parser.add_argument("--to-iso", default=None)
    args = parser.parse_args()

    if args.command == "run":
        try:
            return asyncio.run(run())
        except KeyboardInterrupt:
            return 130

    if args.command == "status":
        from .status_probe import status_once
        return asyncio.run(status_once())

    if args.command == "analyze-once":
        from .driver import run_playbook_once
        from .logging_config import configure as configure_logging
        from .settings import get_settings
        from .state import init_db

        async def _once() -> int:
            settings = get_settings()
            configure_logging(settings.capital_agent_log_dir)
            await init_db(settings.capital_agent_state_dir)
            verdict = await run_playbook_once("readonly_analysis", args.epic)
            print("\n---- verdict ----")
            print(verdict)
            return 0 if "_error" not in verdict else 2

        return asyncio.run(_once())

    if args.command == "strategy-once":
        from .driver import run_playbook_once
        from .logging_config import configure as configure_logging
        from .settings import get_settings
        from .state import init_db

        async def _strategy() -> int:
            settings = get_settings()
            configure_logging(settings.capital_agent_log_dir)
            await init_db(settings.capital_agent_state_dir)
            verdict = await run_playbook_once(args.strategy, args.epic)
            print("\n---- verdict ----")
            print(verdict)
            return 0 if "_error" not in verdict else 2

        return asyncio.run(_strategy())

    if args.command == "backtest":
        from .backtest.runner import run_backtest
        from .logging_config import configure as configure_logging
        from .settings import get_settings

        async def _bt() -> int:
            configure_logging(get_settings().capital_agent_log_dir)
            await run_backtest(epic=args.epic, resolution=args.resolution,
                               max_bars=args.max_bars,
                               from_iso=args.from_iso, to_iso=args.to_iso)
            return 0

        return asyncio.run(_bt())

    return 1


if __name__ == "__main__":
    sys.exit(main())
