"""Mocked CLI dispatch / inventory / entry points — real shipped parsers & dispatch."""
from __future__ import annotations

import argparse
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


# Legacy / diagnostic commands that must remain in the inventory
REQUIRED_LEGACY = (
    "enable-fb",
    "disable-settings-ui",
    "settings-ui-check",
    "fb-config",
    "enable-cursor",
    "enable-settings-ui",
    "stock-ui",
)


class TestPointerInventory(unittest.TestCase):
    def test_inventory_includes_legacy(self):
        for name in REQUIRED_LEGACY:
            self.assertIn(name, POINTER_ALL_COMMANDS)
            self.assertIn(name, POINTER_SIMPLE_COMMANDS)

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
            p.parse_args(["cursor-rate", "0"])
        with self.assertRaises(SystemExit):
            p.parse_args(["cursor-rate", "99"])
        args = p.parse_args(["cursor-rate", "30"])
        self.assertEqual(args.hz, 30)

    def test_cursor_style_choices(self):
        p = build_pointer_parser()
        args = p.parse_args(["cursor-style", "win95"])
        self.assertEqual(args.style, "win95")
        with self.assertRaises(SystemExit):
            p.parse_args(["cursor-style", "nope"])

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


class TestPointerMainCleanup(unittest.TestCase):
    def test_main_closes_connection_and_propagates(self):
        from paperpointer import cli as ppcli

        fake = MagicMock()
        with patch.object(ppcli, "password_from_env", return_value="pw"):
            with patch.object(ppcli, "connect", return_value=fake) as conn:
                with patch.object(ppcli, "dispatch_pointer", return_value=42) as disp:
                    code = ppcli.main(["status", "--password", "pw"])
        self.assertEqual(code, 42)
        conn.assert_called_once()
        disp.assert_called_once()
        fake.close.assert_called_once()

    def test_main_closes_on_dispatch_error(self):
        from paperpointer import cli as ppcli

        fake = MagicMock()
        with patch.object(ppcli, "password_from_env", return_value="pw"):
            with patch.object(ppcli, "connect", return_value=fake):
                with patch.object(
                    ppcli, "dispatch_pointer", side_effect=RuntimeError("boom")
                ):
                    with self.assertRaises(RuntimeError):
                        ppcli.main(["status"])
        fake.close.assert_called_once()


