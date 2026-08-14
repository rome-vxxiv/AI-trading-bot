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
                        choices=("run", "status"),
                        help="run = start scheduler; status = one-shot status probe")
    args = parser.parse_args()

    if args.command == "run":
        try:
            return asyncio.run(run())
        except KeyboardInterrupt:
            return 130

    if args.command == "status":
        from .status_probe import status_once
        return asyncio.run(status_once())

    return 1


if __name__ == "__main__":
    sys.exit(main())
