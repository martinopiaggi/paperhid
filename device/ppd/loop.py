"""Pointer daemon runtime loop."""
from __future__ import annotations

import errno
import os
import struct
import time

from .config import write_cursor_style_file
from .constants import (
    CURSOR_HIDE_MS_DEFAULT,
    CURSOR_HIDE_MS_MAX,
    CURSOR_HZ_DEFAULT,
    CURSOR_LAG_MS_DEFAULT,
    CURSOR_STYLE_DEFAULT,
    EVENT_SIZE,
    EVENT_FMT,
    FINGER_CODES,
    ORIENTATION_AUTO,
    PRIMARY_CLICK,
    RIGHT_CLICK,
    select,
)
from .cursor import CursorPublisher
from .evdev import (
    KeyboardPresence,
    choose_sources,
    list_sources,
    pressed_key_codes,
)
from .logutil import log
from .orientation import UiOrientation, logical_to_physical, normalize_orientation
from .touch import (
    ContactState,
    TouchClick,
    clamp_move,
    map_abs_position,
    moved_past_drag_threshold,
    open_sources,
)


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

