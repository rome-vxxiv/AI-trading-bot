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
load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(prog="capital-agent")
    parser.add_argument("command", nargs="?", default="run",
                        choices=("run", "status", "analyze-once"),
                        help="run = start scheduler; status = one-shot session "
                             "probe; analyze-once = fire the analysis driver "
                             "against --epic ignoring session gating")
    parser.add_argument("--epic", default="GOLD",
                        help="Epic for analyze-once (default: GOLD)")
    parser.add_argument("--strategy", default="readonly_manual",
                        help="Strategy id label recorded in the signals table")
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
        from .driver import run_analysis_once
        from .logging_config import configure as configure_logging
        from .settings import get_settings
        from .state import init_db

        async def _once() -> int:
            settings = get_settings()
            configure_logging(settings.capital_agent_log_dir)
            await init_db(settings.capital_agent_state_dir)
            verdict = await run_analysis_once(epic=args.epic, strategy_id=args.strategy)
            print("\n---- verdict ----")
            print(verdict)
            return 0 if "_error" not in verdict else 2

        return asyncio.run(_once())

    return 1


if __name__ == "__main__":
    sys.exit(main())
