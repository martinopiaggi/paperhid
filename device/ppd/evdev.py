"""Evdev discovery, classification, and keyboard-presence uinput."""
from __future__ import annotations

import array
import glob
import os
import struct
import time

from .constants import (
    ABS_X,
    ABS_Y,
    BTN_0,
    BTN_LEFT,
    BTN_TOOL_FINGER,
    BTN_TOUCH,
    BUS_VIRTUAL,
    EV_ABS,
    EV_KEY,
    EV_REL,
    EV_SYN,
    FINGER_CODES,
    IGNORE_NAME_SUBSTR,
    KEY_BITMAP_BYTES,
    PRIMARY_CLICK,
    REL_X,
    REL_Y,
    RM_KEYBOARD_NAME,
    UI_DEV_CREATE,
    UI_DEV_DESTROY,
    UI_SET_EVBIT,
    UI_SET_KEYBIT,
    UINPUT_PATHS,
    _PRESENCE_KEY_CODES,
    fcntl,
)
from .logutil import log


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
