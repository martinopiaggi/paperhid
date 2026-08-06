"""Unit tests for shipped paperpointerd pure logic (PC / Windows safe).

Drives real functions from device/paperpointerd.py — not a reimplementation.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path  # noqa: F401 — used by style-file test
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DAEMON_PATH = ROOT / "device" / "paperpointerd.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("paperpointerd", DAEMON_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paperpointerd"] = mod
    spec.loader.exec_module(mod)
    return mod


pp = _load_daemon()


class TestDefaultConf(unittest.TestCase):
    def test_defaults_elan_touch_ranges_cursor_off(self):
        cfg = pp.default_conf()
        self.assertEqual(cfg["touch_x_max"], 2064)
        self.assertEqual(cfg["touch_y_max"], 2832)
        self.assertEqual(cfg["cursor"], 0)
        self.assertEqual(cfg["cursor_hz"], pp.CURSOR_HZ_DEFAULT)
        self.assertEqual(cfg["cursor_lag_ms"], pp.CURSOR_LAG_MS_DEFAULT)
        self.assertEqual(cfg["cursor_hide_ms"], pp.CURSOR_HIDE_MS_DEFAULT)
        self.assertEqual(cfg["cursor_style"], pp.CURSOR_STYLE_DEFAULT)
        self.assertEqual(cfg["finger_drag"], 0)
        self.assertEqual(cfg["orientation"], pp.ORIENTATION_AUTO)

    def test_load_conf_from_fixture(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write("accel=3.5\ncursor=0\ntouch_x_max=2064\n")
            path = f.name
        try:
            cfg = pp.load_conf(path)
            self.assertEqual(cfg["accel"], 3.5)
            self.assertEqual(cfg["cursor"], 0)
            self.assertEqual(cfg["touch_x_max"], 2064)
            self.assertEqual(cfg["touch_y_max"], 2832)  # default retained
        finally:
            os.unlink(path)

    def test_malformed_numeric_values_keep_defaults_and_continue(self):
        defaults = pp.default_conf()
        with tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "accel=not-a-number\n"
                "cursor=definitely\n"
                "cursor_hz=fast\n"
                "cursor_lag_ms=late\n"
                "touch_x_max=broken\n"
                "source=/dev/input/event9\n"
            )
            path = f.name
        try:
            cfg = pp.load_conf(path)
            for key in (
                "accel",
                "cursor",
                "cursor_hz",
                "cursor_lag_ms",
                "touch_x_max",
            ):
                self.assertEqual(cfg[key], defaults[key], key)
            self.assertEqual(cfg["source"], "/dev/input/event9")
        finally:
            os.unlink(path)

    def test_non_finite_float_config_keeps_default(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write("accel=nan\n")
            path = f.name
        try:
            cfg = pp.load_conf(path)
            self.assertEqual(cfg["accel"], pp.default_conf()["accel"])
        finally:
            os.unlink(path)

    def test_cursor_rate_is_bounded_but_accepts_fast_mode(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write(f"cursor_hz={pp.CURSOR_HZ_MAX}\n")
            accepted_path = f.name
        with tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write(f"cursor_hz={pp.CURSOR_HZ_MAX + 1}\n")
            rejected_path = f.name
        try:
            self.assertEqual(
                pp.load_conf(accepted_path)["cursor_hz"], pp.CURSOR_HZ_MAX
            )
            self.assertEqual(
                pp.load_conf(rejected_path)["cursor_hz"],
                pp.CURSOR_HZ_DEFAULT,
            )
        finally:
            os.unlink(accepted_path)
            os.unlink(rejected_path)

    def test_cursor_lag_is_bounded(self):
        for value, expected in (
            (0, 0),
            (pp.CURSOR_LAG_MS_MAX, pp.CURSOR_LAG_MS_MAX),
            (-1, pp.CURSOR_LAG_MS_DEFAULT),
            (pp.CURSOR_LAG_MS_MAX + 1, pp.CURSOR_LAG_MS_DEFAULT),
        ):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".conf", delete=False, encoding="utf-8"
                ) as f:
                    f.write(f"cursor_lag_ms={value}\n")
                    path = f.name
                try:
                    self.assertEqual(pp.load_conf(path)["cursor_lag_ms"], expected)
                finally:
                    os.unlink(path)

    def test_cursor_hide_ms_is_bounded_default_off(self):
        for value, expected in (
            (0, 0),
            (3000, 3000),
            (pp.CURSOR_HIDE_MS_MAX, pp.CURSOR_HIDE_MS_MAX),
            (-1, pp.CURSOR_HIDE_MS_DEFAULT),
            (pp.CURSOR_HIDE_MS_MAX + 1, pp.CURSOR_HIDE_MS_DEFAULT),
        ):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".conf", delete=False, encoding="utf-8"
                ) as f:
                    f.write(f"cursor_hide_ms={value}\n")
                    path = f.name
                try:
                    self.assertEqual(pp.load_conf(path)["cursor_hide_ms"], expected)
                finally:
                    os.unlink(path)

    def test_cursor_style_allowlist(self):
        for value, expected in (
            ("cross", "cross"),
            ("win95", "win95"),
            ("WIN95", "win95"),
            ("arrow", pp.CURSOR_STYLE_DEFAULT),
            ("", pp.CURSOR_STYLE_DEFAULT),
        ):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".conf", delete=False, encoding="utf-8"
                ) as f:
                    f.write(f"cursor_style={value}\n")
                    path = f.name
                try:
                    self.assertEqual(pp.load_conf(path)["cursor_style"], expected)
                finally:
                    os.unlink(path)

    def test_orientation_config_accepts_fixed_and_auto(self):
        self.assertEqual(pp.default_conf()["orientation"], pp.ORIENTATION_AUTO)
        for value, expected in (
            ("auto", "auto"),
            ("0", 0),
            ("90", 90),
            ("180", 180),
            ("270", 270),
            ("45", pp.ORIENTATION_AUTO),
            ("portrait", pp.ORIENTATION_AUTO),
        ):
            with self.subTest(value=value):
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".conf", delete=False, encoding="utf-8"
                ) as f:
                    f.write(f"orientation={value}\n")
                    path = f.name
                try:
                    self.assertEqual(pp.load_conf(path)["orientation"], expected)
                finally:
                    os.unlink(path)

    def test_normalize_and_write_cursor_style_file(self):
        self.assertEqual(pp.normalize_cursor_style("win95"), "win95")
        self.assertEqual(pp.normalize_cursor_style("nope"), "cross")
        import ppd.constants as ppd_const

        with tempfile.TemporaryDirectory() as tmp:
            style_path = Path(tmp) / "cursor_style"
            old_home = ppd_const.HOME
            old_path = ppd_const.CURSOR_STYLE_PATH
            try:
                ppd_const.HOME = tmp
                ppd_const.CURSOR_STYLE_PATH = str(style_path)
                # Keep facade names in sync for anything still reading them.
                pp.HOME = tmp
                pp.CURSOR_STYLE_PATH = str(style_path)
                self.assertEqual(pp.write_cursor_style_file("win95"), "win95")
                self.assertEqual(style_path.read_text(encoding="ascii").strip(), "win95")
            finally:
                ppd_const.HOME = old_home
                ppd_const.CURSOR_STYLE_PATH = old_path
                pp.HOME = old_home
                pp.CURSOR_STYLE_PATH = old_path


class TestKeyboardPresenceDetection(unittest.TestCase):
    """BT keyboard should suppress OSK via rM_Keyboard presence logic."""

    SAMPLE = """
