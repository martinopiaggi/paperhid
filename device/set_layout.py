#!/usr/bin/env python3
"""On-device keyboard layout applicator (allow-listed keys only).

Shipped to /home/root/.paperhid/ and invoked by paperhid-ui set-layout.

Hardening:
  - Restarts the UI via XOVI when available so Settings → Help stays patched.
  - On failure, restores the libepaper backup and restarts the display.
  - Never depends on a connected Bluetooth keyboard.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Package root: /home/root/.paperhid/py  (shared/, tools/)
_PY_ROOT = Path(__file__).resolve().parent / "py"
if str(_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(_PY_ROOT))

# Layout keys must match paperhid-ui allow-list + shared.layouts
_ALLOWED = frozenset(
    {
        "us",
        "us_intl",
        "uk",
        "de",
        "fr",
        "fr_ca",
        "es",
        "it",
        "pt",
        "br",
        "nl",
        "sv",
        "no",
        "dk",
        "fi",
        "is",
        "de_ch",
        "fr_ch",
        "be",
        "ru",
        "ua",
        "cz",
        "hu",
        "tr",
        "gr",
        "he",
    }
)

_SETTINGS_QMD = (
    "/home/root/xovi/exthome/qt-resource-rebuilder/paperpointer-settings.qmd"
)
_SETTINGS_SRC = "/home/root/.paperpointer/paperpointer-settings.qmd"


def _json_err(msg: str) -> str:
    safe = str(msg).replace("\\", "\\\\").replace('"', "'")[:200]
    return f'{{"ok":false,"error":"{safe}"}}'


def _ensure_settings_qmd_present(t) -> None:
    """If Settings QMD vanished from the active dir, restore from staged copy."""
    try:
        if t.exists(_SETTINGS_QMD):
            return
        if t.exists(_SETTINGS_SRC):
            t.run(
                f"mkdir -p /home/root/xovi/exthome/qt-resource-rebuilder; "
                f"cp -p {_SETTINGS_SRC} {_SETTINGS_QMD}; "
                f"chmod 0644 {_SETTINGS_QMD}",
                timeout=15,
            )
    except Exception:
        pass


def _safe_restart(t) -> None:
    from shared import layout_patcher

    try:
        _ensure_settings_qmd_present(t)
        layout_patcher.restart_display(t)
    except Exception:
        try:
            t.run(
                "systemctl reset-failed xochitl.service 2>/dev/null || true; "
                "systemctl start xochitl",
                timeout=30,
            )
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(_json_err("layout key required"))
        return 2
    key = args[0].strip().lower().replace(" ", "_")
    if key not in _ALLOWED:
        print(_json_err(f"invalid layout {key}"))
        return 2
    try:
        from shared.transport import LocalTransport
        from shared import layout_patcher
    except ImportError as e:
        print(_json_err(f"layout modules missing: {e}"))
        return 10

    t = LocalTransport()
    try:
        layout_patcher.apply_layout(t, key, status_cb=None, restart_ui=True)
        _ensure_settings_qmd_present(t)
        # If apply_layout already started UI via XOVI, ensure QMD was restored
        # before that start when missing; re-start only if still no broker.
        _, _, code = t.run("test -p /run/xovi-mb", timeout=5)
        if code != 0 and layout_patcher._xovi_available(t):
            layout_patcher.restart_display(t)
    except Exception as e:
        # Never leave the tablet with a half-applied library or dead UI.
        try:
            layout_patcher.restore_original(t, restart_ui=False)
        except Exception:
            pass
        _safe_restart(t)
        print(_json_err(e))
        return 1

    print(f'{{"ok":true,"action":"set-layout","layout":"{key}"}}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
