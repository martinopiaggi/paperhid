"""Single SSH session policy for keyboard + pointer host commands.

Credentials are persisted only after a successful authentication, and only when
the user passed ``--save-password``.
"""
from __future__ import annotations

from contextlib import contextmanager

from core import config
from core.connection import open_keyboard_ssh, open_pointer_paramiko
from core.credentials import require_password, resolve_host


def host_from_args(args) -> str:
    return resolve_host(
        cli_host=getattr(args, "host", None),
        cli_ip=getattr(args, "ip", None),
    )


def password_from_args(args) -> str:
    return require_password(getattr(args, "password", None))


def maybe_save_password(args, host: str, password: str) -> None:
    """Persist credentials only when explicitly requested after successful auth."""
    if not getattr(args, "save_password", False):
        return
    cfg = config.load()
    cfg["ip"] = host
    config.set_password(cfg, password)
    config.save(cfg)


@contextmanager
def pointer_session(args, *, persist_password: bool = True):
    """Open pointer Paramiko; optionally save credentials after auth succeeds."""
    host = host_from_args(args)
    password = password_from_args(args)
    client = None
    try:
        client, host, password = open_pointer_paramiko(host=host, password=password)
        if persist_password:
            maybe_save_password(args, host, password)
        yield client
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


@contextmanager
def keyboard_session(args, *, persist_password: bool = True):
    """Open keyboard SSH; optionally save credentials after auth succeeds."""
    host = host_from_args(args)
    password = password_from_args(args)
    ssh = None
    try:
        ssh, host, password = open_keyboard_ssh(
            host=host,
            password=password,
            timeout=getattr(args, "timeout", 15),
        )
        if persist_password:
            maybe_save_password(args, host, password)
        yield ssh, host, password
    finally:
        if ssh is not None:
            try:
                ssh.disconnect()
            except Exception:
                pass
