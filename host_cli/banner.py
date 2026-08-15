"""PaperHid terminal logo (filled block letters, Claude/Gemini CLI style)."""
from __future__ import annotations

import os
import shutil
import sys
from typing import IO, Sequence

# ANSI Shadow — "PAPERHID" (59 cols). Famous filled-block CLI wordmark.
LOGO_WIDE: tuple[str, ...] = (
    "██████╗  █████╗ ██████╗ ███████╗██████╗ ██╗  ██╗██╗██████╗ ",
    "██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗██║  ██║██║██╔══██╗",
    "██████╔╝███████║██████╔╝█████╗  ██████╔╝███████║██║██║  ██║",
    "██╔═══╝ ██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗██╔══██║██║██║  ██║",
    "██║     ██║  ██║██║     ███████╗██║  ██║██║  ██║██║██████╔╝",
    "╚═╝     ╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═════╝ ",
)

# Calvin S — narrow terminals.
LOGO_NARROW: tuple[str, ...] = (
    "╔═╗╔═╗╔═╗╔═╗╦═╗╦ ╦╦╔╦╗",
    "╠═╝╠═╣╠═╝╠╣ ╠╦╝╠═╣║ ║║",
    "╩  ╩ ╩╩  ╚═╝╩╚═╩ ╩╩═╩╝",
)

# Pure ASCII when the console cannot encode box-drawing / block chars.
LOGO_ASCII: tuple[str, ...] = (
    r" ____   _    ____  _____ ____  _   _ ___ ____  ",
    r"|  _ \ / \  |  _ \| ____|  _ \| | | |_ _|  _ \ ",
    r"| |_) / _ \ | |_) |  _| | |_) | |_| || || | | |",
    r"|  __/ ___ \|  __/| |___|  _ <|  _  || || |_| |",
    r"|_| /_/   \_\_|   |_____|_| \_\_| |_|___|____/ ",
)

TAGLINE = "PaperHid · Bluetooth keyboard + mouse for reMarkable Paper Pro"

# Kraft paper → gold → sepia ink (readable on typical dark terminals).
_PALETTE: tuple[tuple[int, int, int], ...] = (
    (255, 236, 186),
    (245, 196, 110),
    (232, 160, 80),
    (212, 132, 64),
)

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_WIDE_MIN_COLS = 64


def _enable_windows_vt() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _ensure_utf8(stream: IO[str]) -> None:
    reconf = getattr(stream, "reconfigure", None)
    if reconf is None:
        return
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if enc in ("utf8", "utf8sig"):
        return
    try:
        reconf(encoding="utf-8", errors="replace")
    except Exception:
        pass


def color_enabled(stream: IO[str] | None = None, *, override: bool | None = None) -> bool:
    """Honor NO_COLOR / FORCE_COLOR / TERM=dumb; otherwise require a TTY."""
    if override is not None:
        return bool(override)
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    target = stream if stream is not None else sys.stdout
    return bool(getattr(target, "isatty", lambda: False)())


def _supports_unicode(stream: IO[str] | None) -> bool:
    enc = (getattr(stream, "encoding", None) if stream is not None else None) or "utf-8"
    try:
        LOGO_WIDE[0].encode(enc)
        return True
    except (LookupError, UnicodeEncodeError, TypeError):
        return False


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _color_at(t: float) -> tuple[int, int, int]:
    if t <= 0:
        return _PALETTE[0]
    if t >= 1:
        return _PALETTE[-1]
    n = len(_PALETTE) - 1
    x = t * n
    i = min(int(x), n - 1)
    f = x - i
    c0, c1 = _PALETTE[i], _PALETTE[i + 1]
    return (_lerp(c0[0], c1[0], f), _lerp(c0[1], c1[1], f), _lerp(c0[2], c1[2], f))


def _paint_line(line: str) -> str:
    width = max(len(line), 1)
    parts: list[str] = []
    last: tuple[int, int, int] | None = None
    for i, ch in enumerate(line):
        if ch == " ":
            if last is not None:
                parts.append(_RESET)
                last = None
            parts.append(ch)
            continue
        rgb = _color_at(i / (width - 1) if width > 1 else 0.0)
        if rgb != last:
            parts.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m")
            last = rgb
        parts.append(ch)
    if last is not None:
        parts.append(_RESET)
    return "".join(parts)


def _center(text: str, width: int) -> str:
    if width <= len(text):
        return text
    return text.center(width).rstrip()


def _select_art(
    *,
    width: int,
    unicode: bool,
) -> Sequence[str]:
    if not unicode:
        return LOGO_ASCII
    if width < _WIDE_MIN_COLS:
        return LOGO_NARROW
    return LOGO_WIDE


def render_banner(
    *,
    color: bool | None = None,
    width: int | None = None,
    stream: IO[str] | None = None,
    unicode: bool | None = None,
) -> str:
    """Return the logo + tagline, with a trailing newline after the block."""
    target = stream if stream is not None else sys.stdout
    cols = width if width is not None else shutil.get_terminal_size((80, 24)).columns
    use_unicode = _supports_unicode(target) if unicode is None else unicode
    art = _select_art(width=cols, unicode=use_unicode)
    use_color = color_enabled(target, override=color)
    art_width = max(len(row) for row in art)
    painted = [_paint_line(row) if use_color else row for row in art]
    tag = _center(TAGLINE, art_width)
    if use_color:
        tag = f"{_DIM}{tag}{_RESET}"
    return "\n" + "\n".join(painted) + "\n" + tag + "\n\n"


def print_banner(
    *,
    file: IO[str] | None = None,
    color: bool | None = None,
    width: int | None = None,
) -> None:
    """Write the logo to *file* (stdout by default)."""
    stream = file if file is not None else sys.stdout
    _enable_windows_vt()
    _ensure_utf8(stream)
    text = render_banner(color=color, width=width, stream=stream)
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        stream.write(
            render_banner(color=False, width=width, stream=stream, unicode=False)
        )
        stream.flush()
