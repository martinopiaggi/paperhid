#!/usr/bin/env python3
"""paperpointerd — BT mouse/touchpad → multitouch clicks on Paper Pro.

Clicks inject a synthetic finger (uinput). No pen/marker injection:
xochitl has no hover cursor, and writing the real Elan marker breaks input.

Cursor motion is coalesced to the version-gated XOVI QML overlay.  The stock
epaper Qt platform has no mouse/hover implementation.
"""
from __future__ import annotations

import array
from collections import deque
import errno
import glob
import math
import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time

try:
    import fcntl
except ImportError:  # Windows / non-Linux hosts (unit tests)
    fcntl = None  # type: ignore[assignment]

try:
    import select
except ImportError:
    select = None  # type: ignore[assignment]

HOME = "/home/root/.paperpointer"
LOG_PATH = os.path.join(HOME, "pointer.log")
CONF_PATH = os.path.join(HOME, "pointer.conf")
CURSOR_BROKER_IN = "/run/xovi-mb"
CURSOR_BROKER_OUT = "/run/xovi-mb-out"
CURSOR_PIPE = "/home/root/.paperpointer/cursor.fifo"
CURSOR_STYLE_PATH = os.path.join(HOME, "cursor_style")
# Written by the QML overlay (auto) or overridden via pointer.conf.
UI_ORIENTATION_PATH = os.path.join(HOME, "ui_orientation")
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

# xochitl KeyboardInfo looks for Type Folio-style keyboards (name rM_Keyboard
# and/or ID_INPUT_KEYBOARD). Bluetooth HID keyboards type fine via
# EpaperEvdevKeyboard but do not always set keyboardConnected, so a touch
# or synthetic mouse click still pops the on-screen virtual keyboard.
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
    "source": "",
}


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


def parse_orientation_mode(value) -> int | str | None:
    """Parse orientation=auto|0|90|180|270. Returns None when invalid."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in VALID_ORIENTATIONS else None
    text = str(value).strip().lower()
    if text == ORIENTATION_AUTO:
        return ORIENTATION_AUTO
    try:
        angle = int(text)
    except (TypeError, ValueError):
        return None
    return angle if angle in VALID_ORIENTATIONS else None


def normalize_orientation(rotation) -> int:
    """Clamp a rotation to a known UI angle; unknown values become 0°."""
    try:
        angle = int(rotation)
    except (TypeError, ValueError):
        return 0
    return angle if angle in VALID_ORIENTATIONS else 0


def logical_to_physical(
    nx: float, ny: float, rotation: int
) -> tuple[float, float]:
    """Map normalized upright-UI coords to the Elan panel's portrait axes.

    Logical coordinates describe the cursor as drawn on the rotated UI.
    Physical coordinates are what the fixed-orientation touchscreen expects.
    This is the only rotation used for synthetic touch output.
    """
    nx = float(nx)
    ny = float(ny)
    if not math.isfinite(nx) or not math.isfinite(ny):
        raise ValueError("touch coordinates must be finite")
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    rot = normalize_orientation(rotation)
    if rot == 0:
        return nx, ny
    if rot == 90:
        return 1.0 - ny, nx
    if rot == 180:
        return 1.0 - nx, 1.0 - ny
    # 270°
    return ny, 1.0 - nx


def physical_to_logical(
    nx: float, ny: float, rotation: int
) -> tuple[float, float]:
    """Inverse of logical_to_physical (for tests and diagnostics)."""
    nx = float(nx)
    ny = float(ny)
    if not math.isfinite(nx) or not math.isfinite(ny):
        raise ValueError("touch coordinates must be finite")
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    rot = normalize_orientation(rotation)
    if rot == 0:
        return nx, ny
    if rot == 90:
        return ny, 1.0 - nx
    if rot == 180:
        return 1.0 - nx, 1.0 - ny
    # 270°
    return 1.0 - ny, nx


# xochitl UI names from orientationwrapper (preferred).
# Qt Screen.orientation is NOT updated on Paper Pro; do not use it.
XOCHITL_ORIENTATION_DEG = {
    "Portrait": 0,
    "Landscape": 90,
    "InvertedPortrait": 180,
    "InvertedLandscape": 270,
}
# CSL sensor names (paired with the above on device logs).
CSL_ORIENTATION_DEG = {
    "ORIENTATION_NORMAL": 0,
    "ORIENTATION_RIGHT_UP": 90,
    "ORIENTATION_BOTTOM_UP": 180,
    "ORIENTATION_LEFT_UP": 270,
}


def parse_xochitl_orientation_log(text: str) -> tuple[int, str] | None:
    """Extract the latest UI orientation from xochitl journal/log text.

    Prefers ``Setting new orientation <Name>`` (what the UI actually applies)
    over the raw CSL sensor line.
    """
    if not text:
        return None
    last_ui: tuple[int, str] | None = None
    last_csl: tuple[int, str] | None = None
    for line in text.splitlines():
        if "Setting new orientation" in line:
            for name, deg in XOCHITL_ORIENTATION_DEG.items():
                # Match whole name so Landscape does not hit InvertedLandscape.
                if re.search(rf"Setting new orientation\s+{name}\b", line):
                    last_ui = (deg, f"xochitl={name}")
                    break
        elif "New orientation from csl" in line:
            for name, deg in CSL_ORIENTATION_DEG.items():
                if name in line:
                    last_csl = (deg, f"csl={name}")
                    break
    return last_ui or last_csl


def read_xochitl_orientation(
    lines: int = 80,
) -> tuple[int, str] | None:
    """Query journalctl for the last xochitl UI orientation (best effort)."""
    try:
        proc = subprocess.run(
            [
                "journalctl",
                "-u",
                "xochitl",
                "--no-pager",
                "-n",
                str(max(10, int(lines))),
                "--output=cat",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        parsed = parse_xochitl_orientation_log(proc.stdout or "")
        if parsed is not None:
            return parsed
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # Fallback: logread (BusyBox systems without journal for the unit).
    try:
        proc = subprocess.run(
            ["logread"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        # Only scan the tail — logread can be large.
        tail = "\n".join((proc.stdout or "").splitlines()[-200:])
        return parse_xochitl_orientation_log(tail)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class UiOrientation:
    """Cached UI rotation for touch injection — never blocks the input loop.

    Cursor motion and FIFO publishing stay in logical coordinates and must not
    wait on orientation detection.

    Architecture::

        background watcher → cached rotation
        mouse event → logical cursor + publish (immediate)
                   ↘ cheap cached transform only when touch is needed

    For ``orientation=auto`` a daemon thread polls xochitl (journalctl/logread)
    off the hot path. ``get()`` is always an immediate cache read.

    Fixed ``orientation=0|90|180|270`` returns immediately with no thread.
    """

    # How often the watcher may launch journalctl/logread (never on get()).
    POLL_S = 1.0

    def __init__(
        self,
        mode: int | str = ORIENTATION_AUTO,
        path: str | None = None,
        *,
        poll_s: float | None = None,
        reader=None,
        start_watcher: bool = True,
    ):
        parsed = parse_orientation_mode(mode)
        self.mode: int | str = (
            ORIENTATION_AUTO if parsed is None else parsed
        )
        self.path = UI_ORIENTATION_PATH if path is None else path
        self.poll_s = float(self.POLL_S if poll_s is None else poll_s)
        if self.poll_s < 0.05:
            self.poll_s = 0.05
        # Injectable for tests; production uses read_xochitl_orientation.
        self._reader = read_xochitl_orientation if reader is None else reader
        self._lock = threading.Lock()
        self._value = 0
        self._source = "default"
        self._last_logged: int | None = None
        self._last_logged_source: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        if self.mode != ORIENTATION_AUTO:
            angle = normalize_orientation(self.mode)
            self._value = angle
            self._source = f"fixed={self.mode}"
            self._log_if_changed(angle)
            return

        # Fast seed from status file only (no subprocess on the constructor path).
        self._seed_from_file()
        if start_watcher:
            self._thread = threading.Thread(
                target=self._watch,
                name="paperpointer-orient",
                daemon=True,
            )
            self._thread.start()

    def get(self, force: bool = False) -> int:
        """Return the latest cached rotation. Never launches a process.

        ``force`` is accepted for API compatibility but does not run journalctl;
        the background watcher is the only refresh path for auto mode.
        """
        del force  # hot path must never block on process I/O
        with self._lock:
            return self._value

    @property
    def source(self) -> str:
        with self._lock:
            return self._source

    def close(self) -> None:
        """Stop the background watcher (if any)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(2.0, self.poll_s + 1.0))
        self._thread = None

    def _seed_from_file(self) -> None:
        try:
            with open(self.path, encoding="ascii") as f:
                raw = f.read().strip()
            angle = normalize_orientation(int(raw))
        except (OSError, ValueError, TypeError):
            return
        with self._lock:
            self._value = angle
            self._source = f"file={self.path}"
        self._log_if_changed(angle)

    def _watch(self) -> None:
        # Refresh immediately once, then on the poll interval.
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception as e:  # noqa: BLE001 — watcher must not die
                log(f"orientation watcher error: {e!r}")
            if self._stop.wait(self.poll_s):
                return

    def _refresh(self) -> None:
        angle, source = self._resolve_auto()
        with self._lock:
            self._value = angle
            self._source = source
        self._log_if_changed(angle)
        self._publish_status_file(angle)

    def _resolve_auto(self) -> tuple[int, str]:
        try:
            parsed = self._reader()
        except Exception as e:  # noqa: BLE001
            log(f"orientation reader failed: {e!r}")
            parsed = None
        if parsed is not None:
            angle, source = parsed
            return normalize_orientation(angle), str(source)

        try:
            with open(self.path, encoding="ascii") as f:
                raw = f.read().strip()
            angle = normalize_orientation(int(raw))
            return angle, f"file={self.path}"
        except (OSError, ValueError, TypeError):
            pass

        with self._lock:
            return self._value, f"cached,{self._source}"

    def _publish_status_file(self, angle: int) -> None:
        """Mirror the active angle for diagnostics (daemon is source of truth)."""
        try:
            os.makedirs(HOME, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="ascii") as f:
                f.write(f"{int(angle)}\n")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _log_if_changed(self, angle: int) -> None:
        with self._lock:
            source = self._source
        if angle != self._last_logged or source != self._last_logged_source:
            log(f"ui orientation={angle} ({source})")
            self._last_logged = angle
            self._last_logged_source = source


