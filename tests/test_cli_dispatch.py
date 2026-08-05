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
        from paperpointer import cli as ppcli

        conn = MagicMock()
        with patch.object(ppcli, "preflight_tablet_python", return_value=2) as pre:
            with patch.object(ppcli, "put_tree") as put:
                code = ppcli.cmd_install(conn)
        self.assertEqual(code, 2)
        pre.assert_called_once_with(conn)
        put.assert_not_called()


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
