#!/usr/bin/env python3
"""On-device keyboard layout applicator (allow-listed keys only).

Shipped to /home/root/.paperhid/ and invoked by paperhid-ui set-layout.
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


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print('{"ok":false,"error":"layout key required"}')
        return 2
    key = args[0].strip().lower().replace(" ", "_")
    if key not in _ALLOWED:
        print(f'{{"ok":false,"error":"invalid layout {key}"}}')
        return 2
    try:
        from shared.transport import LocalTransport
        from shared import layout_patcher
    except ImportError as e:
        print(f'{{"ok":false,"error":"layout modules missing: {e}"}}')
        return 10
    try:
        layout_patcher.apply_layout(
            LocalTransport(), key, status_cb=None, restart_ui=True
        )
    except Exception as e:
        msg = str(e).replace('"', "'")[:200]
        print(f'{{"ok":false,"error":"{msg}"}}')
        return 1
    print(f'{{"ok":true,"action":"set-layout","layout":"{key}"}}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
