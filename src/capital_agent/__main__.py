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
                                 "strategy-once", "backtest",
                                 "go-live", "go-demo",
                                 "kill", "unlock", "jobs"),
                        help="run | status | analyze-once | strategy-once | "
                             "backtest | go-live | go-demo | kill | unlock | jobs")
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
    parser.add_argument("--confirm", action="store_true",
                        help="Skip interactive prompt on go-live (scripted use only)")
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
                               from_iso=args.from_iso, to_iso=args.to_iso,
                               strategy=args.strategy)
            return 0

        return asyncio.run(_bt())

    if args.command in ("go-live", "go-demo"):
        return _flip_env(target=args.command, skip_prompt=args.confirm)

    if args.command in ("kill", "unlock", "jobs"):
        return _health_cli(args.command)

    return 1


def _health_cli(cmd: str) -> int:
    """kill/unlock/jobs subcommands hit the health API on 127.0.0.1:8080."""
    import json
    import os as _os
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    bind = _os.environ.get("HEALTH_API_BIND", "127.0.0.1:8080")
    token = _os.environ.get("HEALTH_API_TOKEN", "")
    if cmd == "jobs":
        try:
            with urlopen(f"http://{bind}/jobs", timeout=5) as r:
                print(r.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as e:
            print(f"[X] cannot reach scheduler at http://{bind}: {e}")
            return 2
        return 0

    if cmd in ("kill", "unlock"):
        if not token:
            print("[X] HEALTH_API_TOKEN not set — required for kill/unlock")
            return 2
        url = f"http://{bind}/{cmd}"
        if cmd == "kill":
            url += "?reason=cli"
        req = Request(url, method="POST",
                      headers={"X-Auth-Token": token})
        try:
            with urlopen(req, timeout=5) as r:
                print(r.read().decode("utf-8"))
        except HTTPError as e:
            print(f"[X] HTTP {e.code}: {e.read().decode('utf-8')}")
            return 2
        except (URLError, TimeoutError) as e:
            print(f"[X] cannot reach scheduler at http://{bind}: {e}")
            return 2
        return 0

    _ = json  # keep import used across branches
    return 2


def _flip_env(*, target: str, skip_prompt: bool) -> int:
    """Rewrite .env to set CAP_DRY_RUN + I_UNDERSTAND_LIVE_RISK atomically."""
    from pathlib import Path
    env_path = Path(".env")
    if not env_path.exists():
        print("ERROR: .env not found in cwd")
        return 1
    lines = env_path.read_text(encoding="utf-8").splitlines()

    if target == "go-live":
        print("\n" + "=" * 60)
        print("  GO-LIVE CEREMONY")
        print("=" * 60)
        print("  This will flip CAP_DRY_RUN=false and")
        print("  I_UNDERSTAND_LIVE_RISK=YES in your .env.")
        print("")
        print("  Even on your DEMO account, this means real preview -> ")
        print("  execute calls will hit Capital.com and open real")
        print("  positions on your demo balance.")
        print("")
        print("  Sanity checks before continuing:")
        print("  - CAP_ENV should be 'demo' unless you REALLY know")
        print("  - Your kill switch must be UNLOCKED")
        print("  - Your risk.yaml limits should be tight")
        print("=" * 60)
        if not skip_prompt:
            reply = input('Type "GO LIVE" exactly (or anything else to abort): ')
            if reply.strip() != "GO LIVE":
                print("Aborted. No changes made.")
                return 1
        new_lines = []
        set_dry = False; set_fuse = False
        for line in lines:
            if line.startswith("CAP_DRY_RUN="):
                new_lines.append("CAP_DRY_RUN=false"); set_dry = True
            elif line.startswith("I_UNDERSTAND_LIVE_RISK="):
                new_lines.append("I_UNDERSTAND_LIVE_RISK=YES"); set_fuse = True
            else:
                new_lines.append(line)
        if not set_dry: new_lines.append("CAP_DRY_RUN=false")
        if not set_fuse: new_lines.append("I_UNDERSTAND_LIVE_RISK=YES")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print("\n.env flipped to LIVE. Restart the scheduler to pick up the change.")
        return 0

    # go-demo
    new_lines = []
    set_dry = False; set_fuse = False
    for line in lines:
        if line.startswith("CAP_DRY_RUN="):
            new_lines.append("CAP_DRY_RUN=true"); set_dry = True
        elif line.startswith("I_UNDERSTAND_LIVE_RISK="):
            new_lines.append("I_UNDERSTAND_LIVE_RISK=NO"); set_fuse = True
        else:
            new_lines.append(line)
    if not set_dry: new_lines.append("CAP_DRY_RUN=true")
    if not set_fuse: new_lines.append("I_UNDERSTAND_LIVE_RISK=NO")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(".env flipped to DEMO (dry-run). Restart the scheduler.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
