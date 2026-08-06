"""Synthetic touch injection and input-frame helpers."""
from __future__ import annotations

import math
import os
import struct
import time

from .constants import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_PRESSURE,
    ABS_MT_SLOT,
    ABS_MT_TOUCH_MAJOR,
    ABS_MT_TRACKING_ID,
    ABS_X,
    ABS_Y,
    BTN_LEFT,
    BTN_TOOL_FINGER,
    BTN_TOUCH,
    CLICK_DRAG_THRESHOLD,
    EVENT_FMT,
    EV_ABS,
    EV_KEY,
    EV_REL,
    EV_SYN,
    INPUT_PROP_DIRECT,
    REL_WHEEL,
    REL_X,
    REL_Y,
    SYN_DROPPED,
    SYN_REPORT,
    UI_DEV_CREATE,
    UI_DEV_DESTROY,
    UI_SET_ABSBIT,
    UI_SET_EVBIT,
    UI_SET_KEYBIT,
    UI_SET_PROPBIT,
    fcntl,
)
from .evdev import (
    _uinput_create,
    _uinput_open,
    classify_source,
    choose_sources,
    device_name,
    list_sources,
)
from .logutil import log


def _ev(fd: int, etype: int, code: int, value: int) -> None:
    os.write(fd, struct.pack(EVENT_FMT, 0, 0, etype, code, int(value)))


def _sync(fd: int) -> None:
    _ev(fd, EV_SYN, SYN_REPORT, 0)


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

