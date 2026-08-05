#!/usr/bin/env python3
"""Draw a software cursor into xochitl's framebuffer (Paper Pro / XOVI spy).

Requires XOVI + framebuffer-spy + xovi-message-broker.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import select
import struct
import sys
import time

MB_IN = "/run/xovi-mb"
MB_OUT = "/run/xovi-mb-out"
LOG = "/home/root/.paperpointer/pointer.log"
CFG_CACHE = "/home/root/.paperpointer/fb.cfg"

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


class IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


libc.process_vm_readv.restype = ctypes.c_ssize_t
libc.process_vm_readv.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.c_ulong,
]
libc.process_vm_writev.restype = ctypes.c_ssize_t
libc.process_vm_writev.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.POINTER(IOVec),
    ctypes.c_ulong,
    ctypes.c_ulong,
]


def log(msg: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] fb: {msg}\n")
    except OSError:
        pass


def xochitl_pid() -> int:
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmd = open(f"/proc/{name}/cmdline", "rb").read().replace(b"\0", b" ")
        except OSError:
            continue
        if cmd.startswith(b"/usr/bin/xochitl"):
            return int(name)
    raise RuntimeError("xochitl not running")


def mb_call(signal: str, value: str = "", timeout: float = 2.5) -> str:
    """Call a native XOVI message-broker export. Avoid FIFO open deadlocks."""
    if not os.path.exists(MB_IN):
        raise RuntimeError("no /run/xovi-mb")

    # Open OUT first (nonblock), then IN.
    try:
        rfd = os.open(MB_OUT, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        raise RuntimeError(f"open mb-out: {e}") from e

    # drain
    try:
        while True:
            if not select.select([rfd], [], [], 0)[0]:
                break
            if not os.read(rfd, 8192):
                break
    except OSError:
        pass

    pkt = f">e{signal}:{value}\n".encode()
    try:
        # O_NONBLOCK write — broker must already be reading
        wfd = os.open(MB_IN, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        os.close(rfd)
        raise RuntimeError(f"open mb-in: {e}") from e
    try:
        os.write(wfd, pkt)
    finally:
        os.close(wfd)

    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        ready, _, _ = select.select([rfd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(rfd, 4096)
        except OSError:
            continue
        if not chunk:
            continue
        buf += chunk
        # config is one line / one blob
        if len(buf) >= 10:
            break
    os.close(rfd)
    return buf.decode("utf-8", "replace").strip()


def parse_cfg(raw: str) -> dict:
    m = re.search(r"(0x[0-9a-fA-F]+),(\d+),(\d+),(\d+),(\d+),(\d+)", raw)
    if not m:
        raise RuntimeError(f"bad fb config: {raw!r}")
    addr_s, w, h, typ, bpl, reload = m.groups()
    return {
        "addr": int(addr_s, 16),
        "width": int(w),
        "height": int(h),
        "type": int(typ),
        "bpl": int(bpl),
        "requires_reload": int(reload),
        "raw": m.group(0),
    }


def get_fb_config() -> dict:
    raw = mb_call("framebuffer-spy$getConfigString")
    cfg = parse_cfg(raw)
    try:
        with open(CFG_CACHE, "w", encoding="utf-8") as f:
            f.write(cfg["raw"] + "\n")
    except OSError:
        pass
    return cfg


def get_fb_config_cached() -> dict:
    try:
        return get_fb_config()
    except Exception as e:
        log(f"mb config failed: {e}")
        if os.path.isfile(CFG_CACHE):
            raw = open(CFG_CACHE, encoding="utf-8").read().strip()
            return parse_cfg(raw)
        raise


def _vm_rw(pid: int, addr: int, data: bytes | None, length: int, write: bool) -> bytes:
    buf = ctypes.create_string_buffer(length)
    if write:
        if data is None or len(data) < length:
            raise ValueError("short write buffer")
        ctypes.memmove(buf, data, length)
    local = IOVec(
        iov_base=ctypes.cast(buf, ctypes.c_void_p),
        iov_len=length,
    )
    remote = IOVec(iov_base=ctypes.c_void_p(addr), iov_len=length)
    li = (IOVec * 1)(local)
    ri = (IOVec * 1)(remote)
    if write:
        n = libc.process_vm_writev(pid, li, 1, ri, 1, 0)
    else:
        n = libc.process_vm_readv(pid, li, 1, ri, 1, 0)
    if n < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"process_vm_{'write' if write else 'read'}v errno={err}")
    if n != length:
        raise OSError(f"short vm transfer {n}/{length}")
    if write:
        return b""
    return bytes(buf.raw[:length])


def read_rect(pid: int, cfg: dict, x: int, y: int, w: int, h: int) -> bytes:
    bpl = cfg["bpl"]
    base = cfg["addr"]
    out = bytearray()
    for row in range(h):
        addr = base + (y + row) * bpl + x * 4
        out += _vm_rw(pid, addr, None, w * 4, write=False)
    return bytes(out)


def write_rect(pid: int, cfg: dict, x: int, y: int, w: int, h: int, rgba: bytes) -> None:
    bpl = cfg["bpl"]
    base = cfg["addr"]
    stride = w * 4
    for row in range(h):
        addr = base + (y + row) * bpl + x * 4
        chunk = rgba[row * stride : (row + 1) * stride]
        _vm_rw(pid, addr, chunk, len(chunk), write=True)


def make_cross(size: int = 29, t: int = 2) -> tuple[bytes, int]:
    if size % 2 == 0:
        size += 1
    mid = size // 2
    pix = bytearray(size * size * 4)

    def put(x, y, r, g, b, a=255):
        if 0 <= x < size and 0 <= y < size:
            i = (y * size + x) * 4
            pix[i : i + 4] = bytes((r, g, b, a))

    # white halo
    for y in range(size):
        for x in range(size):
            if abs(x - mid) <= t + 2 and abs(y - mid) <= mid:
                put(x, y, 255, 255, 255)
            if abs(y - mid) <= t + 2 and abs(x - mid) <= mid:
                put(x, y, 255, 255, 255)
    # black core
    for y in range(size):
        for x in range(size):
            if abs(x - mid) <= t and abs(y - mid) <= mid:
                put(x, y, 0, 0, 0)
            if abs(y - mid) <= t and abs(x - mid) <= mid:
                put(x, y, 0, 0, 0)
    return bytes(pix), size


class FBCursor:
    def __init__(self):
        self.pid = xochitl_pid()
        self.cfg = get_fb_config_cached()
        self.sprite, self.size = make_cross(31, 2)
        self.half = self.size // 2
        self._saved = None
        self._sx = self._sy = 0
        self._visible = False
        self._last = 0.0
        self._min_dt = 1 / 10
        log(f"fb cursor pid={self.pid} {self.cfg['raw']}")

    def refresh(self) -> None:
        self.pid = xochitl_pid()
        self.cfg = get_fb_config_cached()

    def _tl(self, cx: int, cy: int) -> tuple[int, int]:
        w, h = self.cfg["width"], self.cfg["height"]
        return (
            max(0, min(w - self.size, cx - self.half)),
            max(0, min(h - self.size, cy - self.half)),
        )

    def hide(self) -> None:
        if not self._visible or self._saved is None:
            return
        try:
            write_rect(
                self.pid, self.cfg, self._sx, self._sy, self.size, self.size, self._saved
            )
        except OSError as e:
            log(f"hide: {e}")
        self._visible = False
        self._saved = None

    def show_at_display(self, dx: int, dy: int, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last) < self._min_dt:
            return
        self._last = now
        w, h = self.cfg["width"], self.cfg["height"]
        dx = max(0, min(w - 1, int(dx)))
        dy = max(0, min(h - 1, int(dy)))
        x, y = self._tl(dx, dy)
        try:
            if self._visible and self._saved is not None:
                write_rect(
                    self.pid,
                    self.cfg,
                    self._sx,
                    self._sy,
                    self.size,
                    self.size,
                    self._saved,
                )
            self._saved = read_rect(self.pid, self.cfg, x, y, self.size, self.size)
            # composite: only draw opaque sprite pixels
            composed = bytearray(self._saved)
            sp = self.sprite
            for i in range(0, len(sp), 4):
                if sp[i + 3] > 0:
                    composed[i : i + 4] = sp[i : i + 4]
            write_rect(self.pid, self.cfg, x, y, self.size, self.size, bytes(composed))
            self._sx, self._sy = x, y
            self._visible = True
        except OSError as e:
            log(f"show: {e}")
            self._visible = False
            self._saved = None
            try:
                self.refresh()
            except Exception as e2:
                log(f"refresh: {e2}")


def touch_to_display(
    tx: float, ty: float, txm: int = 2064, tym: int = 2832, dw: int = 1620, dh: int = 2160
) -> tuple[int, int]:
    return int(tx / max(1, txm) * dw), int(ty / max(1, tym) * dh)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: fb_cursor.py config|demo|dot X Y")
        return 1
    cmd = argv[1]
    if cmd == "config":
        cfg = get_fb_config()
        print(cfg["raw"])
        print(cfg)
        return 0
    if cmd == "demo":
        cur = FBCursor()
        print("config", cur.cfg["raw"])
        w, h = cur.cfg["width"], cur.cfg["height"]
        print("sweep")
        for i in range(40):
            cur.show_at_display(int((w - 1) * i / 39), h // 2, force=True)
            time.sleep(0.08)
        time.sleep(0.4)
        cur.hide()
        print("done")
        return 0
    if cmd == "dot":
        cur = FBCursor()
        cur.show_at_display(int(argv[2]), int(argv[3]), force=True)
        print("ok")
        return 0
    print("unknown")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
