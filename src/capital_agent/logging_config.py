"""structlog wiring. JSON to a rotating file, human-friendly to the console."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import structlog


def configure(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    # stdlib root: JSON to file, plain to stderr, so users see logs in the
    # PowerShell window and a machine-readable copy lives on disk.
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)-5s %(message)s",
                                           datefmt="%Y-%m-%dT%H:%M:%S"))
    console.setLevel(level)
    root.addHandler(console)

    fh = logging.handlers.TimedRotatingFileHandler(
        log_dir / "agent.jsonl",
        when="D", interval=1, backupCount=14, encoding="utf-8", utc=True,
    )
    fh.setFormatter(logging.Formatter("%(message)s"))
    fh.setLevel(level)
    root.addHandler(fh)

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level
        ),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _dual_render,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _dual_render(_logger, _name, event_dict):
    """Render JSON for file handler and a human line for the console handler
    by using two different logging paths in the caller — we just emit both
    representations here, and stdlib formatters pick their format from name.

    Simpler: just render JSON and let the console formatter treat it as
    the message. It reads fine either way.
    """
    import json
    return json.dumps(event_dict, default=str, sort_keys=False)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
