"""Mocked CLI dispatch / inventory / entry points — real shipped parsers & dispatch."""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from paperpointer.cli import (
    POINTER_ALL_COMMANDS,
    POINTER_SIMPLE_COMMANDS,
    build_parser as build_pointer_parser,
    dispatch_pointer,
    register_pointer_commands,
    shared_flag_parser,
)


def _parse_expect_exit(parser, argv):
    """parse_args that must SystemExit; swallow argparse usage noise."""
    with contextlib.redirect_stderr(io.StringIO()):
        with contextlib.redirect_stdout(io.StringIO()):
            parser.parse_args(argv)


# Supported cursor/settings commands (QML path only; no abandoned FB tooling).
REQUIRED_CURSOR_COMMANDS = (
    "disable-settings-ui",
    "settings-ui-check",
    "enable-cursor",
    "enable-settings-ui",
    "stock-ui",
)
REMOVED_LEGACY_COMMANDS = ("enable-fb", "fb-config")


class TestPointerInventory(unittest.TestCase):
    def test_inventory_includes_supported_cursor(self):
        for name in REQUIRED_CURSOR_COMMANDS:
            self.assertIn(name, POINTER_ALL_COMMANDS)
            self.assertIn(name, POINTER_SIMPLE_COMMANDS)

    def test_inventory_excludes_abandoned_fb(self):
        for name in REMOVED_LEGACY_COMMANDS:
            self.assertNotIn(name, POINTER_ALL_COMMANDS)
            self.assertNotIn(name, POINTER_SIMPLE_COMMANDS)

    def test_parser_registers_all_simple(self):
        p = build_pointer_parser()
        # argparse stores choices after parse failures; probe via subparsers
        actions = [a for a in p._subparsers._group_actions if a.dest == "cmd"]
        self.assertTrue(actions)
        choices = set(actions[0].choices.keys())
        for name in POINTER_ALL_COMMANDS:
            self.assertIn(name, choices, f"missing command {name}")

    def test_cursor_rate_bounds(self):
        p = build_pointer_parser()
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["cursor-rate", "0"])
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["cursor-rate", "99"])
        args = p.parse_args(["cursor-rate", "30"])
        self.assertEqual(args.hz, 30)

    def test_cursor_style_choices(self):
        p = build_pointer_parser()
        args = p.parse_args(["cursor-style", "win95"])
        self.assertEqual(args.style, "win95")
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["cursor-style", "nope"])

    def test_flags_before_and_after_subcommand(self):
        p = build_pointer_parser()
        a = p.parse_args(["--password", "x", "--host", "1.1.1.1", "status"])
        self.assertEqual(a.password, "x")
        self.assertEqual(a.host, "1.1.1.1")
        self.assertEqual(a.cmd, "status")
        b = p.parse_args(["status", "--password", "y", "--host", "2.2.2.2"])
        self.assertEqual(b.password, "y")
        self.assertEqual(b.host, "2.2.2.2")

    def test_test_tap_positionals(self):
        p = build_pointer_parser()
        args = p.parse_args(["test-tap", "100", "200"])
        self.assertEqual(args.x, 100)
        self.assertEqual(args.y, 200)

    def test_input_monitor_seconds(self):
        p = build_pointer_parser()
        args = p.parse_args(["input-monitor", "5"])
        self.assertEqual(args.seconds, 5)


