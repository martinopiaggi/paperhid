"""Configuration load/defaults and cursor style status file."""
from __future__ import annotations

import math
import os

from . import constants as C
from .constants import (
    CONF_PATH,
    CURSOR_HIDE_MS_MAX,
    CURSOR_HZ_MAX,
    CURSOR_LAG_MS_MAX,
    CURSOR_STYLE_DEFAULT,
    CURSOR_STYLES,
    DEFAULT_CONF,
)
from .logutil import log
from .orientation import parse_orientation_mode


def default_conf() -> dict:
    """Return a fresh copy of shipped defaults (cursor off; Elan touch ranges)."""
    return dict(DEFAULT_CONF)


def load_conf(path: str | None = None) -> dict:
    """Load pointer.conf, retaining safe defaults for invalid values."""
    cfg = default_conf()
    conf_path = CONF_PATH if path is None else path
    if not os.path.isfile(conf_path):
        return cfg
    try:
        with open(conf_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k not in cfg:
                    continue
                try:
                    if k in ("source", "cursor_style", "orientation"):
                        parsed = v
                    elif isinstance(cfg[k], float):
                        parsed = float(v)
                    else:
                        parsed = int(v)
                except (ValueError, OverflowError):
                    log(f"invalid config {k}={v!r}; using {cfg[k]!r}")
                    continue

                valid = True
                if k == "accel":
                    valid = math.isfinite(parsed) and 0.1 <= parsed <= 20.0
                elif k in ("touch_x_max", "touch_y_max"):
                    valid = 1 <= parsed <= 65535
                elif k in ("display_w", "display_h"):
                    valid = 1 <= parsed <= 16384
                elif k == "cursor_hz":
                    valid = 1 <= parsed <= CURSOR_HZ_MAX
                elif k == "cursor_lag_ms":
                    valid = 0 <= parsed <= CURSOR_LAG_MS_MAX
                elif k == "cursor_hide_ms":
                    valid = 0 <= parsed <= CURSOR_HIDE_MS_MAX
                elif k == "cursor_style":
                    valid = str(parsed).strip().lower() in CURSOR_STYLES
                    if valid:
                        parsed = str(parsed).strip().lower()
                elif k == "orientation":
                    parsed = parse_orientation_mode(parsed)
                    valid = parsed is not None
                elif k in (
                    "invert_x",
                    "invert_y",
                    "swap_xy",
                    "scroll_as_swipe",
                    "finger_drag",
                    "cursor",
                ):
                    valid = parsed in (0, 1)
                elif k == "source":
                    valid = not parsed or parsed.startswith("/dev/input/event")

                if valid:
                    cfg[k] = parsed
                else:
                    log(f"unsafe config {k}={v!r}; using {cfg[k]!r}")
    except OSError as e:
        log(f"conf read failed: {e}")
    return cfg


def normalize_cursor_style(style: str | None) -> str:
    """Return an allow-listed cursor skin name."""
    name = (style or "").strip().lower()
    if name in CURSOR_STYLES:
        return name
    return CURSOR_STYLE_DEFAULT


def write_cursor_style_file(style: str | None) -> str:
    """Publish the active skin for the QML overlay (one-line status file)."""
    name = normalize_cursor_style(style)
    try:
        # Read live attributes so tests can rebind HOME / CURSOR_STYLE_PATH.
        os.makedirs(C.HOME, exist_ok=True)
        path = C.CURSOR_STYLE_PATH
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(name + "\n")
        os.replace(tmp, path)
    except OSError as e:
        log(f"cursor style file write failed: {e}")
    return name