I: Bus=0019 Vendor=0000 Product=0000 Version=0000
N: Name="30370000.snvs:snvs-powerkey"
H: Handlers=kbd event0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0005 Vendor=36f7 Product=5755 Version=0001
N: Name="CLVX S | Channel 2 Keyboard"
H: Handlers=kbd leds event5
B: EV=12001f
B: KEY=3bfff 0 0 483ffff17aff32d bfd4444600000000 1 130ff38b17c007 ffff7bfad9415fff ffbeffdfffefffff fffffffffffffffe

I: Bus=0005 Vendor=36f7 Product=5755 Version=0001
N: Name="CLVX S | Channel 2 Wireless Radio Control"
H: Handlers=kbd event6 rfkill
B: EV=13
B: KEY=80000000000000 0 0 0

I: Bus=0006 Vendor=0001 Product=0001 Version=0001
N: Name="paperpointer-touch"
H: Handlers=event4
B: EV=b
B: KEY=420 0 0 0 0 0

I: Bus=0006 Vendor=0001 Product=0001 Version=0001
N: Name="rM_Keyboard"
H: Handlers=kbd event9
B: EV=3
B: KEY=ffffffffffff
"""

    def test_parses_blocks(self):
        devs = pp.parse_input_device_blocks(self.SAMPLE)
        names = [d["name"] for d in devs]
        self.assertIn("CLVX S | Channel 2 Keyboard", names)
        self.assertIn("paperpointer-touch", names)

    def test_clvx_keyboard_counts_as_external(self):
        self.assertTrue(
            pp.external_keyboard_present(self.SAMPLE),
            "CLVX Keyboard HID must count as external keyboard",
        )

    def test_presence_and_touch_do_not_count(self):
        only_self = """
I: Bus=0006 Vendor=0001 Product=0001 Version=0001
N: Name="rM_Keyboard"
H: Handlers=kbd event9
B: EV=3
B: KEY=ffffffffffff

I: Bus=0006 Vendor=0001 Product=0001 Version=0001
N: Name="paperpointer-touch"
H: Handlers=event4
B: EV=b
B: KEY=420 0 0 0 0 0
"""
        self.assertFalse(pp.external_keyboard_present(only_self))

    def test_powerkey_and_radio_do_not_count(self):
        stock = """
