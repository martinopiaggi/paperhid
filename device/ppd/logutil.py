"""Pointer daemon file logger."""
from __future__ import annotations

import os
import time

from .constants import HOME, LOG_PATH


def log(msg: str) -> None:
    try:
        os.makedirs(HOME, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 65536:
            with open(LOG_PATH, "rb") as f:
                tail = f.read()[-16384:]
            with open(LOG_PATH, "wb") as f:
                f.write(tail)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass
