"""Unified SSH credential and host resolution for PaperHid.

Precedence (both root CLI and paperpointer compatibility entry points):

  1. CLI ``--password``
  2. ``PAPERWRITER_PASSWORD``
  3. Compatibility env: ``PAPERPOINTER_PASSWORD``, then ``MOVEWRITER_PASSWORD``
  4. Host-side ``~/.paperwriter/config.json`` (base64 field)

Never prints the password.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_HOST = "10.11.99.1"

# Env keys after CLI --password (primary first, then compatibility aliases).
ENV_PASSWORD_KEYS = (
    "PAPERWRITER_PASSWORD",
    "PAPERPOINTER_PASSWORD",
    "MOVEWRITER_PASSWORD",
)

ENV_HOST_KEYS = (
    "PAPERWRITER_IP",
    "PAPERPOINTER_HOST",
    "MOVEWRITER_IP",
)


def resolve_password(
    cli_password: Optional[str] = None,
    *,
    use_config: bool = True,
) -> str:
    """Return the SSH password using the monorepo precedence contract.

    Empty string means no password was found (callers decide whether to error).
    """
    if cli_password:
        return cli_password
    for key in ENV_PASSWORD_KEYS:
        val = os.environ.get(key) or ""
        if val:
            return val
    if use_config:
        try:
            from core import config

            stored = config.get_password(config.load())
            if stored:
                return stored
        except Exception:
            pass
    return ""


def resolve_host(
    cli_host: Optional[str] = None,
    cli_ip: Optional[str] = None,
    *,
    use_config: bool = True,
) -> str:
    """Resolve tablet host/IP. ``--host`` and ``--ip`` are aliases; non-empty wins in order."""
    for candidate in (cli_host, cli_ip):
        if candidate:
            return candidate
    for key in ENV_HOST_KEYS:
        val = os.environ.get(key) or ""
        if val:
            return val
    if use_config:
        try:
            from core import config

            ip = (config.load().get("ip") or "").strip()
            if ip:
                return ip
        except Exception:
            pass
    return DEFAULT_HOST


def require_password(cli_password: Optional[str] = None, *, use_config: bool = True) -> str:
    """Like resolve_password but raises ValueError when missing."""
    pw = resolve_password(cli_password, use_config=use_config)
    if not pw:
        raise ValueError(
            "SSH password required. Pass --password, set PAPERWRITER_PASSWORD "
            "(or PAPERPOINTER_PASSWORD / MOVEWRITER_PASSWORD), or save config."
        )
    return pw
