"""Single keyboard-layout registry (display name → qmap key).

Used by the desktop UI and the on-device native app backend.
"""
from __future__ import annotations

# (display label, layout key under resources/keymaps/)
KEYBOARD_LAYOUTS: list[tuple[str, str]] = [
    ("US English", "us"),
    ("US International", "us_intl"),
    ("UK English", "uk"),
    ("German", "de"),
    ("French", "fr"),
    ("Canadian French", "fr_ca"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Brazilian", "br"),
    ("Dutch", "nl"),
    ("Swedish", "sv"),
    ("Norwegian", "no"),
    ("Danish", "dk"),
    ("Finnish", "fi"),
    ("Icelandic", "is"),
    ("Swiss German", "de_ch"),
    ("Swiss French", "fr_ch"),
    ("Belgian", "be"),
    ("Russian", "ru"),
    ("Ukrainian", "ua"),
    ("Czech", "cz"),
    ("Hungarian", "hu"),
    ("Turkish", "tr"),
    ("Greek", "gr"),
    ("Hebrew", "he"),
]

LAYOUT_NAMES = [name for name, _ in KEYBOARD_LAYOUTS]
LAYOUT_MAP = dict(KEYBOARD_LAYOUTS)