class TestDispatchPointer(unittest.TestCase):
    def _args(self, cmd, **kw):
        ns = argparse.Namespace(cmd=cmd, **kw)
        return ns

    def test_every_simple_command_routes(self):
        conn = MagicMock(name="paramiko")
        for name in POINTER_SIMPLE_COMMANDS:
            handler = f"cmd_{name.replace('-', '_')}"
            with patch(f"paperpointer.cli.{handler}", return_value=7) as m:
                code = dispatch_pointer(self._args(name), conn)
                self.assertEqual(code, 7, name)
                m.assert_called_once_with(conn)

    def test_cursor_rate_dispatch(self):
        conn = MagicMock()
        with patch("paperpointer.cli.cmd_cursor_rate", return_value=0) as m:
            code = dispatch_pointer(self._args("cursor-rate", hz=12), conn)
            self.assertEqual(code, 0)
            m.assert_called_once_with(conn, 12)

    def test_cursor_style_dispatch(self):
        conn = MagicMock()
        with patch("paperpointer.cli.cmd_cursor_style", return_value=0) as m:
            dispatch_pointer(self._args("cursor-style", style="cross"), conn)
            m.assert_called_once_with(conn, "cross")

    def test_cursor_style_uploads_daemon_and_ppd_package(self):
        """Style redeploy stages then activates facade+ppd together."""
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "put_daemon_sources") as put_daemon:
            with patch.object(ppcli, "activate_daemon_sources", return_value=0) as act:
                with patch.object(ppcli, "put_file_atomic"):
                    with patch.object(ppcli, "run", return_value=("", "", 0)):
                        code = ppcli.cmd_cursor_style(conn, "cross")
        self.assertEqual(code, 0)
        put_daemon.assert_called_once_with(conn)
        act.assert_called_once()
        self.assertEqual(act.call_args.kwargs.get("restart"), False)

    def test_cursor_rate_uploads_daemon_and_ppd_package(self):
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "put_daemon_sources") as put_daemon:
            with patch.object(ppcli, "activate_daemon_sources", return_value=0) as act:
                code = ppcli.cmd_cursor_rate(conn, 12)
        self.assertEqual(code, 0)
        put_daemon.assert_called_once_with(conn)
        act.assert_called_once()
        self.assertEqual(act.call_args.kwargs.get("cursor_hz"), 12)
        self.assertEqual(act.call_args.kwargs.get("restart"), True)

    def test_put_daemon_sources_stages_facade_and_ppd_next(self):
        """Staging must not write live ppd/; activation swaps both."""
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "put_file_atomic") as put_file:
            with patch.object(ppcli, "put_tree") as put_tree:
                with patch.object(ppcli, "run", return_value=("", "", 0)) as run_mock:
                    daemon, ppd = ppcli.put_daemon_sources(conn)
        put_file.assert_called_once()
        args, _ = put_file.call_args
        self.assertEqual(args[1], ppcli.DEVICE_DIR / "paperpointerd.py")
        self.assertEqual(args[2], ppcli.DAEMON_CANDIDATE)
        self.assertEqual(daemon, ppcli.DAEMON_CANDIDATE)
        put_tree.assert_called_once()
        t_args, _ = put_tree.call_args
        self.assertEqual(t_args[1], ppcli.PPD_DIR)
        self.assertEqual(t_args[2], ppcli.PPD_NEXT)
        self.assertEqual(ppd, ppcli.PPD_NEXT)
        # Live tree is not the staging target.
        self.assertNotEqual(t_args[2], ppcli.PPD_LIVE)
        self.assertTrue(
            any("rm -rf" in str(c) and "ppd.next" in str(c) for c in run_mock.call_args_list)
            or run_mock.called
        )

    def test_activate_daemon_sources_script_swaps_both_paths(self):
        """Remote script must validate staged pair and roll back facade + ppd."""
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "run", return_value=("OK\n", "", 0)) as run_mock:
            code = ppcli.activate_daemon_sources(conn, cursor_hz=12, restart=True)
        self.assertEqual(code, 0)
        script = run_mock.call_args[0][1]
        self.assertIn("ppd.next", script)
        self.assertIn("paperpointerd.py.candidate", script)
        self.assertIn("HAD_PPD", script)
        self.assertIn("OLD_PPD", script)
        self.assertIn("ARMED=1", script)
        self.assertIn("mv -f \"$PPD_NEXT\" \"$PPD\"", script)
        self.assertIn("mv -f \"$CANDIDATE\" \"$DAEMON\"", script)
        # Joint rollback restores ppd, not only facade+conf.
        self.assertIn('mv -f "$OLD_PPD" "$PPD"', script)
        self.assertIn("cursor_hz=12", script)

    def test_test_tap_dispatch(self):
        conn = MagicMock()
        with patch("paperpointer.cli.cmd_test_tap", return_value=0) as m:
            dispatch_pointer(self._args("test-tap", x=1, y=2), conn)
            m.assert_called_once_with(conn, 1, 2)

    def test_input_monitor_clamps(self):
        conn = MagicMock()
        with patch("paperpointer.cli.cmd_input_monitor", return_value=0) as m:
            dispatch_pointer(self._args("input-monitor", seconds=100), conn)
            m.assert_called_once_with(conn, 30)
            m.reset_mock()
            dispatch_pointer(self._args("input-monitor", seconds=0), conn)
            m.assert_called_once_with(conn, 1)

    def test_unknown_returns_one(self):
        self.assertEqual(dispatch_pointer(self._args("nope"), MagicMock()), 1)


