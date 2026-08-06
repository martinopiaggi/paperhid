"""Sanity checks for fixed on-device UI action scripts (no device required)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "device" / "ui-actions"
CURSOR_QMD = ROOT / "device" / "paperpointer-cursor.qmd"
SETTINGS_QMD = ROOT / "device" / "paperpointer-settings.qmd"
SETTINGS_INSTALLER = ROOT / "device" / "enable_settings_ui.sh"
CLI = ROOT / "paperpointer" / "cli.py"


class TestUiActionsShipped(unittest.TestCase):
    def test_scripts_exist(self):
        for name in (
            "lib.sh",
            "status.sh",
            "set-hide-ms.sh",
            "set-accel.sh",
            "set-cursor-style.sh",
            "restart.sh",
        ):
            path = UI / name
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("set -eu", text)

    def test_hide_ms_allowlist(self):
        # Wrappers delegate; allow-list lives in paperhid-ui.
        wrap = (UI / "set-hide-ms.sh").read_text(encoding="utf-8")
        self.assertIn("paperhid-ui set-hide-ms", wrap)
        text = (ROOT / "device" / "paperhid-ui").read_text(encoding="utf-8")
        self.assertIn("0|3000|10000|60000", text)
        self.assertIn("cursor_hide_ms", text)

    def test_accel_allowlist(self):
        wrap = (UI / "set-accel.sh").read_text(encoding="utf-8")
        self.assertIn("paperhid-ui set-accel", wrap)
        text = (ROOT / "device" / "paperhid-ui").read_text(encoding="utf-8")
        self.assertIn("1.0|2.0|3.0|4.0", text)

    def test_cursor_style_allowlist(self):
        wrap = (UI / "set-cursor-style.sh").read_text(encoding="utf-8")
        self.assertIn("paperhid-ui set-cursor-style", wrap)
        text = (ROOT / "device" / "paperhid-ui").read_text(encoding="utf-8")
        self.assertIn("cross|win95", text)
        self.assertIn("win95.png", text)

    def test_cursor_qmd_has_no_experimental_settings_panel(self):
        """The independent settings patch must never share the cursor AFFECT."""
        text = CURSOR_QMD.read_text(encoding="utf-8")
        self.assertIn("VERSION 3.28.0.164", text)
        self.assertIn("AFFECT /qml/device/view/main/MainView.qml", text)
        self.assertIn("paperpointer-cursor", text)
        self.assertIn("win95.png", text)
        self.assertIn("cursor_style", text)
        self.assertNotIn("paperpointer-settings-root", text)
        self.assertNotIn("ui-actions/set-hide-ms.sh", text)
        self.assertIn("AsyncCommandExecutor", text)

    def test_settings_qmd_targets_help_not_main_view(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        self.assertIn("VERSION 3.28.0.164", text)
        self.assertIn("AFFECT /qml/device/view/settings/Help.qml", text)
        self.assertNotIn("AFFECT /qml/device/view/main/MainView.qml", text)
        self.assertIn('objectName: "paperpointer-settings-root"', text)
        self.assertIn("Item#itemWrapper > ColumnLayout#column", text)
        self.assertIn("LOCATE BEFORE ArkControls.Body#supportText", text)
        self.assertIn("Layout.fillWidth: true", text)
        self.assertNotIn("y: column.y + supportText.y", text)
        self.assertIn('listeningFor: ["paperpointer.settings.ping"]', text)

    def test_settings_commands_are_fixed_and_allow_listed(self):
        text = SETTINGS_QMD.read_text(encoding="utf-8")
        ui = "/home/root/.paperhid/paperhid-ui"
        self.assertIn(f'command: "{ui}"', text)
        self.assertGreaterEqual(text.count(f'command: "{ui}"'), 5)
        self.assertIn('arguments: ["status", "--fast"]', text)
        self.assertIn('arguments: ["set-hide-ms", "0"]', text)
        self.assertIn('arguments: ["set-hide-ms", "3000"]', text)
        self.assertIn('arguments: ["set-cursor-style", "cross"]', text)
        self.assertIn('arguments: ["set-cursor-style", "win95"]', text)
        self.assertNotIn('command: "/bin/sh"', text)
        self.assertNotIn("/home/root/.paperpointer/ui-actions/", text)

    def test_shipped_win95_skin_exists(self):
        cursors = ROOT / "device" / "cursors"
        self.assertTrue((cursors / "win95.png").is_file())
        self.assertTrue((cursors / "win95.meta").is_file())
        self.assertGreater((cursors / "win95.png").stat().st_size, 100)

    def test_settings_installer_has_rollback_and_cursor_canary(self):
        text = SETTINGS_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("rollback()", text)
        self.assertIn("paperpointer-settings.qmd.rollback", text)
        self.assertIn("cursor-ping", text)
        self.assertIn("wait_for_stable_xochitl", text)
        self.assertIn("systemctl reset-failed xochitl.service", text)
        self.assertNotIn('"$XO" = "$XO_CHECK"', text)
        # Keyboard-only: must not require the pointer daemon.
        self.assertNotIn(
            "systemctl is-active --quiet paperpointer.service",
            text,
        )

    def test_settings_disable_uses_the_same_stable_xochitl_check(self):
        text = CLI.read_text(encoding="utf-8")
        start = text.index("def cmd_disable_settings_ui")
        end = text.index("def cmd_settings_ui_check", start)
        command = text[start:end]
        self.assertIn("wait_for_stable_xochitl", command)
        self.assertIn("systemctl reset-failed xochitl.service", command)
        self.assertNotIn('"$XO" = "$(pidof xochitl', command)
        self.assertNotIn("systemctl is-active --quiet", command)

    def test_enable_cursor_never_deletes_settings_qmd(self):
        cursor = (ROOT / "device" / "enable_cursor.sh").read_text(encoding="utf-8")
        self.assertNotIn("rm -f \"$LEGACY_SETTINGS_QMD\"", cursor)
        self.assertNotIn('rm -f "$QMD_TARGET" "$LEGACY_SETTINGS_QMD"', cursor)
        # Must not remove the live settings panel by name on the success path.
        self.assertNotIn(
            'rm -f "$QMD_TARGET" "$LEGACY_SETTINGS_QMD" "$PING_LOG"',
            cursor,
        )
        # Success path must not delete paperpointer-settings.qmd at all.
        for line in cursor.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "paperpointer-settings.qmd" in stripped and "rm " in stripped:
                self.fail(f"enable_cursor must not rm settings QMD: {stripped}")

    def test_stock_ui_preserves_settings_qmd(self):
        text = CLI.read_text(encoding="utf-8")
        start = text.index("def cmd_stock_ui")
        rest = text[start + 1 :]
        cut = len(rest)
        for marker in ("\ndef cmd_", "\ndef build_", "\ndef dispatch_", "\nPOINTER_"):
            if marker in rest:
                cut = min(cut, rest.index(marker))
        body = text[start : start + 1 + cut]
        self.assertIn("Settings UI preserved", body)
        self.assertIn("never delete the Settings panel QMD", body)
        # Must not rm both cursor and settings in one command.
        self.assertNotIn('rm -f "$QMD" "$SETTINGS_QMD" "$FIFO"', body)
        self.assertIn('rm -f "$QMD" "$FIFO"', body)


if __name__ == "__main__":
    unittest.main()