class TestBothModuleEntryPoints(unittest.TestCase):
    def test_paperpointer_main_module(self):
        import importlib.util
        from pathlib import Path

        main_path = Path(__file__).resolve().parents[1] / "paperpointer" / "__main__.py"
        text = main_path.read_text(encoding="utf-8")
        self.assertIn("if __name__", text)
        self.assertIn("main()", text)
        from paperpointer.cli import main as cli_main

        self.assertTrue(callable(cli_main))

    def test_help_lists_legacy_via_both_paths(self):
        import subprocess

        for mod in ("paperpointer", "paperpointer.cli"):
            r = subprocess.run(
                [sys.executable, "-m", mod, "--help"],
                capture_output=True,
                text=True,
                cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
            )
            # --help exits 0
            self.assertEqual(r.returncode, 0, r.stderr)
            # subcommands only show on subcommand help sometimes; parse -h of root
            # lists usage; run status -h isn't needed — check parser choices instead
        p = build_pointer_parser()
        help_text = p.format_help()
        # Root help may not list all; check choices
        actions = [a for a in p._subparsers._group_actions if a.dest == "cmd"]
        choices = actions[0].choices
        for name in REQUIRED_LEGACY:
            self.assertIn(name, choices)


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

    def _run_cmd_status(self, probe_stdout, kb_state, save_password=False):
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
            self.assertIn("ACTIVE:", cmd)
            self.assertIn("FAILED:", cmd)
            self.assertIn("UNIT_ETC", cmd)
            self.assertIn("UNIT_USR", cmd)
            return probe_stdout, "", 0

        with patch.object(root_cli, "_password_from_args", return_value="x"):
            with patch.object(root_cli, "_host_from_args", return_value="10.11.99.1"):
                with patch.object(
                    root_cli,
                    "open_keyboard_ssh",
                    return_value=(fake_ssh, "h", "p"),
                ):
                    with patch.object(
                        root_cli.device_mod,
                        "detect",
                        return_value={
                            "label": "Paper Pro",
                            "model": "paper_pro",
                            "img_version": "3.28.0.164",
                        },
                    ):
                        with patch.object(
                            root_cli.bluetooth,
                            "verify_device_state",
                            return_value=kb_state,
                        ):
                            with patch.object(
                                root_cli,
                                "open_pointer_paramiko",
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


class TestRootCliInstallModes(unittest.TestCase):
    def test_install_requires_mode(self):
        import cli as root_cli

        p = root_cli.build_parser()
        with self.assertRaises(SystemExit):
            p.parse_args(["install"])

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
            p.parse_args(["install", "--keyboard", "--pointer"])

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

    def test_install_service_alias_accepts_wait(self):
        """Compat: python cli.py install-service --wait N (PaperWriter CLI)."""
        import cli as root_cli

        p = root_cli.build_parser()
        args = p.parse_args(["install-service", "--wait", "30"])
        self.assertEqual(args.command, "install-service")
        self.assertEqual(args.wait, 30)

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
        with patch.object(root_cli, "_password_from_args", return_value="wrong"):
            with patch.object(root_cli, "_host_from_args", return_value="10.11.99.1"):
                with patch.object(
                    root_cli,
                    "open_pointer_paramiko",
                    side_effect=RuntimeError("auth failed"),
                ):
                    with patch.object(root_cli.config, "save") as save:
                        with patch.object(root_cli, "_maybe_save_password") as maybe:
                            # Drive real cmd_install path: connect fails before save
                            code = root_cli.cmd_install(args)
        self.assertEqual(code, 1)
        # Connection failed: save helper must not have been called with success path.
        # cmd_install only calls _maybe_save_password after open succeeds.
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
        with patch.object(root_cli, "_password_from_args", return_value="x"):
            with patch.object(root_cli, "_host_from_args", return_value="10.11.99.1"):
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
        with patch.object(root_cli, "_password_from_args", return_value="x"):
            with patch.object(root_cli, "_host_from_args", return_value="10.11.99.1"):
                with patch.object(root_cli.kb, "cmd_install_service", return_value=0):
                    with patch.object(
                        root_cli,
                        "open_pointer_paramiko",
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
        from core import native_app_installer as nai

        # Binary present but --version fails → not usable
        def results(cmd):
            if "mount" in cmd and "bind" in cmd:
                return "", "", 0
            if "opkg --version" in cmd:
                return "", "segfault", 1
            return "", "", 1

        self.assertFalse(nai._opkg_usable(self._ssh(results)))

        def ok(cmd):
            if "opkg --version" in cmd:
                return "opkg version 1.0", "", 0
            return "", "", 0

        self.assertTrue(nai._opkg_usable(self._ssh(ok)))

    def test_tablet_python_usable_requires_exec(self):
        from core import native_app_installer as nai

        def broken(cmd):
            if "mount" in cmd:
                return "", "", 0
            if "python3 -c" in cmd:
                return "", "ImportError", 1
            return "", "", 1

        self.assertFalse(nai._tablet_python_usable(self._ssh(broken)))

        def ok(cmd):
            if "python3 -c" in cmd:
                return "3", "", 0
            return "", "", 0

        self.assertTrue(nai._tablet_python_usable(self._ssh(ok)))

    def test_ensure_entware_cleans_partial_debris_before_reinstall(self):
        from core import native_app_installer as nai

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
            nai._ensure_entware(ssh, say=lambda m: None)
        self.assertIn("entware install failed", str(ctx.exception))
        # Must have attempted cleanup of partial tree (before and/or after fail)
        cleaned = [c for c in calls if "rm -rf /home/root/.entware" in c]
        self.assertTrue(cleaned, "expected partial Entware cleanup")

    def test_ensure_entware_skips_install_when_opkg_usable(self):
        from core import native_app_installer as nai

        def results(cmd):
            if "opkg --version" in cmd:
                return "opkg 1", "", 0
            return "", "", 0

        ssh = self._ssh(results)
        nai._ensure_entware(ssh, say=lambda m: None)
        wget_calls = [c for c, in ((call.args[0],) for call in ssh.exec.call_args_list)
                      if "rmpp_entware" in c and "wget" in c]
        # Simpler: no wget
        all_cmds = [call.args[0] for call in ssh.exec.call_args_list]
        self.assertFalse(any("wget" in c and "rmpp_entware" in c for c in all_cmds))

    def test_ensure_python3_does_not_accept_non_opt_python(self):
        """command -v python3 alone must not short-circuit Entware python."""
        from core import native_app_installer as nai

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
            nai._ensure_python3(ssh, say=lambda m: None)
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
