"""
Application logging setup: file under configured logs directory + stderr for the service.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logging(logs_dir: Path, app_name: str = "staad-api") -> Optional[Path]:
    """
    Configure root logger with a rotating file handler and a stream handler.

    Idempotent: safe to call more than once (handlers are not duplicated if already present).

    Returns:
        Path to the log file, or ``None`` if file logging could not be started.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{app_name}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    has_file = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == str(log_path)
        for h in root.handlers
    )
    if not has_file:
        try:
            fh = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            fh.setLevel(logging.INFO)
            root.addHandler(fh)
        except OSError:
            log_path = None

    has_stream = any(type(h).__name__ == "StreamHandler" for h in root.handlers)
    if not has_stream:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)

    return log_path
