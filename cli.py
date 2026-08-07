"""PaperHid CLI — Bluetooth keyboard + mouse for reMarkable Paper Pro.

    python cli.py install --keyboard | --pointer | --all
    python cli.py status | scan | pair | …

Password: --password → PAPERHID_PASSWORD → legacy env aliases
(optional legacy: saved host config if present).

Implementation lives in the ``host_cli`` package (single CLI module).
"""
from __future__ import annotations

# Re-export the unified CLI surface so ``import cli as root_cli`` keeps working
# for tests and scripts.
from host_cli.app import *  # noqa: F403
from host_cli.app import (  # noqa: F401 — explicit names used by tests/patches
    CliError,
    build_parser,
    cmd_bootstrap_python,
    cmd_detect,
    cmd_install,
    cmd_pointer,
    cmd_repair_ui,
    cmd_set_layout,
    cmd_settings_ui,
    cmd_status,
    cmd_uninstall,
    main,
    open_keyboard_ssh,
    open_pointer_paramiko,
    parse_pointer_probe_output,
    shared_root_flags,
)
from host_cli import keyboard as kb  # noqa: F401
from host_cli.session import (  # noqa: F401
    host_from_args as _host_from_args,
    password_from_args as _password_from_args,
)
from core import config  # noqa: F401 — tests patch root_cli.config

if __name__ == "__main__":
    raise SystemExit(main() or 0)