class TestRootPointerDispatchCleanup(unittest.TestCase):
    """Root ``cli.py pointer`` owns connect/close; library dispatch stays pure."""

    def test_root_cmd_pointer_closes_connection_and_propagates(self):
        import cli as root_cli

        fake = MagicMock()
        args = argparse.Namespace(
            host="10.11.99.1",
            ip=None,
            password="pw",
            save_password=False,
            timeout=15,
            pointer_cmd="status",
            cmd=None,
        )
        with patch("host_cli.session.password_from_args", return_value="pw"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch(
                    "host_cli.session.open_pointer_paramiko",
                    return_value=(fake, "h", "p"),
                ) as conn:
                    with patch(
                        "paperpointer.cli.dispatch_pointer", return_value=42
                    ) as disp:
                        code = root_cli.cmd_pointer(args)
        self.assertEqual(code, 42)
        conn.assert_called_once()
        disp.assert_called_once()
        fake.close.assert_called_once()

    def test_root_cmd_pointer_closes_on_dispatch_error(self):
        import cli as root_cli

        fake = MagicMock()
        args = argparse.Namespace(
            host="10.11.99.1",
            ip=None,
            password="pw",
            save_password=False,
            timeout=15,
            pointer_cmd="status",
            cmd=None,
        )
        with patch("host_cli.session.password_from_args", return_value="pw"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch(
                    "host_cli.session.open_pointer_paramiko",
                    return_value=(fake, "h", "p"),
                ):
                    with patch(
                        "paperpointer.cli.dispatch_pointer",
                        side_effect=RuntimeError("boom"),
                    ):
                        with self.assertRaises(RuntimeError):
                            root_cli.cmd_pointer(args)
        fake.close.assert_called_once()


class TestPhase4HostSurface(unittest.TestCase):
    """Desktop GUI and AppLoad removed; layout via unified CLI + Settings."""

    def test_desktop_gui_and_nativeapp_removed(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "main.py").exists())
        self.assertFalse((root / "ui").is_dir())
        self.assertFalse((root / "nativeapp").is_dir())
        self.assertFalse((root / "core" / "native_app_installer.py").is_file())

    def test_parser_has_layout_not_native_app(self):
        import cli as root_cli

        p = root_cli.build_parser()
        actions = [a for a in p._subparsers._group_actions if a.dest == "command"]
        choices = set(actions[0].choices.keys())
        self.assertIn("set-layout", choices)
        self.assertIn("settings-ui", choices)
        self.assertIn("repair-ui", choices)  # alias
        self.assertNotIn("install-native-app", choices)
        self.assertNotIn("uninstall-native-app", choices)
        self.assertNotIn("refuse-native", choices)

    def test_settings_ui_calls_enable_settings(self):
        import argparse
        import cli as root_cli

        class Cp1252Stream(io.StringIO):
            def write(self, text):
                text.encode("cp1252")
                return super().write(text)

        conn = MagicMock()
        args = argparse.Namespace(
            host="10.11.99.1",
            ip=None,
            password="pw",
            save_password=False,
        )
        with patch(
            "host_cli.session.open_pointer_paramiko",
            return_value=(conn, "10.11.99.1", "pw"),
        ):
            with patch(
                "paperpointer.cli.cmd_enable_settings_ui", return_value=0
            ) as en:
                with contextlib.redirect_stdout(Cp1252Stream()):
                    code = root_cli.cmd_settings_ui(args)
        self.assertEqual(code, 0)
        en.assert_called_once_with(conn)
        conn.close.assert_called_once()
        # Alias still points at the same handler
        self.assertIs(root_cli.cmd_repair_ui, root_cli.cmd_settings_ui)

    def test_set_layout_resolves_display_and_key(self):
        import cli as root_cli

        p = root_cli.build_parser()
        a = p.parse_args(["set-layout", "--layout", "it"])
        self.assertEqual(a.layout, "it")
        b = p.parse_args(["set-layout", "--layout", "Italian"])
        self.assertEqual(b.layout, "Italian")

    def test_set_layout_unknown_exits_without_ssh(self):
        import cli as root_cli
        import argparse

        args = argparse.Namespace(
            layout="not-a-real-layout",
            host="10.11.99.1",
            ip=None,
            password="x",
            save_password=False,
        )
        with patch("host_cli.session.open_keyboard_ssh") as open_ssh:
            code = root_cli.cmd_set_layout(args)
        self.assertEqual(code, 2)
        open_ssh.assert_not_called()


class TestUnifiedCliEntry(unittest.TestCase):
    def test_paperpointer_module_is_not_public_cli(self):
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            [sys.executable, "-m", "paperpointer"],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("cli.py pointer", r.stderr)

    def test_unified_help_lists_supported_pointer_commands(self):
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(
            [sys.executable, "cli.py", "pointer", "--help"],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        help_text = r.stdout + r.stderr
        for name in REQUIRED_CURSOR_COMMANDS:
            self.assertIn(name, help_text)
        for name in REMOVED_LEGACY_COMMANDS:
            self.assertNotIn(name, help_text)

        p = build_pointer_parser()
        actions = [a for a in p._subparsers._group_actions if a.dest == "cmd"]
        choices = actions[0].choices
        for name in REQUIRED_CURSOR_COMMANDS:
            self.assertIn(name, choices)
        for name in REMOVED_LEGACY_COMMANDS:
            self.assertNotIn(name, choices)


class TestPointerProbeParse(unittest.TestCase):
    """Drive shipped parse_pointer_probe_output + cmd_status path."""

    def test_inactive_not_confused_with_is_failed_active(self):
        """systemctl is-failed prints 'active' when unit is not-failed (incl. inactive)."""
        import cli as root_cli

        # Real multi-command shape after the labeled-marker fix, plus the
        # dangerous legacy bare-token mix that used to flip inactive→active.
        labeled = (
            "HOME_YES\n"
            "UNIT_YES\n"
            "ACTIVE:inactive\n"
            "FAILED:active\n"  # is-failed "active" == not failed
        )
        facts = root_cli.parse_pointer_probe_output(labeled)
        self.assertTrue(facts["home_present"])
        self.assertTrue(facts["unit_file_present"])
        self.assertIs(facts["is_active"], False)
        self.assertFalse(facts["is_failed"])

        from core.status_merge import classify_pointer_status, merge_exit_codes

        ptr = classify_pointer_status(
            home_present=facts["home_present"],
            unit_file_present=facts["unit_file_present"],
            is_active=facts["is_active"],
            is_failed=facts["is_failed"],
        )
        self.assertEqual(ptr.state, "inactive")
        self.assertEqual(merge_exit_codes(0, ptr), 1)

    def test_active_running_healthy(self):
        import cli as root_cli

        facts = root_cli.parse_pointer_probe_output(
            "HOME_YES\nUNIT_YES\nACTIVE:active\nFAILED:active\n"
        )
        self.assertIs(facts["is_active"], True)
        self.assertFalse(facts["is_failed"])
        from core.status_merge import classify_pointer_status

        ptr = classify_pointer_status(**{
            k: facts[k]
            for k in ("home_present", "unit_file_present", "is_active", "is_failed")
        })
        self.assertEqual(ptr.state, "active")
        self.assertEqual(ptr.exit_code, 0)

    def test_failed_unit(self):
        import cli as root_cli

        facts = root_cli.parse_pointer_probe_output(
            "HOME_YES\nUNIT_YES\nACTIVE:failed\nFAILED:failed\n"
        )
        self.assertIs(facts["is_active"], False)
        self.assertTrue(facts["is_failed"])

    def test_not_installed(self):
        import cli as root_cli

        facts = root_cli.parse_pointer_probe_output(
            "HOME_NO\nUNIT_ETC_NO\nUNIT_USR_NO\nACTIVE:inactive\nFAILED:active\n"
        )
        from core.status_merge import classify_pointer_status, merge_exit_codes

        ptr = classify_pointer_status(
            home_present=facts["home_present"],
            unit_file_present=facts["unit_file_present"],
            is_active=facts["is_active"],
            is_failed=facts["is_failed"],
        )
        self.assertTrue(ptr.is_absent)
        self.assertEqual(merge_exit_codes(0, ptr), 0)

    def test_home_only_staged_exit_zero(self):
        """After uninstall --pointer, home remains; no unit → staged, exit 0."""
        import cli as root_cli
        from core.status_merge import classify_pointer_status, merge_exit_codes

        facts = root_cli.parse_pointer_probe_output(
            "HOME_YES\nUNIT_ETC_NO\nUNIT_USR_NO\nACTIVE:inactive\nFAILED:active\n"
        )
        self.assertTrue(facts["home_present"])
        self.assertFalse(facts["unit_file_present"])
        ptr = classify_pointer_status(
            home_present=facts["home_present"],
            unit_file_present=facts["unit_file_present"],
            is_active=facts["is_active"],
            is_failed=facts["is_failed"],
        )
        self.assertEqual(ptr.state, "staged")
        self.assertEqual(merge_exit_codes(0, ptr), 0)

    def test_unit_usr_path_counts_as_present(self):
        import cli as root_cli

        facts = root_cli.parse_pointer_probe_output(
            "HOME_YES\nUNIT_ETC_NO\nUNIT_USR_YES\nACTIVE:active\nFAILED:active\n"
        )
        self.assertTrue(facts["unit_file_present"])
        self.assertTrue(facts["unit_usr_present"])
        self.assertFalse(facts["unit_etc_present"])

    def _run_cmd_status(
        self,
        probe_stdout,
        kb_state,
        save_password=False,
        probe_stderr="",
        probe_code=0,
    ):
        import argparse
        import cli as root_cli

        fake_c = MagicMock()
        fake_ssh = MagicMock()
        args = argparse.Namespace(
            host="10.11.99.1",
            ip=None,
            password="x",
            save_password=save_password,
            timeout=15,
        )

        def fake_run(c, cmd, timeout=20):
            # Pointer unit probe
            if "ACTIVE:" in cmd and "UNIT_ETC" in cmd:
                self.assertIn("FAILED:", cmd)
                self.assertIn("UNIT_USR", cmd)
                return probe_stdout, probe_stderr, probe_code
            # Settings / XOVI probe (second run from cmd_status)
            if "QMD_ACTIVE" in cmd or "paperpointer-settings.qmd" in cmd:
                return (
                    "QMD_ACTIVE=no\nQMD_STAGED=no\nXOVI_START=no\n"
                    "XOVI_MB=no\nXOCHITL=yes\n",
                    "",
                    0,
                )
            self.fail(f"unexpected run() cmd: {cmd[:120]}")

        with patch("host_cli.session.password_from_args", return_value="x"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch(
                    "host_cli.session.open_keyboard_ssh",
                    return_value=(fake_ssh, "h", "p"),
                ):
                    with patch(
                        "host_cli.app.device_mod.detect",
                        return_value={
                            "label": "Paper Pro",
                            "model": "paper_pro",
                            "img_version": "3.28.0.164",
                        },
                    ):
                        with patch(
                            "host_cli.app.bluetooth.verify_device_state",
                            return_value=kb_state,
                        ):
                            with patch(
                                "host_cli.session.open_pointer_paramiko",
                                return_value=(fake_c, "h", "p"),
                            ):
                                with patch(
                                    "paperpointer.sshutil.run",
                                    side_effect=fake_run,
                                ):
                                    code = root_cli.cmd_status(args)
        return code, fake_c, fake_ssh

    def test_cmd_status_inactive_exits_nonzero(self):
        """Mocked open_pointer_paramiko+run with real multi-line probe stdout.

        Keyboard path succeeds so exit code comes only from pointer classification.
        """
        probe_stdout = (
            "HOME_YES\n"
            "UNIT_ETC_YES\n"
            "UNIT_USR_NO\n"
            "ACTIVE:inactive\n"
            "FAILED:active\n"
        )
        code, fake_c, fake_ssh = self._run_cmd_status(
            probe_stdout,
            {
                "service_installed": True,
                "service_present": True,
                "service_active": True,
                "service_failed": False,
            },
        )
        self.assertEqual(code, 1)
        fake_c.close.assert_called_once()
        fake_ssh.disconnect.assert_called_once()

    def test_cmd_status_home_only_after_uninstall_exits_zero(self):
        probe_stdout = (
            "HOME_YES\n"
            "UNIT_ETC_NO\n"
            "UNIT_USR_NO\n"
            "ACTIVE:inactive\n"
            "FAILED:active\n"
        )
        code, _, _ = self._run_cmd_status(
            probe_stdout,
            {
                "service_present": True,
                "service_active": True,
                "service_failed": False,
                "service_installed": True,
            },
        )
        self.assertEqual(code, 0)

    def test_cmd_status_keyboard_inactive_exits_nonzero(self):
        """Installed but stopped keyboard service must fail merged status."""
        probe_stdout = (
            "HOME_NO\n"
            "UNIT_ETC_NO\n"
            "UNIT_USR_NO\n"
            "ACTIVE:inactive\n"
            "FAILED:active\n"
        )
        code, _, _ = self._run_cmd_status(
            probe_stdout,
            {
                "service_present": True,
                "service_active": False,
                "service_failed": False,
                "service_installed": True,
            },
        )
        self.assertEqual(code, 1)

    def test_cmd_status_failed_pointer_probe_exits_nonzero(self):
        code, _, _ = self._run_cmd_status(
            "",
            {
                "service_present": True,
                "service_active": True,
                "service_failed": False,
                "service_installed": True,
            },
            probe_stderr="remote shell failed",
            probe_code=127,
        )
        self.assertEqual(code, 1)


class TestDelegatedKeyboardCommands(unittest.TestCase):
    def test_successful_command_saves_requested_password(self):
        import cli as root_cli

        with patch.object(root_cli.kb, "cmd_scan", return_value=0) as handler:
            with patch("host_cli.app.maybe_save_password") as save:
                code = root_cli.main(
                    ["scan", "--password", "pw", "--save-password"]
                )

        self.assertEqual(code, 0)
        self.assertFalse(handler.call_args.args[0].save_password)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[1:], ("10.11.99.1", "pw"))

    def test_delegated_cli_error_keeps_its_exit_code(self):
        import cli as root_cli

        stderr = io.StringIO()
        with patch.object(
            root_cli.kb,
            "cmd_scan",
            side_effect=root_cli.kb.CliError("no password", code=2),
        ):
            with contextlib.redirect_stderr(stderr):
                code = root_cli.main(["scan", "--password", "pw"])

        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue(), "error: no password\n")


class TestRootCliInstallModes(unittest.TestCase):
    def test_install_requires_mode(self):
        import cli as root_cli

        p = root_cli.build_parser()
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["install"])

    def test_install_modes_mutex(self):
        import cli as root_cli

        p = root_cli.build_parser()
        a = p.parse_args(["install", "--keyboard"])
        self.assertTrue(a.keyboard)
        b = p.parse_args(["install", "--pointer"])
        self.assertTrue(b.pointer)
        c = p.parse_args(["install", "--all"])
        self.assertTrue(c.all)
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["install", "--keyboard", "--pointer"])

    def test_global_flags_before_and_after_subcommand(self):
        """README form: install --all --save-password (flag after subcommand)."""
        import cli as root_cli

        p = root_cli.build_parser()
        # README main path — must not argparse-reject.
        after = p.parse_args(
            ["install", "--all", "--save-password", "--password", "pw"]
        )
        self.assertTrue(after.all)
        self.assertTrue(after.save_password)
        self.assertEqual(after.password, "pw")
        # Also still works before the subcommand.
        before = p.parse_args(
            ["--save-password", "--password", "x", "--host", "1.2.3.4", "install", "--all"]
        )
        self.assertTrue(before.save_password)
        self.assertEqual(before.password, "x")
        self.assertEqual(before.host, "1.2.3.4")
        self.assertTrue(before.all)
        # Detect with flag after
        det = p.parse_args(["detect", "--timeout", "9"])
        self.assertEqual(det.timeout, 9)
        self.assertFalse(det.save_password)

    def test_install_service_alias_removed(self):
        """Never-released PaperWriter-style aliases are not public."""
        import cli as root_cli

        p = root_cli.build_parser()
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["install-service", "--wait", "30"])
        with self.assertRaises(SystemExit):
            _parse_expect_exit(p, ["uninstall-service"])

    def test_failed_auth_never_saves_password(self):
        """--save-password must not write config when connect fails."""
        import argparse
        import cli as root_cli

        args = argparse.Namespace(
            keyboard=False,
            pointer=True,
            all=False,
            host=None,
            ip="10.11.99.1",
            password="wrong",
            save_password=True,
            timeout=15,
            wait=12,
        )
        with patch("host_cli.session.password_from_args", return_value="wrong"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch(
                    "host_cli.session.open_pointer_paramiko",
                    side_effect=RuntimeError("auth failed"),
                ):
                    with patch.object(root_cli.config, "save") as save:
                        with patch("host_cli.session.maybe_save_password") as maybe:
                            # Drive real cmd_install path: connect fails before save
                            code = root_cli.cmd_install(args)
        self.assertEqual(code, 1)
        # Connection failed: save helper must not have been called with success path.
        # cmd_install only calls maybe_save_password after open succeeds.
        maybe.assert_not_called()
        save.assert_not_called()

    def test_install_all_skips_pointer_on_keyboard_fail(self):
        import cli as root_cli

        args = argparse.Namespace(
            keyboard=False,
            pointer=False,
            all=True,
            host=None,
            ip="10.11.99.1",
            password="x",
            save_password=False,
            timeout=15,
            wait=12,
        )
        with patch("host_cli.session.password_from_args", return_value="x"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch.object(root_cli.kb, "cmd_install_service", return_value=5):
                    with patch(
                        "paperpointer.cli.cmd_install"
                    ) as ptr_install:
                        code = root_cli.cmd_install(args)
        self.assertEqual(code, 5)
        ptr_install.assert_not_called()

    def test_install_all_partial_on_pointer_fail(self):
        import cli as root_cli

        args = argparse.Namespace(
            keyboard=False,
            pointer=False,
            all=True,
            host=None,
            ip="10.11.99.1",
            password="x",
            save_password=False,
            timeout=15,
            wait=12,
        )
        fake_c = MagicMock()
        with patch("host_cli.session.password_from_args", return_value="x"):
            with patch("host_cli.session.host_from_args", return_value="10.11.99.1"):
                with patch.object(root_cli.kb, "cmd_install_service", return_value=0):
                    with patch(
                        "host_cli.session.open_pointer_paramiko",
                        return_value=(fake_c, "h", "p"),
                    ):
                        with patch(
                            "paperpointer.cli.cmd_install", return_value=9
                        ) as ptr:
                            code = root_cli.cmd_install(args)
        self.assertEqual(code, 9)
        ptr.assert_called_once()
        fake_c.close.assert_called_once()

    def test_preflight_called_before_upload(self):
        """With bootstrap disabled, missing Python aborts before upload."""
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "preflight_tablet_python", return_value=2) as pre:
            with patch.object(ppcli, "put_tree") as put:
                with patch.object(ppcli, "bootstrap_tablet_python") as boot:
                    code = ppcli.cmd_install(conn, bootstrap=False)
        self.assertEqual(code, 2)
        pre.assert_called_once_with(conn)
        boot.assert_not_called()
        put.assert_not_called()

    def test_bootstrap_runs_when_python_missing(self):
        """Vanilla tablet: auto-bootstrap then upload when preflight becomes OK."""
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(
            ppcli, "preflight_tablet_python", side_effect=[2, 0]
        ) as pre:
            with patch.object(ppcli, "bootstrap_tablet_python") as boot:
                with patch.object(ppcli, "put_tree") as put:
                    with patch.object(ppcli, "run", return_value=("", "", 0)):
                        code = ppcli.cmd_install(conn, bootstrap=True)
        self.assertEqual(code, 0)
        self.assertEqual(pre.call_count, 2)
        boot.assert_called_once_with(conn)
        put.assert_called_once()

    def test_bootstrap_failure_does_not_upload(self):
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "preflight_tablet_python", return_value=2):
            with patch.object(
                ppcli,
                "bootstrap_tablet_python",
                side_effect=RuntimeError("no wifi"),
            ):
                with patch.object(ppcli, "put_tree") as put:
                    code = ppcli.cmd_install(conn, bootstrap=True)
        self.assertEqual(code, 2)
        put.assert_not_called()


