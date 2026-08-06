"""Console logging helpers for PaperHid (GUI + CLI)."""
from __future__ import annotations

import logging
import sys


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# Shared package logger; modules use logging.getLogger(__name__).
ROOT_NAME = "paperhid"


def get_logger(name: str | None = None) -> logging.Logger:
    if not name or name == ROOT_NAME:
        return logging.getLogger(ROOT_NAME)
    if name.startswith(ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def setup_logging(verbose: bool = True, debug: bool = False) -> logging.Logger:
    """Configure console logging for the terminal that launched cli.py.

    Idempotent: safe to call more than once.
    """
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(level)

    # Avoid duplicate handlers if re-entered
    if not any(getattr(h, "_paperhid_console", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        handler._paperhid_console = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    else:
        for h in root.handlers:
            if getattr(h, "_paperhid_console", False):
                h.setLevel(level)

    # Keep noisy third-party libs quieter unless full debug
    if not debug:
        logging.getLogger("paramiko").setLevel(logging.WARNING)
    else:
        logging.getLogger("paramiko").setLevel(logging.DEBUG)

    root.debug("logging ready (level=%s)", logging.getLevelName(level))
    return root
