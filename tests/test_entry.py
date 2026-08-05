"""Entry-point checks that import the shipped daemon without live hardware."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAEMON_PATH = ROOT / "device" / "paperpointerd.py"
SHIPPED_CONF = ROOT / "device" / "pointer.conf"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("paperpointerd_entry", DAEMON_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paperpointerd_entry"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestShippedEntry(unittest.TestCase):
    def test_default_conf_entry(self):
        pp = _load_daemon()
        cfg = pp.default_conf()
        self.assertIsInstance(cfg, dict)
        self.assertEqual(cfg["touch_x_max"], pp.TOUCH_X_MAX)
        self.assertEqual(cfg["touch_y_max"], pp.TOUCH_Y_MAX)
        self.assertEqual(cfg["cursor"], 0)
        self.assertEqual(cfg["cursor_hz"], pp.CURSOR_HZ_DEFAULT)
        self.assertEqual(cfg["cursor_lag_ms"], pp.CURSOR_LAG_MS_DEFAULT)
        self.assertEqual(cfg["cursor_hide_ms"], pp.CURSOR_HIDE_MS_DEFAULT)
        self.assertEqual(cfg["cursor_style"], pp.CURSOR_STYLE_DEFAULT)

    def test_load_shipped_pointer_conf(self):
        pp = _load_daemon()
        self.assertTrue(SHIPPED_CONF.is_file(), "device/pointer.conf must exist")
        cfg = pp.load_conf(str(SHIPPED_CONF))
        self.assertEqual(cfg["cursor"], 0)
        self.assertEqual(cfg["cursor_hz"], pp.CURSOR_HZ_DEFAULT)
        self.assertEqual(cfg["cursor_lag_ms"], pp.CURSOR_LAG_MS_DEFAULT)
        self.assertEqual(cfg["cursor_hide_ms"], pp.CURSOR_HIDE_MS_DEFAULT)
        self.assertEqual(cfg["cursor_style"], pp.CURSOR_STYLE_DEFAULT)
        self.assertEqual(cfg["touch_x_max"], 2064)
        self.assertEqual(cfg["touch_y_max"], 2832)
        self.assertGreaterEqual(cfg["accel"], 1.0)

    def test_classifier_fixture_print_contract(self):
        """Concrete return: ignored stock names are not pointer sources."""
        pp = _load_daemon()
        result = pp.classify_from_caps(
            "Elan marker input",
            has_abs_xy=True,
            has_btn_touch=True,
            has_btn_left=True,
        )
        self.assertIsNone(result)
        pad = pp.classify_from_caps(
            "Touchpad",
            has_abs_xy=True,
            has_btn_tool_finger=True,
        )
        self.assertEqual(pad, "abs_pad")


if __name__ == "__main__":
    unittest.main()
