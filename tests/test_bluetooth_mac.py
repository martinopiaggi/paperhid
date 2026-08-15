"""Strict Bluetooth MAC normalization (shell-safe addresses only)."""
from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import MagicMock

from shared.bluetooth import (
    _ssh_connection_interface,
    _wifi_gate,
    normalize_mac,
    pair,
    reconnect_now,
    remove,
)
from shared.transport import SshTransport


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


class TestWifiGate(unittest.TestCase):
    def test_skips_gate_when_ssh_uses_wifi(self):
        ssh = MagicMock()
        ssh.exec.return_value = ("wlan0\n", "", 0)

        gated = _wifi_gate(SshTransport(ssh), True)

        self.assertFalse(gated)
        self.assertEqual(ssh.exec.call_count, 1)
        self.assertNotIn("rfkill", ssh.exec.call_args.args[0])

    def test_gates_and_restores_when_ssh_uses_usb(self):
        ssh = MagicMock()
        ssh.exec.side_effect = [
            ("usb0\n", "", 0),
            ("", "", 0),
            ("", "", 0),
        ]
        transport = SshTransport(ssh)

        self.assertTrue(_wifi_gate(transport, True))
        self.assertFalse(_wifi_gate(transport, False))

        commands = [call.args[0] for call in ssh.exec.call_args_list]
        self.assertIn("SSH_CONNECTION", commands[0])
        self.assertIn("rfkill block wifi", commands[1])
        self.assertLess(
            commands[2].index("rfkill unblock wifi"),
            commands[2].index("ip link set wlan0 up"),
        )

    def test_skips_gate_when_ssh_interface_is_unknown(self):
        ssh = MagicMock()
        ssh.exec.return_value = ("", "", 0)

        self.assertFalse(_wifi_gate(SshTransport(ssh), True))
        self.assertEqual(ssh.exec.call_count, 1)

    def test_normalizes_iproute_peer_suffix(self):
        ssh = MagicMock()
        ssh.exec.return_value = ("usb0@if5\n", "", 0)

        self.assertEqual(_ssh_connection_interface(SshTransport(ssh)), "usb0")

    def test_usb_address_is_safe_fallback_when_remote_interface_is_unavailable(self):
        ssh = MagicMock()
        ssh._last_ip = "10.11.99.1"
        ssh.exec.side_effect = [("", "", 0), ("", "", 0)]

        self.assertTrue(_wifi_gate(SshTransport(ssh), True))
        self.assertIn("rfkill block wifi", ssh.exec.call_args_list[-1].args[0])

    def test_gate_error_attempts_wifi_recovery(self):
        ssh = MagicMock()
        ssh.exec.side_effect = [
            ("usb0\n", "", 0),
            TimeoutError("gate timed out"),
            ("", "", 0),
        ]

        with self.assertRaises(TimeoutError):
            _wifi_gate(SshTransport(ssh), True)

        commands = [call.args[0] for call in ssh.exec.call_args_list]
        self.assertIn("rfkill unblock wifi", commands[-1])


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
