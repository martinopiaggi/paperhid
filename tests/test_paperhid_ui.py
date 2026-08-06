"""Shipped paperhid-ui helper + Settings QMD wiring (host-side, no tablet)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "device" / "paperhid-ui"
SETTINGS_QMD = ROOT / "device" / "paperpointer-settings.qmd"
SETTINGS_INSTALLER = ROOT / "device" / "enable_settings_ui.sh"
CLI = ROOT / "paperpointer" / "cli.py"


class TestPaperhidUiShipped(unittest.TestCase):
    def test_script_exists_and_has_commands(self):
        self.assertTrue(UI.is_file(), UI)
        text = UI.read_text(encoding="utf-8")
        self.assertIn("set -eu", text)
        for cmd in (
            "status",
            "bluetooth-on",
            "bluetooth-off",
            "bluetooth-restart",
            "keyboard-enable",
            "keyboard-disable",
            "keyboard-reconnect",
            "pointer-restart",
            "set-accel",
            "set-cursor-style",
            "set-hide-ms",
        ):
            self.assertIn(cmd, text)

    def test_operation_lock(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("paperhid-ui.lock", text)
        self.assertIn("with_lock", text)
        self.assertIn("busy", text)

    def test_bluetooth_off_stops_reconnect_first(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn('KB_SERVICE="remarkable-bt-keyboard.service"', text)
        off = text[text.index("cmd_bluetooth_off") : text.index("cmd_bluetooth_restart")]
        stop_pos = off.index('systemctl stop "$KB_SERVICE"')
        power_pos = off.index("power off")
        self.assertLess(stop_pos, power_pos)

    def test_bluetooth_on_restores_reconnect(self):
        text = UI.read_text(encoding="utf-8")
        on = text[text.index("cmd_bluetooth_on") : text.index("cmd_bluetooth_off")]
        self.assertIn("power on", on)
        self.assertIn('systemctl start "$KB_SERVICE"', on)

    def test_status_emits_json_shape_keys(self):
        text = UI.read_text(encoding="utf-8")
        # Shell source escapes quotes as \"key\" inside the printf template.
        for key in (
            "adapter",
            "services",
            "paired",
            "input_nodes",
            "last_error",
            "ok",
        ):
            self.assertIn(key, text)
        self.assertIn("cmd_status", text)
    def test_allowlists(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("1.0|2.0|3.0|4.0", text)
        self.assertIn("cross|win95", text)
        self.assertIn("0|3000|10000|60000", text)

    def test_busybox_safe_mac_read(self):
        """Tablet BusyBox head has no -c; use cut for MAC truncation."""
        text = UI.read_text(encoding="utf-8")
        self.assertNotIn("head -c", text)
        self.assertIn("cut -c1-17", text)

    def test_settings_qmd_uses_paperhid_ui_only(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        self.assertIn('command: "/home/root/.paperhid/paperhid-ui"', text)
        self.assertNotIn("/home/root/.paperpointer/ui-actions/", text)
        self.assertIn('arguments: ["status"]', text)
        self.assertIn('arguments: ["bluetooth-on"]', text)
        self.assertIn('arguments: ["bluetooth-off"]', text)
        self.assertIn('arguments: ["bluetooth-restart"]', text)
        self.assertIn('arguments: ["keyboard-reconnect"]', text)
        self.assertIn('arguments: ["set-hide-ms", "0"]', text)
        self.assertIn('arguments: ["set-cursor-style", "cross"]', text)
        # Accumulate stdout before JSON parse
        self.assertIn("statusBuf", text)
        self.assertIn("JSON.parse", text)
        self.assertIn("applyStatusJson", text)

    def test_settings_qmd_has_sections(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        for label in (
            "Status",
            "Bluetooth",
            "Paired devices",
            "Keyboard",
            "Pointer and cursor",
        ):
            self.assertIn(f'qsTr("{label}")', text)

    def test_settings_installer_requires_paperhid_ui_not_pointer_service(self):
        text = SETTINGS_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('HOME_PH="/home/root/.paperhid"', text)
        self.assertIn("paperhid-ui", text)
        self.assertIn('UI_BIN="$HOME_PH/paperhid-ui"', text)
        self.assertIn("[ -x \"$UI_BIN\" ]", text)
        self.assertNotIn(
            "systemctl is-active --quiet paperpointer.service",
            text,
        )

    def test_cli_enable_settings_uploads_paperhid_ui(self):
        text = CLI.read_text(encoding="utf-8")
        start = text.index("def cmd_enable_settings_ui")
        rest = text[start:]
        end = rest.index("\ndef cmd_") if "\ndef cmd_" in rest[1:] else 2500
        body = rest[:end]
        self.assertIn("paperhid-ui", body)
        self.assertIn(".paperhid", body)


if __name__ == "__main__":
    unittest.main()
