"""Central logging setup.

Every entry point (the scheduled daily run, ad-hoc CLI commands, and the future
web server) calls `setup_logging()` so they all append to one persistent,
rotating file at `logs/jobhunter.log` instead of scattering to stderr only —
giving a durable audit trail of scrapes, digests, backups, and status changes.

The console handler keeps the original `LEVEL name: message` format (so the
daily run's `run_daily.bat` redirect looks unchanged); the file handler prefixes
a timestamp, which is what makes a persistent log worth reading later.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).with_name("logs")
_LOG_FILE = _LOG_DIR / "jobhunter.log"
_configured = False


def setup_logging(level: int = logging.INFO) -> Path:
    """Configure the root logger once (idempotent) with a console handler and a
    rotating file handler (5 × 1 MB). Returns the log file path. Safe to call
    from any entry point, including repeatedly."""
    global _configured
    if _configured:
        return _LOG_FILE
    _LOG_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    fileh = RotatingFileHandler(
        _LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fileh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fileh)

    _configured = True
    return _LOG_FILE
