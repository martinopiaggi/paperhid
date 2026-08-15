"""Host CLI ASCII logo (filled-block PAPERHID wordmark)."""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from host_cli.banner import (
    LOGO_ASCII,
    LOGO_NARROW,
    LOGO_WIDE,
    TAGLINE,
    color_enabled,
    print_banner,
    render_banner,
)


class TestRenderBanner(unittest.TestCase):
    def test_wide_plain_contains_wordmark_and_tagline(self):
        text = render_banner(color=False, width=80, unicode=True)
        self.assertIn(LOGO_WIDE[0], text)
        self.assertIn(TAGLINE, text)
        self.assertIn("PaperHid", text)
        self.assertNotIn("\x1b[", text)
        self.assertTrue(text.startswith("\n"))
        self.assertTrue(text.endswith("\n\n"))

    def test_narrow_uses_compact_art(self):
        text = render_banner(color=False, width=40, unicode=True)
        self.assertIn(LOGO_NARROW[0], text)
        self.assertNotIn(LOGO_WIDE[0], text)
        self.assertIn(TAGLINE, text)

    def test_ascii_fallback(self):
        text = render_banner(color=False, unicode=False)
        self.assertIn(LOGO_ASCII[0], text)
        self.assertIn("PaperHid", text)
        self.assertNotIn("█", text)

    def test_color_paints_truecolor_escapes(self):
        text = render_banner(color=True, width=80, unicode=True)
        self.assertIn("\x1b[38;2;", text)
        self.assertIn("\x1b[0m", text)
        self.assertIn(TAGLINE, text.replace("\x1b[2m", "").replace("\x1b[0m", ""))

    def test_print_banner_writes_stdout(self):
        buf = io.StringIO()
        print_banner(file=buf, color=False, width=80)
        self.assertIn("██████", buf.getvalue())
        self.assertIn("PaperHid", buf.getvalue())


class TestColorEnabled(unittest.TestCase):
    def test_override_wins(self):
        self.assertTrue(color_enabled(io.StringIO(), override=True))
        self.assertFalse(color_enabled(io.StringIO(), override=False))

    def test_no_color_env(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            self.assertFalse(color_enabled(io.StringIO()))

    def test_force_color_env(self):
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        env["FORCE_COLOR"] = "1"
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(color_enabled(io.StringIO()))

    def test_non_tty_default_off(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("NO_COLOR", "FORCE_COLOR")
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(color_enabled(io.StringIO()))


if __name__ == "__main__":
    unittest.main()
