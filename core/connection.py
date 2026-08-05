"""SSH connection policy for PaperHid monorepo.

v1 policy (documented and intentional):

* **Keyboard / PaperWriter operations** use ``core.ssh_client.SSHClient``
  (thread-safe wrapper with ``exec`` / upload helpers).
* **Pointer / PaperPointer operations** use a **raw Paramiko** client from
  ``paperpointer.sshutil.connect`` (handlers expect Paramiko + ``run``/``put_tree``).
* **Combined commands** (``install --all``, merged ``status``/``detect``) open
  **sequential single-purpose connections**: keyboard work first (SSHClient),
  then pointer work (raw Paramiko). Connections are closed after each phase.
  They do **not** share one live dual-client session in v1.

This avoids ambiguous dual-client ownership while keeping both stacks intact.
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
    """Open PaperWriter-style SSHClient. Returns (client, host, password)."""
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
