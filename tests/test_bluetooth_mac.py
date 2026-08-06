"""Strict Bluetooth MAC normalization (shell-safe addresses only)."""
from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import MagicMock

from shared.bluetooth import normalize_mac, pair, remove, reconnect_now


class TestNormalizeMac(unittest.TestCase):
    def test_colon_form(self):
        self.assertEqual(normalize_mac("aa:bb:cc:dd:ee:ff"), "AA:BB:CC:DD:EE:FF")

    def test_dash_form(self):
        self.assertEqual(normalize_mac("AA-BB-CC-DD-EE-FF"), "AA:BB:CC:DD:EE:FF")

    def test_compact_form(self):
        self.assertEqual(normalize_mac("aabbccddeeff"), "AA:BB:CC:DD:EE:FF")

    def test_strips_spaces(self):
        self.assertEqual(normalize_mac("  aa:bb:cc:dd:ee:ff  "), "AA:BB:CC:DD:EE:FF")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            normalize_mac("")
        with self.assertRaises(ValueError):
            normalize_mac(None)  # type: ignore[arg-type]

    def test_rejects_injection(self):
        bad = [
            "aa:bb:cc:dd:ee:ff; reboot",
            "$(reboot)",
            "aa:bb:cc:dd:ee:ff;rm",
            "not-a-mac",
            "aa:bb:cc:dd:ee",
            "gg:bb:cc:dd:ee:ff",
            "../../etc/passwd",
            "aa:bb:cc:dd:ee:ff/../x",
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_mac(value)


class TestMacRequiredBeforeShell(unittest.TestCase):
    def test_pair_rejects_before_run(self):
        t = MagicMock()
        with self.assertRaises(ValueError):
            pair(t, "evil; rm -rf /")
        t.run.assert_not_called()

    def test_remove_uses_normalized(self):
        t = MagicMock()
        t.run.return_value = ("", "", 0)
        remove(t, "aa-bb-cc-dd-ee-ff")
        t.run.assert_called_once()
        cmd = t.run.call_args[0][0]
        self.assertIn("AA:BB:CC:DD:EE:FF", cmd)
        self.assertNotIn("aa-bb", cmd)

    def test_reconnect_rejects_bad_mac(self):
        t = MagicMock()
        with mock.patch("shared.bluetooth.ensure_adapter_ready"):
            with self.assertRaises(ValueError):
                reconnect_now(t, mac="not-valid")

if __name__ == "__main__":
    unittest.main()