class TestEntwareBootstrapUsability(unittest.TestCase):
    """Partial Entware must not be mistaken for a usable install."""

    def _ssh(self, results):
        """results: list of (out, err, code) or a callable(cmd)->triple."""
        ssh = MagicMock()

        def exec_side_effect(cmd, timeout=5):
            if callable(results):
                return results(cmd)
            if not results:
                return "", "", 1
            return results.pop(0)

        ssh.exec.side_effect = exec_side_effect
        return ssh

    def test_opkg_usable_requires_version_exit_zero(self):
        from core import tablet_python as tp

        # Binary present but --version fails → not usable
        def results(cmd):
            if "mount" in cmd and "bind" in cmd:
                return "", "", 0
            if "opkg --version" in cmd:
                return "", "segfault", 1
            return "", "", 1

        self.assertFalse(tp._opkg_usable(self._ssh(results)))

        def ok(cmd):
            if "opkg --version" in cmd:
                return "opkg version 1.0", "", 0
            return "", "", 0

        self.assertTrue(tp._opkg_usable(self._ssh(ok)))

    def test_tablet_python_usable_requires_exec(self):
        from core import tablet_python as tp

        def broken(cmd):
            if "mount" in cmd:
                return "", "", 0
            if "python3 -c" in cmd:
                return "", "ImportError", 1
            return "", "", 1

        self.assertFalse(tp._tablet_python_usable(self._ssh(broken)))

        def ok(cmd):
            if "python3 -c" in cmd:
                return "3", "", 0
            return "", "", 0

        self.assertTrue(tp._tablet_python_usable(self._ssh(ok)))

    def test_ensure_entware_cleans_partial_debris_before_reinstall(self):
        from core import tablet_python as tp

        calls = []

        def results(cmd):
            calls.append(cmd)
            # First usability probe fails; debris present; then reinstall path
            if "opkg --version" in cmd:
                # After cleanup+install, second phase still fails → error path
                return "", "", 1
            if "test -d /home/root/.entware" in cmd and "rmpp_entware" in cmd:
                return "", "", 0  # debris present
            if "rm -rf /home/root/.entware" in cmd:
                return "", "", 0
            if "rmpp_entware.sh" in cmd and "wget" in cmd:
                return "partial fail", "network", 1
            return "", "", 0

        ssh = self._ssh(results)
        with self.assertRaises(RuntimeError) as ctx:
            tp._ensure_entware(ssh, say=lambda m: None)
        self.assertIn("entware install failed", str(ctx.exception))
        # Must have attempted cleanup of partial tree (before and/or after fail)
        cleaned = [c for c in calls if "rm -rf /home/root/.entware" in c]
        self.assertTrue(cleaned, "expected partial Entware cleanup")

    def test_ensure_entware_skips_install_when_opkg_usable(self):
        from core import tablet_python as tp

        def results(cmd):
            if "opkg --version" in cmd:
                return "opkg 1", "", 0
            return "", "", 0

        ssh = self._ssh(results)
        tp._ensure_entware(ssh, say=lambda m: None)
        # Simpler: no wget
        all_cmds = [call.args[0] for call in ssh.exec.call_args_list]
        self.assertFalse(any("wget" in c and "rmpp_entware" in c for c in all_cmds))

    def test_ensure_python3_does_not_accept_non_opt_python(self):
        """command -v python3 alone must not short-circuit Entware python."""
        from core import tablet_python as tp

        state = {"n": 0}

        def results(cmd):
            if "python3 -c" in cmd:
                # Never usable → forces install then still fails
                return "", "", 1
            if "opkg update" in cmd or "opkg install" in cmd:
                state["n"] += 1
                return "Installing python3", "", 0
            return "", "", 0

        ssh = self._ssh(results)
        with self.assertRaises(RuntimeError) as ctx:
            tp._ensure_python3(ssh, say=lambda m: None)
        self.assertIn("Python 3 install failed", str(ctx.exception))
        self.assertGreaterEqual(state["n"], 1)


class TestRegisterOnCustomParser(unittest.TestCase):
    def test_register_pointer_commands_api(self):
        shared = shared_flag_parser()
        p = argparse.ArgumentParser(parents=[shared])
        sub = p.add_subparsers(dest="cmd", required=True)
        register_pointer_commands(sub, shared)
        args = p.parse_args(["probe"])
        self.assertEqual(args.cmd, "probe")


if __name__ == "__main__":
    unittest.main()
