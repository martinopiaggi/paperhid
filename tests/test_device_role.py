"""Keyboard vs pointer role classification (multi-device pairing)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from shared.bluetooth import classify_device_role, pair_and_connect


class TestClassifyDeviceRole(unittest.TestCase):
    def test_mouse_icon(self):
        info = """Device DF:74:B9:A0:FF:20 (random)
Name: ERGO M575
Icon: input-mouse
UUID: Human Interface Device    (00001812-0000-1000-8000-00805f9b34fb)
"""
        self.assertEqual(classify_device_role(info), "pointer")

    def test_keyboard_icon(self):
        info = """Device C0:03:5D:12:76:23 (random)
Name: Split65-3
Icon: input-keyboard
UUID: Human Interface Device    (00001812-0000-1000-8000-00805f9b34fb)
"""
        self.assertEqual(classify_device_role(info), "keyboard")

    def test_appearance_mouse(self):
        info = "Appearance: 0x03c2 (962)\nName: ERGO M575\n"
        self.assertEqual(classify_device_role(info), "pointer")

    def test_appearance_keyboard(self):
        info = "Appearance: 0x03c1\nName: Some KB\n"
        self.assertEqual(classify_device_role(info), "keyboard")

    def test_name_fallback_mouse(self):
        self.assertEqual(classify_device_role("", "ERGO M575"), "pointer")

    def test_name_fallback_keyboard(self):
        self.assertEqual(classify_device_role("", "Split65-3"), "keyboard")

    def test_hid_uuid_alone_is_unknown(self):
        info = "UUID: Human Interface Device (00001812-0000-1000-8000-00805f9b34fb)\n"
        self.assertEqual(classify_device_role(info, "Gadget"), "unknown")


class TestScanEarlyExitHelpers(unittest.TestCase):
    def test_devices_match_mac(self):
        from shared.bluetooth import _devices_match

        devs = [{"mac": "AA:BB:CC:DD:EE:FF", "name": "KB"}]
        self.assertTrue(_devices_match(devs, until_mac="aa-bb-cc-dd-ee-ff"))
        self.assertFalse(_devices_match(devs, until_mac="11:22:33:44:55:66"))

    def test_devices_match_name(self):
        from shared.bluetooth import _devices_match

        devs = [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Split65-3"}]
        self.assertTrue(_devices_match(devs, until_name="Split65"))
        self.assertFalse(_devices_match(devs, until_name="ERGO"))


class TestPairDoesNotRemoveByDefault(unittest.TestCase):

    def test_old_mac_ignored_without_replace_flag(self):
        t = MagicMock()
        t.run.return_value = ("", "", 0)
        with patch("shared.bluetooth.ensure_adapter_ready"):
            with patch("shared.bluetooth.probe_radio_scan_health", return_value=(True, "ok")):
                with patch("shared.bluetooth.device_known", return_value=True):
                    with patch("shared.bluetooth.pair_interactive"):
                        with patch("shared.bluetooth.trust"):
                            with patch("shared.bluetooth.connect"):
                                with patch(
                                    "shared.bluetooth.get_connection_status",
                                    return_value=True,
                                ):
                                    with patch("shared.bluetooth.remove") as rem:
                                        pair_and_connect(
                                            t,
                                            "aa:bb:cc:dd:ee:ff",
                                            old_mac="11:22:33:44:55:66",
                                            replace_previous=False,
                                            pre_scan=False,
                                        )
                                        rem.assert_not_called()

    def test_replace_previous_removes_old_keyboard(self):
        t = MagicMock()
        t.run.return_value = ("Icon: input-keyboard\nName: OldKB\n", "", 0)
        with patch("shared.bluetooth.ensure_adapter_ready"):
            with patch("shared.bluetooth.probe_radio_scan_health", return_value=(True, "ok")):
                with patch("shared.bluetooth.device_known", return_value=True):
                    with patch("shared.bluetooth.pair_interactive"):
                        with patch("shared.bluetooth.trust"):
                            with patch("shared.bluetooth.connect"):
                                with patch(
                                    "shared.bluetooth.get_connection_status",
                                    return_value=True,
                                ):
                                    with patch("shared.bluetooth.remove") as rem:
                                        pair_and_connect(
                                            t,
                                            "aa:bb:cc:dd:ee:ff",
                                            old_mac="11:22:33:44:55:66",
                                            replace_previous=True,
                                            pre_scan=False,
                                        )
                                        rem.assert_called_once()

    def test_replace_previous_skips_pointer(self):
        t = MagicMock()
        t.run.return_value = ("Icon: input-mouse\nName: ERGO M575\n", "", 0)
        with patch("shared.bluetooth.ensure_adapter_ready"):
            with patch("shared.bluetooth.probe_radio_scan_health", return_value=(True, "ok")):
                with patch("shared.bluetooth.device_known", return_value=True):
                    with patch("shared.bluetooth.pair_interactive"):
                        with patch("shared.bluetooth.trust"):
                            with patch("shared.bluetooth.connect"):
                                with patch(
                                    "shared.bluetooth.get_connection_status",
                                    return_value=True,
                                ):
                                    with patch("shared.bluetooth.remove") as rem:
                                        pair_and_connect(
                                            t,
                                            "aa:bb:cc:dd:ee:ff",
                                            old_mac="df:74:b9:a0:ff:20",
                                            replace_previous=True,
                                            pre_scan=False,
                                        )
                                        rem.assert_not_called()


if __name__ == "__main__":
    unittest.main()
