"""Compatibility re-export of keyboard host commands.

Prefer ``python cli.py …``. Implementation: ``host_cli.keyboard``.
"""
from __future__ import annotations

from host_cli.errors import CliError
from host_cli.keyboard import (  # noqa: F401
    cmd_detect,
    cmd_diagnose,
    cmd_install_service,
    cmd_pair,
    cmd_refuse_layout,
    cmd_save_mac,
    cmd_scan,
    cmd_ssh,
    cmd_status,
    cmd_uninstall_service,
    cmd_unpair,
)

__all__ = [
    "CliError",
    "cmd_detect",
    "cmd_diagnose",
    "cmd_install_service",
    "cmd_pair",
    "cmd_refuse_layout",
    "cmd_save_mac",
    "cmd_scan",
    "cmd_ssh",
    "cmd_status",
    "cmd_uninstall_service",
    "cmd_unpair",
]
