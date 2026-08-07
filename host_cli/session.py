"""Single SSH session policy for keyboard + pointer host commands.

Password comes from ``--password`` or ``PAPERHID_PASSWORD`` (see
``core.credentials``). Nothing is written back to disk for credentials.
"""
from __future__ import annotations

from contextlib import contextmanager

from core.connection import open_keyboard_ssh, open_pointer_paramiko
from core.credentials import require_password, resolve_host


def host_from_args(args) -> str:
    return resolve_host(
        cli_host=getattr(args, "host", None),
        cli_ip=getattr(args, "ip", None),
    )


def password_from_args(args) -> str:
    return require_password(getattr(args, "password", None))


@contextmanager
def pointer_session(args):
    """Open pointer Paramiko for the duration of the block."""
    host = host_from_args(args)
    password = password_from_args(args)
    client = None
    try:
        client, host, password = open_pointer_paramiko(host=host, password=password)
        yield client
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


@contextmanager
def keyboard_session(args):
    """Open keyboard SSH for the duration of the block."""
    host = host_from_args(args)
    password = password_from_args(args)
    ssh = None
    try:
        ssh, host, password = open_keyboard_ssh(
            host=host,
            password=password,
            timeout=getattr(args, "timeout", 15),
        )
        yield ssh, host, password
    finally:
        if ssh is not None:
            try:
                ssh.disconnect()
            except Exception:
                pass
