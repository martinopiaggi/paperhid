"""UI orientation parsing and cached watcher."""
from __future__ import annotations

import math
import os
import re
import subprocess
import threading

from .constants import (
    HOME,
    ORIENTATION_AUTO,
    UI_ORIENTATION_PATH,
    VALID_ORIENTATIONS,
)
from .logutil import log


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