def write_cursor_style_file(style: str | None) -> str:
    """Publish the active skin for the QML overlay (one-line status file)."""
    name = normalize_cursor_style(style)
    try:
        os.makedirs(HOME, exist_ok=True)
        tmp = CURSOR_STYLE_PATH + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(name + "\n")
        os.replace(tmp, CURSOR_STYLE_PATH)
    except OSError as e:
        log(f"cursor style file write failed: {e}")
    return name


def _ioc_read(n: int, size: int) -> int:
    return 0x80004500 + (size << 16) + n


def eviocgbit(ev: int, size: int) -> int:
    return _ioc_read(0x20 + ev, size)


def eviocgname(size: int = 256) -> int:
    return _ioc_read(0x06, size)


def eviocgkey(size: int = KEY_BITMAP_BYTES) -> int:
    return _ioc_read(0x18, size)


def key_codes_from_bitmap(
    bitmap: bytes | bytearray, codes: set[int]
) -> set[int]:
    """Return the requested key codes currently set in an EVIOCGKEY bitmap."""
    return {
        code
        for code in codes
        if code >= 0
        and code // 8 < len(bitmap)
        and bitmap[code // 8] & (1 << (code % 8))
    }


def pressed_key_codes(fd: int, codes: set[int]) -> set[int]:
    """Query held keys after SYN_DROPPED so a held click can be restored."""
    bitmap = bytearray(KEY_BITMAP_BYTES)
    fcntl.ioctl(fd, eviocgkey(len(bitmap)), bitmap)
    return key_codes_from_bitmap(bitmap, codes)


def device_name(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        buf = bytearray(256)
        try:
            fcntl.ioctl(fd, eviocgname(256), buf)
            return buf.split(b"\x00", 1)[0].decode("utf-8", "replace")
        except OSError:
            return ""
    finally:
        os.close(fd)


def has_bits(path: str, ev_type: int, bits_wanted: list[int]) -> bool:
    size = 512
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        buf = bytearray(size)
        try:
            fcntl.ioctl(fd, eviocgbit(ev_type, size), buf)
        except OSError:
            return False
        arr = array.array("B", buf)

        def test(bit: int) -> bool:
            return bool(arr[bit // 8] & (1 << (bit % 8)))

        return all(test(b) for b in bits_wanted)
    finally:
        os.close(fd)


def is_ignored(name: str) -> bool:
    """True if name is a stock-only node we must never consume as HID source."""
    low = name.lower()
    return any(s in low for s in IGNORE_NAME_SUBSTR)


def classify_from_caps(
    name: str,
    *,
    has_abs_xy: bool = False,
    has_rel_xy: bool = False,
    has_btn_tool_finger: bool = False,
    has_btn_touch: bool = False,
    has_btn_left: bool = False,
    has_btn_0: bool = False,
) -> str | None:
    """Pure source classifier: name + capability flags → 'abs_pad' | 'rel' | None.

    Does not open devices. Stock Elan pen/touch and other ignore list names return None.
    """
    if not name or is_ignored(name):
        return None
    low = name.lower()
    if has_abs_xy:
        if (
            "touchpad" in low
            or "trackpad" in low
            or has_btn_tool_finger
            or has_btn_touch
            or has_btn_left
            or has_btn_0
        ):
            return "abs_pad"
    if has_rel_xy:
        if "keyboard" in low and "mouse" not in low:
            return None
        return "rel"
    return None


def classify_source(path: str) -> str | None:
    name = device_name(path)
    return classify_from_caps(
        name,
        has_abs_xy=has_bits(path, EV_ABS, [ABS_X, ABS_Y]),
        has_rel_xy=has_bits(path, EV_REL, [REL_X, REL_Y]),
        has_btn_tool_finger=has_bits(path, EV_KEY, [BTN_TOOL_FINGER]),
        has_btn_touch=has_bits(path, EV_KEY, [BTN_TOUCH]),
        has_btn_left=has_bits(path, EV_KEY, [BTN_LEFT]),
        has_btn_0=has_bits(path, EV_KEY, [BTN_0]),
    )


def list_sources() -> list[tuple[str, str, str]]:
    out = []
    for path in sorted(glob.glob("/dev/input/event*")):
        kind = classify_source(path)
        if kind:
            out.append((path, kind, device_name(path)))
    out.sort(key=lambda t: (0 if t[1] == "rel" else 1, t[0]))
    return out


def choose_sources(
    sources: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Choose one HID path, preferring its kernel-produced relative mouse.

    Composite touchpads commonly expose both a REL mouse node and a raw ABS
    node for the same finger. Opening both doubles or fights each movement.
    """
    relative = [source for source in sources if source[1] == "rel"]
    if relative:
        return relative[:1]
    return sources[:1]


def _uinput_open() -> int:
    path = next((p for p in UINPUT_PATHS if os.path.exists(p)), None)
    if not path:
        raise RuntimeError("no /dev/uinput")
    return os.open(path, os.O_WRONLY | os.O_NONBLOCK)


def _uinput_create(fd: int, name: bytes, absmax: list[int]) -> None:
    name = (name[:79] + b"\x00").ljust(80, b"\x00")
    uid = struct.pack("HHHH", BUS_VIRTUAL, 0x0001, 0x0001, 1)
    ff = struct.pack("i", 0)
    ABS_CNT = 0x40
    absmin = [0] * ABS_CNT
    absfuzz = [0] * ABS_CNT
    absflat = [0] * ABS_CNT
    body = name + uid + ff
    body += struct.pack(f"{ABS_CNT}i", *absmax)
    body += struct.pack(f"{ABS_CNT}i", *absmin)
    body += struct.pack(f"{ABS_CNT}i", *absfuzz)
    body += struct.pack(f"{ABS_CNT}i", *absflat)
    os.write(fd, body)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(0.15)


def parse_input_device_blocks(blob: str) -> list[dict]:
    """Parse ``/proc/bus/input/devices`` into name/handlers/bit maps."""
    devices: list[dict] = []
    cur: dict | None = None
    for raw in (blob or "").splitlines():
        line = raw.rstrip()
        if not line:
            if cur:
                devices.append(cur)
                cur = None
            continue
        if line.startswith("I:"):
            if cur:
                devices.append(cur)
            cur = {"name": "", "handlers": "", "ev": "", "key": ""}
            continue
        if cur is None:
            continue
        if line.startswith("N: Name="):
            cur["name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("H: Handlers="):
            cur["handlers"] = line.split("=", 1)[1].strip()
        elif line.startswith("B: EV="):
            cur["ev"] = line.split("=", 1)[1].strip()
        elif line.startswith("B: KEY="):
            cur["key"] = line.split("=", 1)[1].strip()
    if cur:
        devices.append(cur)
    return devices


def is_external_keyboard_device(dev: dict) -> bool:
    """True for a real Bluetooth/USB keyboard HID, not stock tablet keys."""
    name = (dev.get("name") or "").strip()
    low = name.lower()
    if not name:
        return False
    # Never count our own presence node or the synthetic touch device.
    if low in ("rm_keyboard", "paperpointer-touch") or low.startswith("paperpointer"):
        return False
    for needle in IGNORE_NAME_SUBSTR:
        if needle in low:
            return False
    # Wireless radio / consumer-control companions of composite HIDs.
    if "wireless radio" in low or "consumer control" in low:
        return False
    handlers = (dev.get("handlers") or "").lower()
    # Prefer explicit keyboard naming; also accept kbd handler + KEY bitmap.
    if "keyboard" in low:
        return True
    if "kbd" in handlers.split() and (dev.get("key") or "").strip():
        # Require more than a couple of special keys (powerkey is excluded above).
        key_words = (dev.get("key") or "").split()
        return len(key_words) >= 2
    return False


def external_keyboard_present(devices_blob: str | None = None) -> bool:
    """Whether a non-synthetic external keyboard is currently attached.

    Used to decide if xochitl should treat the session as keyboard-connected
    (suppress the virtual keyboard on touch / mouse focus).
    """
    if devices_blob is None:
        try:
            with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as f:
                devices_blob = f.read()
        except OSError:
            return False
    return any(is_external_keyboard_device(d) for d in parse_input_device_blocks(devices_blob))


class KeyboardPresence:
    """Hold a Type-Folio-named uinput keyboard while a BT keyboard is present.

    xochitl's ``KeyboardInfo`` / virtual-keyboard module suppresses the
    on-screen keyboard when it believes a hardware keyboard is connected
    (Type Folio appears as ``rM_Keyboard``). Bluetooth keyboards often type
    correctly without flipping that flag, so mouse-click focus still opens
    the OSK. While any external keyboard HID is present, we expose a silent
    ``rM_Keyboard`` uinput node so the OSK stays down.
    """

    def __init__(self) -> None:
        self.fd = -1
        self._active = False

    @property
    def active(self) -> bool:
        return self._active and self.fd >= 0

    def sync(self, want: bool | None = None) -> None:
        if want is None:
            want = external_keyboard_present()
        if want and not self.active:
            self._create()
        elif not want and self.active:
            self.close()

    def _create(self) -> None:
        if fcntl is None:
            return
        try:
            fd = _uinput_open()
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            for code in _PRESENCE_KEY_CODES:
                try:
                    fcntl.ioctl(fd, UI_SET_KEYBIT, code)
                except OSError:
                    pass
            # No absolute axes — pure keyboard.
            absmax = [0] * 0x40
            _uinput_create(fd, RM_KEYBOARD_NAME, absmax)
            self.fd = fd
            self._active = True
            log("keyboard presence: rM_Keyboard uinput up (suppress OSK)")
        except Exception as e:
            log(f"keyboard presence create failed: {e!r}")
            self.close()

    def close(self) -> None:
        if self.fd >= 0:
            try:
                if fcntl is not None:
                    fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
        was = self._active
        self.fd = -1
        self._active = False
        if was:
            log("keyboard presence: rM_Keyboard uinput down")


def _ev(fd: int, etype: int, code: int, value: int) -> None:
    os.write(fd, struct.pack(EVENT_FMT, 0, 0, etype, code, int(value)))


def _sync(fd: int) -> None:
    _ev(fd, EV_SYN, SYN_REPORT, 0)


def format_cursor_message(nx: float, ny: float, visible: bool) -> bytes:
    """Encode one bounded line for the private cursor FIFO."""
    nx = float(nx)
    ny = float(ny)
    if not math.isfinite(nx) or not math.isfinite(ny):
        raise ValueError("cursor coordinates must be finite")
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return f"{nx:.6f},{ny:.6f},{int(bool(visible))}\n".encode("ascii")


class CursorPublisher:
    """Latest-wins, rate-limited cursor delivery over one persistent FIFO.

    XOVI's command executor keeps one `cat` process attached to the read side.
    The upstream message-broker input cannot carry motion: it leaks one file
    descriptor per command. The evdev loop never performs FIFO I/O directly.
    """

    def __init__(self, rate_hz: float = CURSOR_HZ_DEFAULT, writer=None):
        rate_hz = float(rate_hz)
        if not math.isfinite(rate_hz):
            raise ValueError("cursor rate must be finite")
        rate_hz = max(1.0, min(float(CURSOR_HZ_MAX), rate_hz))
        self.interval = 1.0 / rate_hz
        self._writer = writer
        self._fifo_fd = -1
        self._condition = threading.Condition()
        self._pending: tuple[float, float, bool] | None = None
        self._last_sent: tuple[float, float, bool] | None = None
        self._last_sent_at = 0.0
        self._sent_history: deque[
            tuple[float, tuple[float, float, bool]]
        ] = deque(maxlen=128)
        self._stopping = False
        self._last_error_log = 0.0
        self._thread = threading.Thread(
            target=self._run, name="paperpointer-cursor", daemon=True
        )
        self._thread.start()
        log(f"cursor fifo={CURSOR_PIPE} rate={rate_hz:g}Hz")

    def _write_fifo(self, packet: bytes) -> None:
        if self._fifo_fd < 0:
            self._fifo_fd = os.open(CURSOR_PIPE, os.O_WRONLY | os.O_NONBLOCK)
        try:
            written = os.write(self._fifo_fd, packet)
            if written != len(packet):
                raise OSError(errno.EIO, f"short cursor write {written}/{len(packet)}")
        except OSError:
            try:
                os.close(self._fifo_fd)
            except OSError:
                pass
            self._fifo_fd = -1
            raise

    def publish(
        self, tx: float, ty: float, tx_max: int, ty_max: int, visible: bool = True
    ) -> None:
        tx = float(tx)
        ty = float(ty)
        tx_max = int(tx_max)
        ty_max = int(ty_max)
        if not math.isfinite(tx) or not math.isfinite(ty):
            raise ValueError("cursor position must be finite")
        if tx_max <= 0 or ty_max <= 0:
            raise ValueError("cursor coordinate ranges must be positive")
        item = (
            max(0.0, min(1.0, tx / tx_max)),
            max(0.0, min(1.0, ty / ty_max)),
            bool(visible),
        )
        with self._condition:
            if self._stopping:
                return
            self._pending = item
            self._condition.notify()

    def _send(self, item: tuple[float, float, bool]) -> bool:
        try:
            (self._writer or self._write_fifo)(format_cursor_message(*item))
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENXIO, errno.EPIPE):
                return False
            now = time.monotonic()
            if now - self._last_error_log >= 30.0:
                log(f"cursor broker write failed: {e}")
                self._last_error_log = now
            return False
        with self._condition:
            self._last_sent = item
            self._last_sent_at = time.monotonic()
            self._sent_history.append((self._last_sent_at, item))
        return True

    def last_sent_position(
        self, tx_max: int, ty_max: int, max_age: float = 0.25
    ) -> tuple[float, float] | None:
        """Last cursor position accepted by the FIFO, in touch coordinates.

        The FIFO is one-way, so this is not a display acknowledgement. It is
        nevertheless a closer click target than a newer, still-queued point.
        """
        with self._condition:
            item = self._last_sent
            age = time.monotonic() - self._last_sent_at
        if item is None or not item[2] or age > max(0.0, float(max_age)):
            return None
        return item[0] * int(tx_max), item[1] * int(ty_max)

    def visual_position(
        self,
        tx_max: int,
        ty_max: int,
        lag_ms: int,
        max_age: float = 0.5,
    ) -> tuple[float, float] | None:
        """Estimate the coordinate currently visible on the e-ink panel.

        FIFO delivery is not a panel-update acknowledgement. During motion the
        most recently delivered coordinate can therefore be ahead of the
        crosshair the user can see. Select a bounded older sample, but keep
        stationary clicks exact and never cross a hide boundary.
        """
        lag_ms = max(0, min(CURSOR_LAG_MS_MAX, int(lag_ms)))
        now = time.monotonic()
        cutoff = now - lag_ms / 1000.0
        with self._condition:
            history = tuple(self._sent_history)
        if not history:
            return None
        last_at, last_item = history[-1]
        if (
            not last_item[2]
            or now - last_at > max(0.0, float(max_age))
        ):
            return None

        candidate = last_item
        for sent_at, item in reversed(history):
            if not item[2]:
                break
            candidate = item
            if sent_at <= cutoff:
                break
        return candidate[0] * int(tx_max), candidate[1] * int(ty_max)

    def _run(self) -> None:
        next_allowed = 0.0
        while True:
            final = False
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    item = (0.5, 0.5, False)
                    self._pending = None
                    final = True
                while not final:
                    delay = next_allowed - time.monotonic()
                    if delay <= 0:
                        break
                    self._condition.wait(delay)
                    if self._stopping:
                        item = (0.5, 0.5, False)
                        self._pending = None
                        final = True
                        break
                if not final:
                    item = self._pending
                    self._pending = None
            if item is not None:
                self._send(item)
            if final:
                return
            if item is not None:
                next_allowed = time.monotonic() + self.interval

    def close(self) -> None:
        with self._condition:
            if self._stopping:
                return
            self._stopping = True
            self._condition.notify()
        self._thread.join(timeout=1.0)
        if self._thread.is_alive():
            log("cursor publisher did not stop in time")
        if self._fifo_fd >= 0:
            try:
                os.close(self._fifo_fd)
            except OSError:
                pass
            self._fifo_fd = -1


def call_ui_broker(signal: str, value: str = "", timeout: float = 2.0) -> str:
    """Send one request/reply packet to XOVI's QML broker without FIFO deadlock."""
    if not os.path.exists(CURSOR_BROKER_IN) or not os.path.exists(CURSOR_BROKER_OUT):
        return ""
    try:
        rfd = os.open(CURSOR_BROKER_OUT, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        packet = f">u{signal}:{value}\n".encode("utf-8")
        try:
            wfd = os.open(CURSOR_BROKER_IN, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return ""
        try:
            if os.write(wfd, packet) != len(packet):
                return ""
        finally:
            os.close(wfd)

        deadline = time.monotonic() + max(0.1, timeout)
        response = bytearray()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([rfd], [], [], 0.1)
            if not ready:
                continue
            chunk = os.read(rfd, 4096)
            if chunk:
                response.extend(chunk)
                break
        return response.decode("utf-8", "replace").strip()
    finally:
        os.close(rfd)


class TouchClick:
    """Synthetic multitouch finger — UI clicks/drags everywhere."""

    def __init__(self, x_max: int, y_max: int):
        self.x_max = x_max
        self.y_max = y_max
        self.fd = _uinput_open()
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOOL_FINGER)
        for a in (
            ABS_MT_SLOT,
            ABS_MT_TRACKING_ID,
            ABS_MT_POSITION_X,
            ABS_MT_POSITION_Y,
            ABS_MT_PRESSURE,
            ABS_MT_TOUCH_MAJOR,
            ABS_X,
            ABS_Y,
        ):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, a)
        try:
            fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        except OSError:
            pass
        absmax = [0] * 0x40
        absmax[ABS_MT_SLOT] = 0
        absmax[ABS_MT_TRACKING_ID] = 65535
        absmax[ABS_MT_POSITION_X] = x_max
        absmax[ABS_MT_POSITION_Y] = y_max
        absmax[ABS_MT_PRESSURE] = 255
        absmax[ABS_MT_TOUCH_MAJOR] = 255
        absmax[ABS_X] = x_max
        absmax[ABS_Y] = y_max
        _uinput_create(self.fd, b"paperpointer-touch", absmax)
        self.down = False
        self.tid = 1
        self.x = x_max // 2
        self.y = y_max // 2
        log(f"uinput touch {x_max}x{y_max}")

    def move(self, x: float, y: float) -> None:
        self.x = int(max(0, min(self.x_max, x)))
        self.y = int(max(0, min(self.y_max, y)))
        if self.down:
            self._emit(True)

    def contact(self, down: bool) -> None:
        if down and not self.down:
            self.tid = self.tid + 1 if self.tid < 60000 else 1
            self.down = True
            self._emit(True)
        elif not down and self.down:
            self.down = False
            self._emit(False)

    def _emit(self, active: bool) -> None:
        _ev(self.fd, EV_ABS, ABS_MT_SLOT, 0)
        if active:
            _ev(self.fd, EV_ABS, ABS_MT_TRACKING_ID, self.tid)
            _ev(self.fd, EV_ABS, ABS_MT_POSITION_X, self.x)
            _ev(self.fd, EV_ABS, ABS_MT_POSITION_Y, self.y)
            _ev(self.fd, EV_ABS, ABS_MT_PRESSURE, 60)
            _ev(self.fd, EV_ABS, ABS_MT_TOUCH_MAJOR, 12)
            _ev(self.fd, EV_KEY, BTN_TOUCH, 1)
            _ev(self.fd, EV_KEY, BTN_TOOL_FINGER, 1)
            _ev(self.fd, EV_ABS, ABS_X, self.x)
            _ev(self.fd, EV_ABS, ABS_Y, self.y)
        else:
            _ev(self.fd, EV_ABS, ABS_MT_TRACKING_ID, -1)
            _ev(self.fd, EV_KEY, BTN_TOUCH, 0)
            _ev(self.fd, EV_KEY, BTN_TOOL_FINGER, 0)
        _sync(self.fd)

    def tap(self, x: float, y: float, hold_ms: int = 45) -> None:
        self.move(x, y)
        self.contact(True)
        time.sleep(hold_ms / 1000.0)
        self.contact(False)

    def close(self) -> None:
        if self.fd >= 0:
            try:
                if self.down:
                    self.contact(False)
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def clamp_move(cfg: dict, x: float, y: float, dx: float, dy: float) -> tuple[float, float]:
    """Apply REL deltas with accel/invert/swap; clamp to Elan-touch ranges."""
    if cfg["swap_xy"]:
        dx, dy = dy, dx
    if cfg["invert_x"]:
        dx = -dx
    if cfg["invert_y"]:
        dy = -dy
    a = float(cfg["accel"])
    x = max(0.0, min(float(cfg["touch_x_max"]), x + dx * a))
    y = max(0.0, min(float(cfg["touch_y_max"]), y + dy * a))
    return x, y


def map_abs_axis(
    value: float,
    mn: float,
    mx: float,
    touch_max: float,
    invert: bool = False,
) -> float:
    """Map one pad ABS axis into [0, touch_max] (clamped)."""
    if mx <= mn:
        return 0.0
    n = (float(value) - mn) / (mx - mn) * float(touch_max)
    if invert:
        n = float(touch_max) - n
    return max(0.0, min(float(touch_max), n))


def map_abs_position(
    cfg: dict,
    x: float,
    y: float,
    axis: str,
    value: float,
    abs_x: tuple[float, float] | None,
    abs_y: tuple[float, float] | None,
) -> tuple[float, float]:
    """Map pad ABS_X or ABS_Y into touch coords (same rules as the daemon loop)."""
    if axis == "x" and abs_x:
        mn, mx = abs_x
        n = map_abs_axis(
            value, mn, mx, cfg["touch_x_max"], invert=bool(cfg.get("invert_x"))
        )
        if cfg.get("swap_xy"):
            y = n
        else:
            x = n
    elif axis == "y" and abs_y:
        mn, mx = abs_y
        n = map_abs_axis(
            value, mn, mx, cfg["touch_y_max"], invert=bool(cfg.get("invert_y"))
        )
        if cfg.get("swap_xy"):
            x = n
        else:
            y = n
    return x, y


def moved_past_drag_threshold(
    start: tuple[float, float] | None,
    x: float,
    y: float,
    threshold: float = CLICK_DRAG_THRESHOLD,
) -> bool:
    """Ignore press jitter until motion is large enough to be intentional."""
    if start is None:
        return True
    return math.hypot(float(x) - start[0], float(y) - start[1]) >= threshold


class InputFrame:
    """Accumulate one evdev frame and fail closed after SYN_DROPPED."""

    __slots__ = (
        "rel_x",
        "rel_y",
        "wheel",
        "abs_x",
        "abs_y",
        "keys",
        "dropped",
    )

    def __init__(self) -> None:
        self.dropped = False
        self.clear()

    def clear(self) -> None:
        self.rel_x = 0
        self.rel_y = 0
        self.wheel = 0
        self.abs_x: int | None = None
        self.abs_y: int | None = None
        self.keys: list[tuple[int, int]] = []

    def feed(self, etype: int, code: int, value: int):
        """Return ('frame', data), ('dropped', None), or None."""
        if etype == EV_SYN and code == SYN_DROPPED:
            self.clear()
            self.dropped = True
            return "dropped", None
        if self.dropped:
            if etype == EV_SYN and code == SYN_REPORT:
                self.dropped = False
                self.clear()
                return "recovered", None
            return None
        if etype == EV_SYN and code == SYN_REPORT:
            data = (
                self.rel_x,
                self.rel_y,
                self.wheel,
                self.abs_x,
                self.abs_y,
                tuple(self.keys),
            )
            self.clear()
            return "frame", data
        if etype == EV_REL:
            if code == REL_X:
                self.rel_x += value
            elif code == REL_Y:
                self.rel_y += value
            elif code == REL_WHEEL:
                self.wheel += value
        elif etype == EV_ABS:
            if code == ABS_X:
                self.abs_x = value
            elif code == ABS_Y:
                self.abs_y = value
        elif etype == EV_KEY:
            self.keys.append((code, value))
        return None


class ContactState:
    """Track independent HID aliases and derive one multitouch contact.

    Mirrors run_loop button handling without uinput. contact_active is what
    TouchClick.contact would be driven with.
    """

    __slots__ = ("_primary_codes", "_finger_codes", "contact_active")

    def __init__(self) -> None:
        self._primary_codes: set[int] = set()
        self._finger_codes: set[int] = set()
        self.contact_active = False

    @property
    def buttons(self) -> int:
        return int(bool(self._primary_codes))

    @property
    def finger(self) -> int:
        return int(bool(self._finger_codes))

    def on_primary(
        self, pressed: bool, finger_drag: bool = False, code: int = BTN_LEFT
    ) -> bool:
        """Primary button edge. Returns new contact_active."""
        if pressed:
            self._primary_codes.add(code)
        else:
            self._primary_codes.discard(code)
        self.contact_active = self.want_contact(finger_drag)
        return self.contact_active

    def on_finger(
        self, present: bool, finger_drag: bool = False, code: int = BTN_TOUCH
    ) -> bool:
        """Pad finger present/absent. Returns contact_active (may be unchanged)."""
        if present:
            self._finger_codes.add(code)
        else:
            self._finger_codes.discard(code)
        self.contact_active = self.want_contact(finger_drag)
        return self.contact_active

    def want_contact(self, finger_drag: bool = False) -> bool:
        return bool(self._primary_codes or (finger_drag and self._finger_codes))

    def reset(self) -> None:
        self._primary_codes.clear()
        self._finger_codes.clear()
        self.contact_active = False


def _abs_range(fd: int, axis: int) -> tuple[int, int]:
    buf = bytearray(24)
    req = 0x80004540 + (24 << 16) + axis
    fcntl.ioctl(fd, req, buf)
    _v, mn, mx, _f, _fl, _r = struct.unpack("iiiiii", buf)
    return mn, mx


def open_sources(cfg: dict) -> list[dict]:
    forced = (cfg.get("source") or "").strip()
    if forced:
        forced_kind = classify_source(forced)
        if forced_kind is None:
            log(f"refusing unrecognized or protected source {forced}")
            return []
        items = [(forced, forced_kind, device_name(forced))]
    else:
        items = choose_sources(list_sources())
    opened = []
    for path, kind, name in items:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log(f"open {path}: {e}")
            continue
        info = {
            "path": path,
            "kind": kind,
            "name": name,
            "fd": fd,
            "abs_x": None,
            "abs_y": None,
            "frame": InputFrame(),
        }
        if kind == "abs_pad":
            try:
                info["abs_x"] = _abs_range(fd, ABS_X)
                info["abs_y"] = _abs_range(fd, ABS_Y)
            except OSError as e:
                log(f"rejecting {path}: cannot read absolute ranges: {e}")
                os.close(fd)
                continue
            log(f"abs_pad {path} x={info['abs_x']} y={info['abs_y']}")
        log(f"listening {path} kind={kind} name={name!r}")
        opened.append(info)
    return opened


def run_loop(cfg: dict) -> None:
    touch = TouchClick(int(cfg["touch_x_max"]), int(cfg["touch_y_max"]))
    kb_presence = KeyboardPresence()
    # Suppress virtual keyboard while a BT keyboard is connected (mouse/touch focus).
    kb_presence.sync()
    last_kb_presence_check = time.monotonic()
    cur = None
    if cfg.get("cursor"):
        style = write_cursor_style_file(str(cfg.get("cursor_style", CURSOR_STYLE_DEFAULT)))
        log(f"cursor style={style}")
        cur = CursorPublisher(float(cfg.get("cursor_hz", CURSOR_HZ_DEFAULT)))
    # x,y are always logical (upright UI) cursor coordinates.
    x = float(touch.x)
    y = float(touch.y)
    contact = ContactState()
    right_codes: set[int] = set()
    right_active = False
    right_deadline = 0.0
    primary_anchor: tuple[float, float] | None = None
    primary_start: tuple[float, float] | None = None
    primary_moved = False
    events = 0
    txm = int(cfg["touch_x_max"])
    tym = int(cfg["touch_y_max"])
    orient = UiOrientation(cfg.get("orientation", ORIENTATION_AUTO))
    hide_ms = max(
        0, min(CURSOR_HIDE_MS_MAX, int(cfg.get("cursor_hide_ms", CURSOR_HIDE_MS_DEFAULT)))
    )
    cursor_visible = False
    last_pointer_activity = 0.0

    def pub(visible: bool = True, *, activity: bool = False):
        nonlocal cursor_visible, last_pointer_activity
        if cur:
            # FIFO always receives logical coordinates — never physical.
            cur.publish(x, y, txm, tym, visible=visible)
            cursor_visible = bool(visible)
            if activity:
                last_pointer_activity = time.monotonic()

    def visible_cursor_position() -> tuple[float, float]:
        """Logical cursor position currently shown on the e-ink panel."""
        if cur:
            visual = cur.visual_position(
                txm, tym, int(cfg.get("cursor_lag_ms", CURSOR_LAG_MS_DEFAULT))
            )
            if visual is not None:
                return visual
        return x, y

    def move_touch_to_cursor(
        lx: float, ly: float, *, rotation: int | None = None
    ) -> None:
        """Inject touch at physical coords matching the logical cursor.

        TouchClick.move remains a low-level physical API (CLI tap diagnostics).
        Uses only the cached UI rotation — never journalctl/logread.
        Call only when contact is starting or a drag is active, not on hover.
        """
        rot = (
            orient.get()
            if rotation is None
            else normalize_orientation(rotation)
        )
        nx = float(lx) / float(txm)
        ny = float(ly) / float(tym)
        pnx, pny = logical_to_physical(nx, ny, rot)
        touch.move(pnx * txm, pny * tym)

    if cur:
        pub(visible=False)
    # Log the initial cached value (file seed / fixed); watcher updates later.
    log(f"ui orientation cache={orient.get()} ({orient.source})")

    def finish_right_click() -> None:
        nonlocal right_active, right_deadline
        if not right_active:
            return
        touch.contact(False)
        right_active = False
        right_deadline = 0.0
        move_touch_to_cursor(x, y)
        touch.contact(contact.contact_active)

    def finish_right_click_if_due() -> None:
        if right_active and time.monotonic() >= right_deadline:
            finish_right_click()

    def fail_safe_release(reason: str) -> None:
        nonlocal right_active, right_deadline
        nonlocal primary_anchor, primary_start, primary_moved
        if touch.down:
            touch.contact(False)
        contact.reset()
        right_codes.clear()
        right_active = False
        right_deadline = 0.0
        primary_anchor = None
        primary_start = None
        primary_moved = False
        log(reason)

    def resync_source_keys(src: dict) -> None:
        """Restore a still-held primary/finger key after a dropped evdev frame."""
        nonlocal primary_anchor, primary_start, primary_moved
        wanted = PRIMARY_CLICK | FINGER_CODES
        try:
            held = pressed_key_codes(src["fd"], wanted)
        except OSError as e:
            log(f"key resync failed on {src['path']}: {e}")
            return
        for code in sorted(held & PRIMARY_CLICK):
            contact.on_primary(True, bool(cfg.get("finger_drag")), code=code)
        for code in sorted(held & FINGER_CODES):
            contact.on_finger(True, bool(cfg.get("finger_drag")), code=code)
        if contact.buttons:
            primary_anchor = visible_cursor_position()
            primary_start = (x, y)
            primary_moved = False
            move_touch_to_cursor(*primary_anchor)
        else:
            move_touch_to_cursor(x, y)
        touch.contact(contact.contact_active)
        log(
            f"key state resynced on {src['path']}: "
            f"held={sorted(held)} down={int(touch.down)}"
        )

    def apply_frame(src: dict, frame_data) -> None:
        nonlocal x, y, events, right_active, right_deadline
        nonlocal primary_anchor, primary_start, primary_moved
        rel_x, rel_y, wheel, abs_x, abs_y, key_events = frame_data
        moved = False

        if rel_x or rel_y:
            x, y = clamp_move(cfg, x, y, rel_x, rel_y)
            moved = True
        if src["kind"] == "abs_pad":
            if abs_x is not None and src["abs_x"]:
                x, y = map_abs_position(
                    cfg, x, y, "x", abs_x, src["abs_x"], src["abs_y"]
                )
                moved = True
            if abs_y is not None and src["abs_y"]:
                x, y = map_abs_position(
                    cfg, x, y, "y", abs_y, src["abs_x"], src["abs_y"]
                )
                moved = True

        # Hover only updates logical x,y + the cursor FIFO. Convert to physical
        # touch only while a contact is down (drag) — never on every pointer move.
        if moved:
            if contact.buttons:
                primary_moved = primary_moved or moved_past_drag_threshold(
                    primary_start, x, y
                )
            if (
                not right_active
                and touch.down
                and (not contact.buttons or primary_moved)
            ):
                move_touch_to_cursor(x, y)
            events += 1

        if wheel and cfg.get("scroll_as_swipe"):
            # A synthetic swipe must never release a contact owned by a held
            # primary button or finger-drag gesture.
            if not right_active and not contact.contact_active:
                swipe_x, swipe_y = clamp_move(cfg, x, y, 0, -wheel * 50)
                move_touch_to_cursor(x, y)
                touch.contact(True)
                move_touch_to_cursor(swipe_x, swipe_y)
                touch.contact(False)
                move_touch_to_cursor(x, y)
                events += 1

        for code, value in key_events:
            if code in FINGER_CODES:
                was_finger = bool(contact.finger)
                contact.on_finger(
                    bool(value), bool(cfg.get("finger_drag")), code=code
                )
                if not right_active:
                    # Position before contact starts so finger-drag lands correctly.
                    if (
                        value
                        and not was_finger
                        and cfg.get("finger_drag")
                        and contact.contact_active
                    ):
                        move_touch_to_cursor(x, y)
                    touch.contact(contact.contact_active)
                continue

            if code in PRIMARY_CLICK:
                if value not in (0, 1):
                    continue
                was_primary = bool(contact.buttons)
                if value == 1 and right_active:
                    finish_right_click()
                contact.on_primary(
                    bool(value), bool(cfg.get("finger_drag")), code=code
                )
                if not right_active:
                    if value == 1 and not was_primary:
                        primary_anchor = visible_cursor_position()
                        primary_start = (x, y)
                        primary_moved = False
                        # Uses cached orientation from the background watcher.
                        move_touch_to_cursor(*primary_anchor)
                    elif value == 0 and was_primary and not contact.buttons:
                        # With no held-button motion, release at the same point
                        # where the user saw the cursor when they pressed.
                        if primary_moved:
                            move_touch_to_cursor(x, y)
                        primary_anchor = None
                        primary_start = None
                        primary_moved = False
                    elif primary_moved:
                        move_touch_to_cursor(x, y)
                    touch.contact(contact.contact_active)
                events += 1
                continue

            if code in RIGHT_CLICK:
                was_pressed = bool(right_codes)
                if value == 1:
                    right_codes.add(code)
                    if (
                        not was_pressed
                        and not right_active
                        and not contact.contact_active
                    ):
                        move_touch_to_cursor(x, y)
                        touch.contact(True)
                        right_active = True
                        right_deadline = time.monotonic() + 0.55
                        events += 1
                elif value == 0:
                    right_codes.discard(code)

        if moved or key_events or wheel:
            pub(activity=True)
        if events and events % 200 == 0:
            log(
                f"events~{events} pos={int(x)},{int(y)} "
                f"btn={contact.buttons} down={int(touch.down)} "
                f"orient={orient.get()}"
            )

    try:
        while True:
            sources = open_sources(cfg)
            if not sources:
                if cur:
                    pub(visible=False)
                # Still track BT keyboard while waiting for a pointer node.
                kb_presence.sync()
                time.sleep(1.0)
                continue
            # Re-evaluate on every (re)open of pointer sources (connect/wake).
            kb_presence.sync()
            # Show on connect. With cursor_hide_ms=0 the crosshair stays for the
            # whole time a pointer node is open, including idle; a positive value
            # auto-hides after that many idle milliseconds.
            pub(visible=True, activity=True)
            fds = {s["fd"]: s for s in sources}
            try:
                while True:
                    finish_right_click_if_due()
                    timeout = 1.0
                    if right_active:
                        timeout = max(
                            0.0,
                            min(timeout, right_deadline - time.monotonic()),
                        )
                    if hide_ms > 0 and cursor_visible:
                        remaining = (hide_ms / 1000.0) - (
                            time.monotonic() - last_pointer_activity
                        )
                        timeout = max(0.0, min(timeout, remaining))
                    r, _, _ = select.select(list(fds.keys()), [], [], timeout)
                    finish_right_click_if_due()
                    if not r:
                        if any(not os.path.exists(s["path"]) for s in sources):
                            log("source disappeared")
                            break
                        if set(p for p, _, _ in choose_sources(list_sources())) != {
                            s["path"] for s in sources
                        }:
                            log("source set changed")
                            break
                        # Re-check BT keyboard attachment a few times a second.
                        now = time.monotonic()
                        if now - last_kb_presence_check >= 0.5:
                            last_kb_presence_check = now
                            kb_presence.sync()
                        if (
                            hide_ms > 0
                            and cursor_visible
                            and (time.monotonic() - last_pointer_activity)
                            >= (hide_ms / 1000.0)
                        ):
                            pub(visible=False)
                        continue

                    dead = False
                    for fd in r:
                        src = fds[fd]
                        # A high-report-rate Bluetooth touchpad can fill the
                        # per-client evdev ring between select() calls. Drain
                        # bounded batches through EAGAIN instead of reading one
                        # small chunk and returning to the outer loop.
                        batches = 0
                        while batches < 8:
                            try:
                                data = os.read(fd, EVENT_SIZE * 256)
                            except OSError as e:
                                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                                    break
                                log(f"read {src['path']}: {e}")
                                dead = True
                                break
                            if not data:
                                dead = True
                                break
                            batches += 1

                            for off in range(
                                0, len(data) - EVENT_SIZE + 1, EVENT_SIZE
                            ):
                                _, _, etype, code, value = struct.unpack_from(
                                    EVENT_FMT, data, off
                                )
                                result = src["frame"].feed(etype, code, value)
                                if result is None:
                                    continue
                                status, frame_data = result
                                if status == "dropped":
                                    fail_safe_release(
                                        f"SYN_DROPPED on {src['path']}; "
                                        "released contact"
                                    )
                                elif status == "recovered":
                                    resync_source_keys(src)
                                elif status == "frame":
                                    apply_frame(src, frame_data)
                        if dead:
                            break
                    if dead:
                        break
            finally:
                for s in sources:
                    try:
                        os.close(s["fd"])
                    except OSError:
                        pass
                fail_safe_release("source closed")
                pub(visible=False)
                time.sleep(0.25)
    finally:
        orient.close()
        if cur:
            cur.close()
        kb_presence.close()
        touch.close()


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
