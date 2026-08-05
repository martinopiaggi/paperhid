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
        os.environ["PAPERWRITER_PASSWORD"] = "from-writer"
        os.environ["PAPERPOINTER_PASSWORD"] = "from-pointer"
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        self.assertEqual(
            resolve_password("from-cli", use_config=False),
            "from-cli",
        )

    def test_paperwriter_env_before_compat(self):
        os.environ["PAPERPOINTER_PASSWORD"] = "from-pointer"
        os.environ["PAPERWRITER_PASSWORD"] = "from-writer"
        self.assertEqual(resolve_password(None, use_config=False), "from-writer")

    def test_paperpointer_before_movewriter(self):
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        os.environ["PAPERPOINTER_PASSWORD"] = "from-pointer"
        self.assertEqual(resolve_password(None, use_config=False), "from-pointer")

    def test_movewriter_last_env(self):
        os.environ["MOVEWRITER_PASSWORD"] = "from-move"
        self.assertEqual(resolve_password(None, use_config=False), "from-move")

    def test_empty_when_nothing(self):
        self.assertEqual(resolve_password(None, use_config=False), "")

    def test_require_password_raises(self):
        with self.assertRaises(ValueError):
            require_password(None, use_config=False)

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
            for k in ("PAPERWRITER_IP", "PAPERPOINTER_HOST", "MOVEWRITER_IP"):
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

        os.environ["PAPERWRITER_PASSWORD"] = "env-should-lose"
        self.assertEqual(password_from_env("cli-wins"), "cli-wins")

    def test_writer_env_via_sshutil(self):
        from paperpointer.sshutil import password_from_env

        os.environ["PAPERPOINTER_PASSWORD"] = "ptr"
        os.environ["PAPERWRITER_PASSWORD"] = "wrt"
        self.assertEqual(password_from_env(None), "wrt")


if __name__ == "__main__":
    unittest.main()
