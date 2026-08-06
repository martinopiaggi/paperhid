"""Cursor FIFO publisher and UI broker helpers."""
from __future__ import annotations

import errno
import math
import os
import threading
import time
from collections import deque

from .constants import (
    CURSOR_BROKER_IN,
    CURSOR_BROKER_OUT,
    CURSOR_HZ_DEFAULT,
    CURSOR_HZ_MAX,
    CURSOR_LAG_MS_MAX,
    CURSOR_PIPE,
    select,
)
from .logutil import log


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

