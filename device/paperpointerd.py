#!/usr/bin/env python3
"""paperpointerd — BT mouse/touchpad → multitouch clicks on Paper Pro.

Clicks inject a synthetic finger (uinput). No pen/marker injection:
xochitl has no hover cursor, and writing the real Elan marker breaks input.

Cursor motion is coalesced to the version-gated XOVI QML overlay.  The stock
epaper Qt platform has no mouse/hover implementation.

Implementation is split under ``ppd/`` by responsibility; this module is the
on-device entry point and re-exports the public symbols used by unit tests.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

# Ensure sibling package is importable when run as a script on the tablet.
_DEVICE_DIR = Path(__file__).resolve().parent
if str(_DEVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_DEVICE_DIR))

import time  # re-exported for tests that use pp.time
import errno  # re-exported for tests that use pp.errno

from ppd.constants import *  # noqa: F403
from ppd.constants import fcntl, select  # explicit for tests/tools
from ppd.logutil import log
from ppd.config import (
    default_conf,
    load_conf,
    normalize_cursor_style,
    write_cursor_style_file,
)
from ppd.orientation import (
    CSL_ORIENTATION_DEG,
    XOCHITL_ORIENTATION_DEG,
    UiOrientation,
    logical_to_physical,
    normalize_orientation,
    parse_orientation_mode,
    parse_xochitl_orientation_log,
    physical_to_logical,
    read_xochitl_orientation,
)
from ppd.evdev import (
    KeyboardPresence,
    _ioc_read,
    _uinput_create,
    _uinput_open,
    choose_sources,
    classify_from_caps,
    classify_source,
    device_name,
    eviocgbit,
    eviocgkey,
    eviocgname,
    external_keyboard_present,
    has_bits,
    is_external_keyboard_device,
    is_ignored,
    key_codes_from_bitmap,
    list_sources,
    parse_input_device_blocks,
    pressed_key_codes,
)
from ppd.cursor import CursorPublisher, call_ui_broker, format_cursor_message
from ppd.touch import (
    ContactState,
    InputFrame,
    TouchClick,
    _abs_range,
    _ev,
    _sync,
    clamp_move,
    map_abs_axis,
    map_abs_position,
    moved_past_drag_threshold,
    open_sources,
)
from ppd.loop import run_loop


def cmd_list() -> int:
    for path, kind, name in list_sources():
        print(f"{path}\t{kind}\t{name}")
    return 0


def cmd_tap(x: int | None = None, y: int | None = None) -> int:
    cfg = load_conf()
    touch = TouchClick(int(cfg["touch_x_max"]), int(cfg["touch_y_max"]))
    try:
        tx = cfg["touch_x_max"] // 2 if x is None else x
        ty = cfg["touch_y_max"] // 2 if y is None else y
        log(f"test-tap {tx},{ty}")
        touch.tap(tx, ty)
        print(f"tapped {tx},{ty}")
        return 0
    finally:
        touch.close()


def cmd_cursor_ping() -> int:
    response = call_ui_broker("paperpointer.ping")
    if response != CURSOR_READY:
        if response:
            print(f"unexpected cursor response: {response}", file=sys.stderr)
        else:
            print("cursor overlay did not respond", file=sys.stderr)
        return 1
    print(response)
    return 0


def _request_stop(_signum, _frame) -> None:
    """Turn systemd's SIGTERM into the same orderly unwind as Ctrl-C."""
    raise KeyboardInterrupt


def main(argv: list[str]) -> int:
    os.makedirs(HOME, exist_ok=True)
    if len(argv) > 1 and argv[1] == "list":
        return cmd_list()
    if len(argv) > 1 and argv[1] == "tap":
        x = int(argv[2]) if len(argv) > 2 else None
        y = int(argv[3]) if len(argv) > 3 else None
        return cmd_tap(x, y)
    if len(argv) > 1 and argv[1] == "cursor-ping":
        return cmd_cursor_ping()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    if not any(os.path.exists(p) for p in UINPUT_PATHS):
        os.system("modprobe uinput 2>/dev/null")
    cfg = load_conf()
    log("paperpointerd start (touch-click only, no marker inject)")
    log(f"conf={cfg}")
    try:
        run_loop(cfg)
    except KeyboardInterrupt:
        log("interrupt")
    except Exception as e:
        log(f"fatal: {e!r}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
