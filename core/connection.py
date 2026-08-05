"""SSH connection policy for PaperHid.

* Keyboard ops use ``core.ssh_client.SSHClient``.
* Pointer ops use raw Paramiko (``paperpointer.sshutil.connect``).
* Combined commands open sequential connections (keyboard, then pointer).
"""
from __future__ import annotations

from typing import Optional, Tuple

from core.credentials import require_password, resolve_host
from core.ssh_client import SSHClient


def open_keyboard_ssh(
    *,
    host: Optional[str] = None,
    ip: Optional[str] = None,
    password: Optional[str] = None,
    timeout: int = 15,
) -> Tuple[SSHClient, str, str]:
    """Open keyboard-stack SSHClient. Returns (client, host, password)."""
    h = resolve_host(cli_host=host, cli_ip=ip)
    pw = require_password(password)
    ssh = SSHClient()
    ssh.connect(h, pw, timeout=timeout)
    return ssh, h, pw


def open_pointer_paramiko(
    *,
    host: Optional[str] = None,
    ip: Optional[str] = None,
    password: Optional[str] = None,
):
    """Open raw Paramiko client for paperpointer handlers."""
    from paperpointer.sshutil import connect

    h = resolve_host(cli_host=host, cli_ip=ip)
    pw = require_password(password)
    return connect(h, pw), h, pw