I: Bus=0019 Vendor=0000 Product=0000 Version=0000
N: Name="30370000.snvs:snvs-powerkey"
H: Handlers=kbd event0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0005 Vendor=36f7 Product=5755 Version=0001
N: Name="CLVX S | Channel 2 Wireless Radio Control"
H: Handlers=kbd event6 rfkill
B: EV=13
B: KEY=80000000000000 0 0 0
"""
        self.assertFalse(pp.external_keyboard_present(stock))

    def test_is_external_helpers(self):
        self.assertTrue(
            pp.is_external_keyboard_device(
                {
                    "name": "CLVX S | Channel 2 Keyboard",
                    "handlers": "kbd leds event5",
                    "key": "3bfff 0 0",
                }
            )
        )
        self.assertFalse(
            pp.is_external_keyboard_device(
                {"name": "rM_Keyboard", "handlers": "kbd event9", "key": "ffff"}
            )
        )


class TestClampMove(unittest.TestCase):
    def setUp(self):
        self.cfg = pp.default_conf()
        self.cfg["accel"] = 2.0

    def test_rel_delta_applies_accel(self):
        x, y = pp.clamp_move(self.cfg, 100.0, 200.0, 10, 5)
        self.assertEqual(x, 120.0)  # 100 + 10*2
        self.assertEqual(y, 210.0)  # 200 + 5*2

    def test_clamp_high(self):
        x, y = pp.clamp_move(self.cfg, 2060.0, 2830.0, 100, 100)
        self.assertEqual(x, 2064.0)
        self.assertEqual(y, 2832.0)

    def test_clamp_low(self):
        x, y = pp.clamp_move(self.cfg, 5.0, 5.0, -100, -100)
        self.assertEqual(x, 0.0)
        self.assertEqual(y, 0.0)

    def test_invert_and_swap(self):
        self.cfg["invert_x"] = 1
        self.cfg["invert_y"] = 1
        self.cfg["swap_xy"] = 1
        # swap first: dx,dy = 5,10 then invert → -5,-10 → *accel
        x, y = pp.clamp_move(self.cfg, 100.0, 200.0, 10, 5)
        self.assertEqual(x, 100.0 + (-5) * 2.0)  # dy becomes dx after swap, inverted
        self.assertEqual(y, 200.0 + (-10) * 2.0)

    def test_logical_motion_ignores_orientation(self):
        """clamp_move must stay in logical space for every UI rotation."""
        base = pp.clamp_move(self.cfg, 100.0, 200.0, 10, 5)
        for rot in (0, 90, 180, 270):
            self.cfg["orientation"] = rot
            self.assertEqual(
                pp.clamp_move(self.cfg, 100.0, 200.0, 10, 5),
                base,
                rot,
            )


class TestMapAbs(unittest.TestCase):
    def setUp(self):
        self.cfg = pp.default_conf()
        # CLVX-like pad ranges from handoff
        self.abs_x = (0, 20800)
        self.abs_y = (0, 10460)

    def test_abs_mid_maps_to_mid_touch(self):
        x, y = 0.0, 0.0
        x, y = pp.map_abs_position(
            self.cfg, x, y, "x", 10400, self.abs_x, self.abs_y
        )
        x, y = pp.map_abs_position(
            self.cfg, x, y, "y", 5230, self.abs_x, self.abs_y
        )
        self.assertAlmostEqual(x, 1032.0, places=0)
        self.assertAlmostEqual(y, 1416.0, places=0)

    def test_abs_edges_clamped(self):
        x, y = pp.map_abs_position(
            self.cfg, 0, 0, "x", -100, self.abs_x, self.abs_y
        )
        self.assertEqual(x, 0.0)
        x, y = pp.map_abs_position(
            self.cfg, 0, 0, "x", 999999, self.abs_x, self.abs_y
        )
        self.assertEqual(x, 2064.0)
        x, y = pp.map_abs_position(
            self.cfg, 0, 0, "y", 999999, self.abs_x, self.abs_y
        )
        self.assertEqual(y, 2832.0)

    def test_map_abs_axis_invert(self):
        n = pp.map_abs_axis(0, 0, 100, 200, invert=True)
        self.assertEqual(n, 200.0)
        n = pp.map_abs_axis(100, 0, 100, 200, invert=True)
        self.assertEqual(n, 0.0)


class TestDragThreshold(unittest.TestCase):
    def test_small_press_jitter_does_not_start_drag(self):
        self.assertFalse(pp.moved_past_drag_threshold((100, 200), 106, 207))

    def test_intentional_motion_starts_drag(self):
        self.assertTrue(pp.moved_past_drag_threshold((100, 200), 110, 200))

    def test_missing_start_fails_open_for_existing_contact(self):
        self.assertTrue(pp.moved_past_drag_threshold(None, 100, 200))


class TestContactState(unittest.TestCase):
    def test_primary_down_up(self):
        c = pp.ContactState()
        self.assertFalse(c.contact_active)
        self.assertTrue(c.on_primary(True))
        self.assertTrue(c.contact_active)
        self.assertFalse(c.on_primary(False))
        self.assertFalse(c.contact_active)

    def test_primary_hold_while_button_held(self):
        c = pp.ContactState()
        c.on_primary(True)
        self.assertTrue(c.contact_active)
        # move would not change contact; release does
        self.assertFalse(c.on_primary(False))

    def test_finger_alone_no_contact_without_finger_drag(self):
        c = pp.ContactState()
        c.on_finger(True, finger_drag=False)
        self.assertFalse(c.contact_active)
        self.assertFalse(c.want_contact(finger_drag=False))

    def test_finger_drag_holds_contact(self):
        c = pp.ContactState()
        c.on_finger(True, finger_drag=True)
        self.assertTrue(c.contact_active)
        c.on_primary(True, finger_drag=True)
        c.on_primary(False, finger_drag=True)
        # finger still present → stay down
        self.assertTrue(c.contact_active)
        c.on_finger(False, finger_drag=True)
        self.assertFalse(c.contact_active)

    def test_primary_alias_release_keeps_other_button_contact(self):
        c = pp.ContactState()
        c.on_primary(True, code=pp.BTN_LEFT)
        c.on_primary(True, code=pp.BTN_0)

        self.assertTrue(c.on_primary(False, code=pp.BTN_LEFT))
        self.assertEqual(c.buttons, 1)
        self.assertTrue(c.contact_active)

        self.assertFalse(c.on_primary(False, code=pp.BTN_0))
        self.assertEqual(c.buttons, 0)
        self.assertFalse(c.contact_active)

    def test_finger_alias_release_keeps_other_finger_drag_contact(self):
        c = pp.ContactState()
        c.on_finger(True, finger_drag=True, code=pp.BTN_TOUCH)
        c.on_finger(True, finger_drag=True, code=pp.BTN_TOOL_FINGER)

        self.assertTrue(
            c.on_finger(False, finger_drag=True, code=pp.BTN_TOUCH)
        )
        self.assertEqual(c.finger, 1)
        self.assertTrue(c.contact_active)

        self.assertFalse(
            c.on_finger(False, finger_drag=True, code=pp.BTN_TOOL_FINGER)
        )
        self.assertEqual(c.finger, 0)
        self.assertFalse(c.contact_active)


class TestSourceClassify(unittest.TestCase):
    def test_ignore_elan_marker(self):
        self.assertTrue(pp.is_ignored("Elan marker input"))
        self.assertIsNone(
            pp.classify_from_caps(
                "Elan marker input",
                has_abs_xy=True,
                has_btn_touch=True,
            )
        )

    def test_ignore_elan_touch(self):
        self.assertTrue(pp.is_ignored("Elan touch input"))
        self.assertIsNone(
            pp.classify_from_caps(
                "Elan touch input",
                has_abs_xy=True,
                has_btn_touch=True,
            )
        )

    def test_ignore_paperpointer_self(self):
        self.assertTrue(pp.is_ignored("paperpointer-touch"))
        self.assertIsNone(
            pp.classify_from_caps("paperpointer-touch", has_abs_xy=True)
        )

    def test_clvx_touchpad_abs(self):
        kind = pp.classify_from_caps(
            "CLVX S Touchpad",
            has_abs_xy=True,
            has_btn_tool_finger=True,
            has_btn_left=True,
        )
        self.assertEqual(kind, "abs_pad")

    def test_rel_mouse(self):
        kind = pp.classify_from_caps(
            "CLVX S Mouse",
            has_rel_xy=True,
            has_btn_left=True,
        )
        self.assertEqual(kind, "rel")

    def test_keyboard_only_not_pointer(self):
        kind = pp.classify_from_caps(
            "CLVX S Keyboard",
            has_rel_xy=True,
            has_btn_left=False,
        )
        self.assertIsNone(kind)

    def test_gpio_ignored(self):
        self.assertTrue(pp.is_ignored("gpio-keys"))


class TestSourceSelection(unittest.TestCase):
    def test_relative_node_wins_over_abs_node(self):
        absolute = ("/dev/input/event8", "abs_pad", "CLVX S Touchpad")
        relative = ("/dev/input/event7", "rel", "CLVX S Mouse")
        self.assertEqual(pp.choose_sources([absolute, relative]), [relative])

    def test_first_abs_node_is_used_when_no_relative_node_exists(self):
        first = ("/dev/input/event8", "abs_pad", "CLVX S Touchpad")
        second = ("/dev/input/event9", "abs_pad", "Other Touchpad")
        self.assertEqual(pp.choose_sources([first, second]), [first])


class TestCursorMessage(unittest.TestCase):
    def test_finite_coordinates_are_clamped_and_formatted(self):
        self.assertEqual(
            pp.format_cursor_message(-0.25, 1.5, True),
            b"0.000000,1.000000,1\n",
        )

    def test_non_finite_coordinates_are_rejected(self):
        bad_values = (float("nan"), float("inf"), float("-inf"))
        for value in bad_values:
            with self.subTest(axis="x", value=value):
                with self.assertRaises(ValueError):
                    pp.format_cursor_message(value, 0.5, True)
            with self.subTest(axis="y", value=value):
                with self.assertRaises(ValueError):
                    pp.format_cursor_message(0.5, value, True)


class TestLogicalPhysicalOrientation(unittest.TestCase):
    """Single conversion boundary: logical cursor → physical touch."""

    CORNERS = (
        (0.0, 0.0),
        (1.0, 0.0),
        (0.0, 1.0),
        (1.0, 1.0),
        (0.5, 0.5),
    )

    def test_portrait_is_identity(self):
        for x, y in self.CORNERS + ((0.25, 0.75), (0.9, 0.1)):
            self.assertEqual(pp.logical_to_physical(x, y, 0), (x, y))

    def test_center_stable_in_every_orientation(self):
        for rot in (0, 90, 180, 270):
            self.assertEqual(
                pp.logical_to_physical(0.5, 0.5, rot),
                (0.5, 0.5),
                rot,
            )

    def test_corners_map_at_90(self):
        # (lx, ly) → (1 - ly, lx)
        expected = {
            (0.0, 0.0): (1.0, 0.0),
            (1.0, 0.0): (1.0, 1.0),
            (0.0, 1.0): (0.0, 0.0),
            (1.0, 1.0): (0.0, 1.0),
        }
        for src, dst in expected.items():
            self.assertEqual(pp.logical_to_physical(*src, 90), dst)

    def test_corners_map_at_180(self):
        expected = {
            (0.0, 0.0): (1.0, 1.0),
            (1.0, 0.0): (0.0, 1.0),
            (0.0, 1.0): (1.0, 0.0),
            (1.0, 1.0): (0.0, 0.0),
        }
        for src, dst in expected.items():
            self.assertEqual(pp.logical_to_physical(*src, 180), dst)

    def test_corners_map_at_270(self):
        # (lx, ly) → (ly, 1 - lx)
        expected = {
            (0.0, 0.0): (0.0, 1.0),
            (1.0, 0.0): (0.0, 0.0),
            (0.0, 1.0): (1.0, 1.0),
            (1.0, 1.0): (1.0, 0.0),
        }
        for src, dst in expected.items():
            self.assertEqual(pp.logical_to_physical(*src, 270), dst)

    def test_round_trip_logical_physical_logical(self):
        samples = self.CORNERS + ((0.2, 0.3), (0.75, 0.1), (0.01, 0.99))
        for rot in (0, 90, 180, 270):
            for lx, ly in samples:
                pnx, pny = pp.logical_to_physical(lx, ly, rot)
                back = pp.physical_to_logical(pnx, pny, rot)
                self.assertAlmostEqual(back[0], lx, places=9, msg=(rot, lx, ly))
                self.assertAlmostEqual(back[1], ly, places=9, msg=(rot, lx, ly))

    def test_inputs_are_clamped(self):
        self.assertEqual(pp.logical_to_physical(-1.0, 2.0, 0), (0.0, 1.0))
        self.assertEqual(pp.logical_to_physical(-0.5, 1.5, 90), (0.0, 0.0))

    def test_invalid_rotation_falls_back_to_identity(self):
        self.assertEqual(pp.logical_to_physical(0.25, 0.75, 45), (0.25, 0.75))
        self.assertEqual(pp.normalize_orientation("nope"), 0)

    def test_non_finite_rejected(self):
        with self.assertRaises(ValueError):
            pp.logical_to_physical(float("nan"), 0.5, 0)
        with self.assertRaises(ValueError):
            pp.physical_to_logical(0.5, float("inf"), 90)

    def test_landscape_click_transforms_exactly_once(self):
        """Logical cursor pixel → one conversion → physical touch pixel."""
        txm, tym = 2064, 2832
        lx, ly = 0.25 * txm, 0.10 * tym
        pnx, pny = pp.logical_to_physical(lx / txm, ly / tym, 90)
        px, py = pnx * txm, pny * tym
        # 90°: (1 - ly_n, lx_n) = (0.90, 0.25)
        self.assertAlmostEqual(px / txm, 0.90, places=9)
        self.assertAlmostEqual(py / tym, 0.25, places=9)
        # Applying the map a second time must NOT be what injection does.
        twice = pp.logical_to_physical(px / txm, py / tym, 90)
        self.assertNotAlmostEqual(twice[0], pnx)
        self.assertNotAlmostEqual(twice[1], pny)

    def test_click_lag_history_stays_logical(self):
        """visual_position samples are logical; conversion is injection-only."""
        packets: list[bytes] = []
        publisher = pp.CursorPublisher(
            rate_hz=pp.CURSOR_HZ_MAX, writer=packets.append
        )
        try:
            # Logical mid-right in landscape UI must be published unchanged.
            publisher.publish(1548.0, 1416.0, 2064, 2832, visible=True)
            deadline = pp.time.monotonic() + 1.0
            while not packets and pp.time.monotonic() < deadline:
                pp.time.sleep(0.005)
            self.assertTrue(packets)
            self.assertEqual(packets[-1], b"0.750000,0.500000,1\n")
            visual = publisher.visual_position(2064, 2832, lag_ms=0)
            self.assertEqual(visual, (1548.0, 1416.0))
            # Injection boundary converts the lag sample once.
            nx, ny = visual[0] / 2064, visual[1] / 2832
            self.assertEqual(
                pp.logical_to_physical(nx, ny, 90),
                (0.5, 0.75),
            )
        finally:
            publisher.close()

    def test_drag_and_scroll_endpoints_both_transform(self):
        def phys(lx, ly, rot):
            pnx, pny = pp.logical_to_physical(lx, ly, rot)
            return pnx, pny

        start = (0.2, 0.3)
        end = (0.2, 0.6)
        for rot in (90, 180, 270):
            ps = phys(*start, rot)
            pe = phys(*end, rot)
            self.assertNotEqual(ps, start)
            self.assertNotEqual(pe, end)
            # Both endpoints use the same single-step map.
            self.assertEqual(ps, pp.logical_to_physical(*start, rot))
            self.assertEqual(pe, pp.logical_to_physical(*end, rot))


class TestXochitlOrientationParse(unittest.TestCase):
    def test_prefers_ui_setting_over_csl(self):
        text = (
            "New orientation from csl: Orientation::ORIENTATION_NORMAL.\n"
            "Setting new orientation Portrait\n"
            "New orientation from csl: Orientation::ORIENTATION_LEFT_UP.\n"
            "Setting new orientation InvertedLandscape\n"
        )
        self.assertEqual(
            pp.parse_xochitl_orientation_log(text),
            (270, "xochitl=InvertedLandscape"),
        )

    def test_landscape_not_confused_with_inverted(self):
        text = "Setting new orientation Landscape\n"
        self.assertEqual(
            pp.parse_xochitl_orientation_log(text),
            (90, "xochitl=Landscape"),
        )

    def test_csl_fallback(self):
        text = (
            "New orientation from csl: Orientation::ORIENTATION_LEFT_UP.\n"
        )
        self.assertEqual(
            pp.parse_xochitl_orientation_log(text),
            (270, "csl=ORIENTATION_LEFT_UP"),
        )

    def test_empty(self):
        self.assertIsNone(pp.parse_xochitl_orientation_log(""))


class TestUiOrientation(unittest.TestCase):
    def test_fixed_override_ignores_journal_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui_orientation")
            with open(path, "w", encoding="ascii") as f:
                f.write("90\n")
            orient = pp.UiOrientation(0, path=path)
            try:
                self.assertEqual(orient.get(), 0)
            finally:
                orient.close()
            orient90 = pp.UiOrientation(90, path=path)
            try:
                self.assertEqual(orient90.get(), 90)
            finally:
                orient90.close()

    def test_auto_seeds_from_file_without_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui_orientation")
            with open(path, "w", encoding="ascii") as f:
                f.write("270\n")
            orient = pp.UiOrientation(
                "auto",
                path=path,
                reader=lambda: None,
                start_watcher=False,
            )
            try:
                self.assertEqual(orient.get(), 270)
            finally:
                orient.close()

    def test_background_watcher_updates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui_orientation")
            with open(path, "w", encoding="ascii") as f:
                f.write("0\n")
            orient = pp.UiOrientation(
                "auto",
                path=path,
                poll_s=0.05,
                reader=lambda: (270, "xochitl=InvertedLandscape"),
            )
            try:
                deadline = pp.time.monotonic() + 1.0
                while orient.get() != 270 and pp.time.monotonic() < deadline:
                    pp.time.sleep(0.02)
                self.assertEqual(orient.get(), 270)
            finally:
                orient.close()

    def test_auto_missing_sources_defaults_to_portrait(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing")
            orient = pp.UiOrientation(
                "auto",
                path=path,
                reader=lambda: None,
                start_watcher=False,
            )
            try:
                self.assertEqual(orient.get(), 0)
            finally:
                orient.close()

    def test_hot_path_never_calls_slow_orientation_reader(self):
        """Regression: ordinary motion must not launch journalctl/logread."""
        calls: list[int] = []

        def slow_reader():
            calls.append(1)
            pp.time.sleep(0.35)
            return (90, "xochitl=Landscape")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui_orientation")
            orient = pp.UiOrientation(
                "auto",
                path=path,
                poll_s=60.0,  # watcher must not re-enter during the test window
                reader=slow_reader,
                start_watcher=False,  # prove get() alone never calls reader
            )
            try:
                # Seed like production would after a prior watcher refresh.
                with orient._lock:
                    orient._value = 90
                    orient._source = "xochitl=Landscape"

                t0 = pp.time.monotonic()
                cfg = pp.default_conf()
                x, y = 100.0, 200.0
                for _ in range(200):
                    x, y = pp.clamp_move(cfg, x, y, 1, 1)
                    # Same work as the hover path: logical motion + cache read.
                    _ = orient.get()
                    _ = orient.get(force=True)  # force must still be non-blocking
                elapsed = pp.time.monotonic() - t0

                self.assertEqual(calls, [], "reader must not run on the hot path")
                self.assertLess(
                    elapsed,
                    0.15,
                    f"hot path blocked for {elapsed:.3f}s despite slow reader",
                )
                self.assertEqual(orient.get(), 90)
            finally:
                orient.close()

        # Watcher path is allowed to call the reader, but still not from get().
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ui_orientation")
            calls.clear()
            orient = pp.UiOrientation(
                "auto",
                path=path,
                poll_s=0.05,
                reader=slow_reader,
            )
            try:
                t0 = pp.time.monotonic()
                for _ in range(50):
                    orient.get()
                elapsed = pp.time.monotonic() - t0
                self.assertLess(elapsed, 0.1)
                # Background thread may have started one slow call already.
                deadline = pp.time.monotonic() + 1.5
                while not calls and pp.time.monotonic() < deadline:
                    pp.time.sleep(0.02)
                self.assertGreaterEqual(len(calls), 1)
                # get() still returns quickly while watcher is mid-read.
                t1 = pp.time.monotonic()
                for _ in range(100):
                    orient.get()
                self.assertLess(pp.time.monotonic() - t1, 0.1)
            finally:
                orient.close()


class TestCursorPublisher(unittest.TestCase):
    def test_publish_coordinates_ignore_orientation(self):
        """FIFO always carries logical coords; rotation must not appear here."""
        packets: list[bytes] = []
        publisher = pp.CursorPublisher(
            rate_hz=pp.CURSOR_HZ_MAX, writer=packets.append
        )
        try:
            for _ in range(3):
                publisher.publish(516.0, 708.0, 2064, 2832, visible=True)
            deadline = pp.time.monotonic() + 1.0
            while len(packets) < 1 and pp.time.monotonic() < deadline:
                pp.time.sleep(0.005)
            self.assertTrue(packets)
            # Same logical position regardless of any UI rotation concept.
            self.assertEqual(packets[-1], b"0.250000,0.250000,1\n")
        finally:
            publisher.close()

    def test_close_finishes_with_one_hide_and_discards_pending_visible(self):
        packets: list[bytes] = []
        first_visible = threading.Event()

        def writer(packet: bytes) -> None:
            packets.append(packet)
            if packet.endswith(b",1\n"):
                first_visible.set()

        publisher = pp.CursorPublisher(rate_hz=1, writer=writer)
        try:
            publisher.publish(10, 20, 100, 100, visible=True)
            self.assertTrue(first_visible.wait(1.0), "visible packet was not sent")

            # This update is held behind the rate limit. close() must discard it
            # and synchronously make the hide packet the final observation.
            publisher.publish(90, 80, 100, 100, visible=True)
        finally:
            publisher.close()

        hidden = [packet for packet in packets if packet.endswith(b",0\n")]
        self.assertEqual(hidden, [b"0.500000,0.500000,0\n"])
        self.assertEqual(packets[-1], hidden[0])

        packet_count = len(packets)
        publisher.publish(50, 50, 100, 100, visible=True)
        self.assertEqual(packets[packet_count:], [])

    def test_last_sent_position_tracks_successful_visible_write_only(self):
        publisher = pp.CursorPublisher(
            rate_hz=pp.CURSOR_HZ_MAX, writer=lambda _packet: None
        )
        try:
            self.assertIsNone(publisher.last_sent_position(200, 300))
            publisher.publish(50, 75, 200, 300, visible=True)
            deadline = pp.time.monotonic() + 1.0
            while (
                publisher.last_sent_position(200, 300) is None
                and pp.time.monotonic() < deadline
            ):
                pp.time.sleep(0.005)
            self.assertEqual(publisher.last_sent_position(200, 300), (50, 75))

            with publisher._condition:
                publisher._last_sent_at -= 1.0
            self.assertIsNone(publisher.last_sent_position(200, 300))
        finally:
            publisher.close()

        self.assertIsNone(publisher.last_sent_position(200, 300))

    def test_failed_write_is_not_reported_as_last_sent(self):
        attempted = threading.Event()

        def writer(_packet: bytes) -> None:
            attempted.set()
            raise OSError(pp.errno.EPIPE, "no reader")

        publisher = pp.CursorPublisher(rate_hz=pp.CURSOR_HZ_MAX, writer=writer)
        try:
            publisher.publish(50, 75, 200, 300, visible=True)
            self.assertTrue(attempted.wait(1.0))
            self.assertIsNone(publisher.last_sent_position(200, 300))
        finally:
            publisher.close()

    def test_visual_position_rewinds_motion_but_not_stationary_position(self):
        publisher = pp.CursorPublisher(
            rate_hz=pp.CURSOR_HZ_MAX, writer=lambda _packet: None
        )
        try:
            now = pp.time.monotonic()
            with publisher._condition:
                publisher._sent_history.clear()
                publisher._sent_history.extend(
                    (
                        (now - 0.080, (0.25, 0.25, True)),
                        (now - 0.020, (0.75, 0.75, True)),
                    )
                )
            self.assertEqual(
                publisher.visual_position(200, 300, lag_ms=50),
                (50, 75),
            )
            self.assertEqual(
                publisher.visual_position(200, 300, lag_ms=0),
                (150, 225),
            )

            stationary = pp.time.monotonic()
            with publisher._condition:
                publisher._sent_history.clear()
                publisher._sent_history.append(
                    (stationary - 0.100, (0.6, 0.4, True))
                )
            self.assertEqual(
                publisher.visual_position(200, 300, lag_ms=50),
                (120, 120),
            )
        finally:
            publisher.close()

    def test_visual_position_never_crosses_hide_boundary(self):
        publisher = pp.CursorPublisher(
            rate_hz=pp.CURSOR_HZ_MAX, writer=lambda _packet: None
        )
        try:
            now = pp.time.monotonic()
            with publisher._condition:
                publisher._sent_history.clear()
                publisher._sent_history.extend(
                    (
                        (now - 0.100, (0.1, 0.1, True)),
                        (now - 0.060, (0.5, 0.5, False)),
                        (now - 0.010, (0.9, 0.9, True)),
                    )
                )
            self.assertEqual(
                publisher.visual_position(200, 300, lag_ms=100),
                (180, 270),
            )
        finally:
            publisher.close()

    def test_rate_is_capped_at_fast_mode_maximum(self):
        publisher = pp.CursorPublisher(rate_hz=10_000, writer=lambda _: None)
        try:
            self.assertAlmostEqual(publisher.interval, 1 / pp.CURSOR_HZ_MAX)
        finally:
            publisher.close()


class TestInputFrame(unittest.TestCase):
    def test_relative_axes_are_batched_until_syn_report(self):
        frame = pp.InputFrame()

        self.assertIsNone(frame.feed(pp.EV_REL, pp.REL_X, 7))
        self.assertIsNone(frame.feed(pp.EV_REL, pp.REL_Y, -3))
        self.assertEqual(
            frame.feed(pp.EV_SYN, pp.SYN_REPORT, 0),
            ("frame", (7, -3, 0, None, None, ())),
        )

    def test_syn_dropped_discards_through_recovery_report(self):
        frame = pp.InputFrame()

        frame.feed(pp.EV_REL, pp.REL_X, 4)
        self.assertEqual(
            frame.feed(pp.EV_SYN, pp.SYN_DROPPED, 0),
            ("dropped", None),
        )
        self.assertIsNone(frame.feed(pp.EV_REL, pp.REL_X, 1000))
        self.assertIsNone(frame.feed(pp.EV_REL, pp.REL_Y, 2000))
        self.assertIsNone(frame.feed(pp.EV_KEY, pp.BTN_LEFT, 1))
        self.assertEqual(
            frame.feed(pp.EV_SYN, pp.SYN_REPORT, 0),
            ("recovered", None),
        )

        frame.feed(pp.EV_REL, pp.REL_X, 5)
        frame.feed(pp.EV_REL, pp.REL_Y, 6)
        self.assertEqual(
            frame.feed(pp.EV_SYN, pp.SYN_REPORT, 0),
            ("frame", (5, 6, 0, None, None, ())),
        )

    def test_physical_left_button_is_preserved_in_complete_frame(self):
        frame = pp.InputFrame()
        self.assertIsNone(frame.feed(pp.EV_KEY, pp.BTN_LEFT, 1))
        self.assertEqual(
            frame.feed(pp.EV_SYN, pp.SYN_REPORT, 0),
            ("frame", (0, 0, 0, None, None, ((pp.BTN_LEFT, 1),))),
        )


class TestKeyStateRecovery(unittest.TestCase):
    def test_requested_pressed_codes_are_decoded_from_bitmap(self):
        bitmap = bytearray(pp.KEY_BITMAP_BYTES)
        for code in (pp.BTN_LEFT, pp.BTN_TOOL_FINGER):
            bitmap[code // 8] |= 1 << (code % 8)

        held = pp.key_codes_from_bitmap(
            bitmap, pp.PRIMARY_CLICK | pp.FINGER_CODES | {pp.BTN_RIGHT}
        )
        self.assertEqual(held, {pp.BTN_LEFT, pp.BTN_TOOL_FINGER})

    def test_short_bitmap_safely_ignores_out_of_range_codes(self):
        self.assertEqual(
            pp.key_codes_from_bitmap(bytearray(1), {pp.BTN_LEFT}), set()
        )


def _daemon_source_files() -> list[Path]:
    """Shipped daemon entry + split responsibility modules under device/ppd/."""
    files = [DAEMON_PATH]
    ppd_dir = ROOT / "device" / "ppd"
    if ppd_dir.is_dir():
        files.extend(sorted(ppd_dir.glob("*.py")))
    return files


class TestNoPhysicalMarkerWrites(unittest.TestCase):
    """The daemon may classify the marker, but must never open it for writes."""

    def test_no_marker_cleanup_or_write_capable_marker_open(self):
        self.assertFalse(hasattr(pp, "clear_stuck_marker"))
        for path in _daemon_source_files():
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("cleared marker state", src.lower(), path.name)
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or len(node.args) < 2:
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "open"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                ):
                    continue
                target = ast.unparse(node.args[0]).lower()
                flags = ast.unparse(node.args[1])
                self.assertNotIn("marker", target, path.name)
                self.assertNotIn("O_RDWR", flags, path.name)


class TestRunLoopShipped(unittest.TestCase):
    """Drive shipped run_loop far enough to catch missing imports (e.g. KeyboardPresence)."""

    def test_run_loop_constructs_keyboard_presence_without_nameerror(self):
        """Shipped run_loop must bind KeyboardPresence (import regression)."""
        import ppd.loop as loop_mod

        self.assertTrue(
            hasattr(loop_mod, "KeyboardPresence"),
            "ppd.loop must import KeyboardPresence for run_loop",
        )
        # Facade re-exports the same class used by the loop module.
        self.assertIs(pp.KeyboardPresence, loop_mod.KeyboardPresence)

        class FakeTouch:
            def __init__(self, x_max, y_max):
                self.x = int(x_max) // 2
                self.y = int(y_max) // 2
                self.down = False
                self.closed = False

            def close(self):
                self.closed = True

            def contact(self, _down):
                return None

            def move(self, _x, _y):
                return None

        class FakeKb:
            instances: list = []

            def __init__(self):
                FakeKb.instances.append(self)
                self.closed = False

            def sync(self, want=None):
                return None

            def close(self):
                self.closed = True

        class FakeOrient:
            def __init__(self, *args, **kwargs):
                self.source = "test"

            def get(self, force=False):
                return 0

            def close(self):
                return None

        FakeKb.instances = []
        cfg = pp.default_conf()
        cfg["cursor"] = 0
        cfg["orientation"] = 0  # fixed; no background watcher

        iterations = {"n": 0}

        def open_then_stop(_cfg):
            iterations["n"] += 1
            if iterations["n"] >= 2:
                raise KeyboardInterrupt()
            return []

        # Patch names inside the shipped loop module — that is what run_loop uses.
        with patch.object(loop_mod, "TouchClick", FakeTouch):
            with patch.object(loop_mod, "KeyboardPresence", FakeKb):
                with patch.object(loop_mod, "UiOrientation", FakeOrient):
                    with patch.object(
                        loop_mod, "open_sources", side_effect=open_then_stop
                    ):
                        with patch.object(loop_mod.time, "sleep", return_value=None):
                            try:
                                loop_mod.run_loop(cfg)
                            except KeyboardInterrupt:
                                pass

        self.assertEqual(len(FakeKb.instances), 1)
        self.assertTrue(
            FakeKb.instances[0].closed,
            "run_loop finally must close KeyboardPresence",
        )


class TestTouchConversionBoundary(unittest.TestCase):
    """Regression: no rotation before conversion; no second rotation after."""

    def test_run_loop_routes_touch_through_single_wrapper(self):
        # run_loop lives in the loop responsibility module after the split.
        loop_path = ROOT / "device" / "ppd" / "loop.py"
        src = loop_path.read_text(encoding="utf-8")
        start = src.index("def run_loop(")
        body = src[start:]
        self.assertIn("def move_touch_to_cursor(", body)
        self.assertIn("logical_to_physical(", body)
        # Direct physical injection must not remain for synthetic UI gestures.
        # TouchClick.move is only invoked from the conversion wrapper.
        self.assertEqual(body.count("touch.move("), 1)
        self.assertIn("move_touch_to_cursor(", body)
        # Cursor path stays logical.
        self.assertIn("cur.publish(x, y, txm, tym", body)
        self.assertNotIn("physical_to_logical(", body)
        # Hot path must use cached orientation only (no force re-query).
        self.assertIn("orient.get()", body)
        self.assertNotIn("orient.get(force=True)", body)
        self.assertNotIn("read_xochitl_orientation(", body)
        # Hover must not convert every motion — only while contact is down.
        self.assertIn("touch.down", body)

    def test_clamp_and_map_abs_have_no_rotation_parameter(self):
        import inspect

        self.assertNotIn("rotation", inspect.signature(pp.clamp_move).parameters)
        self.assertNotIn("orientation", inspect.signature(pp.clamp_move).parameters)
        self.assertNotIn(
            "rotation", inspect.signature(pp.map_abs_position).parameters
        )
        self.assertNotIn(
            "rotation", inspect.signature(pp.CursorPublisher.publish).parameters
        )

    def test_qml_does_not_write_false_screen_orientation(self):
        """Paper Pro never updates Qt Screen.orientation; do not publish it."""
        qmd = (ROOT / "device" / "paperpointer-cursor.qmd").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Screen.angleBetween", qmd)
        self.assertNotIn("publishOrientation", qmd)
        self.assertNotIn("ui_orientation", qmd)
        self.assertIn("logical coordinates", qmd.lower())


if __name__ == "__main__":
    unittest.main()
