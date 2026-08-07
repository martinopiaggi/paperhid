"""Password/host precedence — drives shipped core.credentials resolvers."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.credentials import (
    ENV_PASSWORD_KEYS,
    resolve_host,
    resolve_password,
    require_password,
)


class TestPasswordPrecedence(unittest.TestCase):
    def setUp(self):
        self._env_backup = {k: os.environ.get(k) for k in ENV_PASSWORD_KEYS}
        for k in ENV_PASSWORD_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_cli_password_wins_over_all_env(self):
        os.environ["PAPERHID_PASSWORD"] = "from-hid"
        os.environ["PAPERWRITER_PASSWORD"] = "from-writer"
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        self.assertEqual(
            resolve_password("from-cli", use_config=False),
            "from-cli",
        )

    def test_paperhid_env_before_legacy(self):
        os.environ["PAPERWRITER_PASSWORD"] = "from-writer"
        os.environ["PAPERHID_PASSWORD"] = "from-hid"
        self.assertEqual(resolve_password(None, use_config=False), "from-hid")

    def test_legacy_writer_before_movewriter(self):
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        os.environ["PAPERWRITER_PASSWORD"] = "from-writer"
        self.assertEqual(resolve_password(None, use_config=False), "from-writer")

    def test_movewriter_last_env(self):
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        self.assertEqual(resolve_password(None, use_config=False), "from-move")

    def test_empty_when_nothing(self):
        self.assertEqual(resolve_password(None, use_config=False), "")

    def test_require_password_raises(self):
        with self.assertRaises(ValueError) as ctx:
            require_password(None, use_config=False)
        self.assertIn("PAPERHID_PASSWORD", str(ctx.exception))

    def test_config_used_when_env_empty(self):
        with patch("core.config.load", return_value={"password_b64": ""}):
            with patch("core.config.get_password", return_value="from-config"):
                self.assertEqual(resolve_password(None, use_config=True), "from-config")

    def test_cli_beats_config(self):
        with patch("core.config.get_password", return_value="from-config"):
            self.assertEqual(
                resolve_password("from-cli", use_config=True),
                "from-cli",
            )


class TestHostResolve(unittest.TestCase):
    def test_default_host(self):
        with patch.dict(os.environ, {}, clear=False):
            for k in (
                "PAPERHID_IP",
                "PAPERWRITER_IP",
                "PAPERPOINTER_HOST",
                "MOVEWRITER_IP",
            ):
                os.environ.pop(k, None)
            self.assertEqual(resolve_host(use_config=False), "10.11.99.1")

    def test_cli_host_wins(self):
        self.assertEqual(
            resolve_host(cli_host="1.2.3.4", use_config=False),
            "1.2.3.4",
        )


class TestPaperpointerPasswordCompat(unittest.TestCase):
    """Compatibility entry uses paperpointer.sshutil.password_from_env → credentials."""

    def setUp(self):
        self._env_backup = {k: os.environ.get(k) for k in ENV_PASSWORD_KEYS}
        for k in ENV_PASSWORD_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_cli_password_first_via_sshutil(self):
        from paperpointer.sshutil import password_from_env

        os.environ["PAPERHID_PASSWORD"] = "env-should-lose"
        self.assertEqual(password_from_env("cli-wins"), "cli-wins")

    def test_paperhid_env_via_sshutil(self):
        from paperpointer.sshutil import password_from_env

        os.environ["PAPERWRITER_PASSWORD"] = "wrt"
        os.environ["PAPERHID_PASSWORD"] = "hid"
        self.assertEqual(password_from_env(None), "hid")


if __name__ == "__main__":
    unittest.main()
