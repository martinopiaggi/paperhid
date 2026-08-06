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
            "set-layout",
        ):
            self.assertIn(cmd, text)

    def test_status_uses_with_lock(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("with_lock cmd_status", text)

    def test_operation_lock_pid_and_busy(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("paperhid-ui.lock", text)
        self.assertIn("with_lock", text)
        self.assertIn("busy", text)
        # PID ownership; reclaim only if dead.
        self.assertIn('"$LOCK_DIR/pid"', text)
        self.assertIn("kill -0", text)
        # Contention prints busy without set_error immediately after mkdir fail.
        mkdir_i = text.index("if ! mkdir \"$LOCK_DIR\"")
        slice_ = text[mkdir_i : mkdir_i + 280]
        self.assertIn('"error":"busy"', slice_)
        self.assertNotIn("set_error", slice_)

    def test_status_fast_vs_full(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("--fast", text)
        self.assertIn("fast=1", text)
        # Full path still has info; fast skips it inside conditional.
        self.assertIn('btctl info "$mac"', text)
        self.assertIn('[ "$fast" -eq 0 ]', text)
        # Single show blob for adapter flags.
        self.assertIn("show_blob=$(btctl show)", text)

    def test_bounded_bluetoothctl(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("run_bounded", text)
        self.assertIn("btctl()", text)

    def test_bluetooth_off_stops_reconnect_first(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn('KB_SERVICE="remarkable-bt-keyboard.service"', text)
        off = text[text.index("cmd_bluetooth_off") : text.index("cmd_bluetooth_restart")]
        stop_pos = off.index('sysctl_quiet stop "$KB_SERVICE"')
        power_pos = off.index("power off")
        self.assertLess(stop_pos, power_pos)

    def test_bluetooth_on_restores_reconnect(self):
        text = UI.read_text(encoding="utf-8")
        on = text[text.index("cmd_bluetooth_on") : text.index("cmd_bluetooth_off")]
        self.assertIn("power on", on)
        self.assertIn('sysctl_quiet start "$KB_SERVICE"', on)

    def test_status_emits_json_shape_keys(self):
        text = UI.read_text(encoding="utf-8")
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
        text = UI.read_text(encoding="utf-8")
        self.assertNotIn("head -c", text)
        self.assertIn("cut -c1-17", text)

    def test_settings_qmd_uses_paperhid_ui_only(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        self.assertIn('command: "/home/root/.paperhid/paperhid-ui"', text)
        self.assertNotIn("/home/root/.paperpointer/ui-actions/", text)
        self.assertIn('arguments: ["status", "--fast"]', text)
        self.assertIn('arguments: ["bluetooth-on"]', text)
        self.assertIn("statusBuf", text)
        self.assertIn("JSON.parse", text)
        self.assertIn("applyStatusJson", text)

    def test_settings_qmd_single_flight(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        self.assertIn("uiBusy", text)
        self.assertIn("activeOperation", text)
        self.assertIn("beginOperation", text)
        self.assertIn("finishOperation", text)
        self.assertIn("runMutation", text)
        self.assertIn("statusDebounce", text)
        self.assertIn("interval: 1500", text)
        # No permanent busy if start fails.
        self.assertIn("Could not start command", text)
        self.assertIn("!executor.startCommand(100)", text)
        # No immediate refresh after mutation.
        self.assertNotIn("onMutationFinished", text)
        self.assertNotIn("mutationBusy", text)

    def test_settings_qmd_has_sections(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        for label in (
            "Status",
            "Bluetooth",
            "Paired devices",
            "Keyboard",
            "Language layout",
            "Pointer and cursor",
        ):
            self.assertIn(f'qsTr("{label}")', text)
        self.assertIn('arguments: ["set-layout", "it"]', text)
        self.assertIn('arguments: ["set-layout", "us"]', text)
        self.assertIn('arguments: ["set-layout", "us_intl"]', text)
        self.assertIn("US Intl", text)
        self.assertIn("preferredWidth: 120", text)
        self.assertNotIn("preferredHeight: 48", text)
        self.assertIn("dead keys", text)

    def test_set_layout_command_allowlist(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("set-layout", text)
        self.assertIn("cmd_set_layout", text)
        self.assertIn("set_layout.py", text)
        self.assertIn("xovi/start", text)

    def test_set_layout_helper_prefers_xovi_and_recovers(self):
        helper = (ROOT / "device" / "set_layout.py").read_text(encoding="utf-8")
        self.assertIn("restart_display", helper)
        self.assertIn("_ensure_settings_qmd_present", helper)
        self.assertIn("restore_original", helper)
        self.assertIn("Never depends on a connected Bluetooth keyboard", helper)

    def test_settings_installer_requires_paperhid_ui_not_pointer_service(self):
        text = SETTINGS_INSTALLER.read_text(encoding="utf-8")
        self.assertIn('HOME_PH="/home/root/.paperhid"', text)
        self.assertIn("paperhid-ui", text)
        self.assertIn('UI_BIN="$HOME_PH/paperhid-ui"', text)
        self.assertIn('[ -x "$UI_BIN" ]', text)
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


class TestPaperhidUiArgDispatch(unittest.TestCase):
    """Lightweight pure checks on status arg parsing in the shell source."""

    def test_status_rejects_unknown_args_in_source(self):
        text = UI.read_text(encoding="utf-8")
        self.assertIn("invalid status argument", text)
        # Only --fast is accepted beyond bare status.
        self.assertIn("--fast) fast=1 ;;", text)


if __name__ == "__main__":
    unittest.main()
