#!/usr/bin/env python3
"""Shared paths, HID constants, and default pointer configuration."""
from __future__ import annotations

import struct

try:
    import fcntl
except ImportError:  # Windows / non-Linux hosts (unit tests)
    fcntl = None  # type: ignore[assignment]

try:
    import select
except ImportError:
    select = None  # type: ignore[assignment]

HOME = "/home/root/.paperpointer"
LOG_PATH = f"{HOME}/pointer.log"
CONF_PATH = f"{HOME}/pointer.conf"
CURSOR_BROKER_IN = "/run/xovi-mb"
CURSOR_BROKER_OUT = "/run/xovi-mb-out"
CURSOR_PIPE = f"{HOME}/cursor.fifo"
CURSOR_STYLE_PATH = f"{HOME}/cursor_style"
# Written by the QML overlay (auto) or overridden via pointer.conf.
UI_ORIENTATION_PATH = f"{HOME}/ui_orientation"
CURSOR_READY = "paperpointer-qml-3.28.0.164"
UINPUT_PATHS = ("/dev/uinput", "/dev/input/uinput")
VALID_ORIENTATIONS = frozenset({0, 90, 180, 270})
ORIENTATION_AUTO = "auto"


EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
SYN_REPORT = 0
SYN_DROPPED = 3

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_0 = 0x100
BTN_1 = 0x101
BTN_TOUCH = 0x14A
BTN_TOOL_FINGER = 0x145

PRIMARY_CLICK = {BTN_LEFT, BTN_0}
RIGHT_CLICK = {BTN_RIGHT, BTN_1}
# finger-on-pad only (not a UI click by itself)
FINGER_CODES = {BTN_TOUCH, BTN_TOOL_FINGER}

REL_X = 0x00
REL_Y = 0x01
REL_WHEEL = 0x08

ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

INPUT_PROP_DIRECT = 0x01
BUS_VIRTUAL = 0x06

TOUCH_X_MAX = 2064
TOUCH_Y_MAX = 2832
CURSOR_HZ_DEFAULT = 30
CURSOR_HZ_MAX = 40
CURSOR_LAG_MS_DEFAULT = 50
CURSOR_LAG_MS_MAX = 250
# 0 = stay painted while a pointer source is open (shipped default).
# Positive values hide the crosshair after that many idle milliseconds.
CURSOR_HIDE_MS_DEFAULT = 0
CURSOR_HIDE_MS_MAX = 600_000
CURSOR_STYLE_DEFAULT = "cross"
CURSOR_STYLES = frozenset({"cross", "win95"})
KEY_BITMAP_BYTES = 96
CLICK_DRAG_THRESHOLD = 10.0

EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

IGNORE_NAME_SUBSTR = (
    "elan marker",
    "elan touch",
    "snvs-powerkey",
    "hall effect",
    "gpio-keys",
    "paperpointer",
)

# Optional OSK suppress: spoof Type Folio name so xochitl sets keyboardConnected.
# Side effect: Paper Pro treats Type Folio as landscape — default off.
RM_KEYBOARD_NAME = b"rM_Keyboard"
# Keys that make udev classify the node as ID_INPUT_KEYBOARD (A-Z + Enter).
_PRESENCE_KEY_CODES = tuple(range(1, 58))  # ESC..KEY_SPACE range on Linux

# Cursor defaults off until the QML overlay passes its version/health checks.
DEFAULT_CONF: dict = {
    "accel": 2.0,
    "touch_x_max": TOUCH_X_MAX,
    "touch_y_max": TOUCH_Y_MAX,
    "display_w": 1620,
    "display_h": 2160,
    "invert_x": 0,
    "invert_y": 0,
    "swap_xy": 0,
    "scroll_as_swipe": 1,
    "finger_drag": 0,
    "cursor": 0,
    "cursor_hz": CURSOR_HZ_DEFAULT,
    "cursor_lag_ms": CURSOR_LAG_MS_DEFAULT,
    "cursor_hide_ms": CURSOR_HIDE_MS_DEFAULT,
    "cursor_style": CURSOR_STYLE_DEFAULT,
    # Logical (upright UI) cursor space is never rotated. Synthetic touch is
    # mapped to the Elan panel's fixed portrait axes via this setting:
    # 0/90/180/270 force a fixed UI rotation; "auto" reads ui_orientation.
    "orientation": ORIENTATION_AUTO,
    # 0 = do not spoof Type Folio (no forced landscape). 1 = hide OSK on click
    # by creating rM_Keyboard while a BT keyboard is present (landscape side effect).
    "osk_suppress": 0,
    "source": "",
}
