"""PaperHid unified CLI — keyboard + pointer for reMarkable Paper Pro.

Install modes (mutually exclusive, required for ``install`` / ``uninstall``)::

    python cli.py install --keyboard
    python cli.py install --pointer
    python cli.py install --all

Partial failure for ``--all``: keyboard runs first; on keyboard failure pointer
is skipped; on pointer failure after successful keyboard, nonzero exit with
keyboard left installed (partial).

SSH policy: sequential single-purpose connections (see ``core.connection``).
Password: ``--password`` → PAPERWRITER_PASSWORD → PAPERPOINTER_PASSWORD →
MOVEWRITER_PASSWORD → ~/.paperwriter/config.json.
"""
from __future__ import annotations

import argparse
import sys

from core import config, service_installer, bluetooth, device as device_mod
from core.connection import open_keyboard_ssh, open_pointer_paramiko
from core.credentials import require_password, resolve_host
from core.status_merge import (
    classify_pointer_status,
    format_status_report,
    merge_exit_codes,
)
from core.ssh_client import SSHClient

# Keyboard handlers live in keyboard_cli (PaperWriter pin surface).
import keyboard_cli as kb


class CliError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _host_from_args(args) -> str:
    return resolve_host(
        cli_host=getattr(args, "host", None),
        cli_ip=getattr(args, "ip", None),
    )


def _password_from_args(args) -> str:
    return require_password(getattr(args, "password", None))


def _maybe_save_password(args, host: str, password: str) -> None:
    if not getattr(args, "save_password", False):
        return
    cfg = config.load()
    cfg["ip"] = host
    config.set_password(cfg, password)
    config.save(cfg)


def _kb_args_view(args):
    """Namespace compatible with keyboard_cli handlers."""
    host = _host_from_args(args)
    ns = argparse.Namespace(
        ip=host,
        password=getattr(args, "password", None),
        save_password=getattr(args, "save_password", False),
        timeout=getattr(args, "timeout", 15),
        wait=getattr(args, "wait", 12),
        scan_timeout=getattr(args, "scan_timeout", 22),
        mac=getattr(args, "mac", None),
        name=getattr(args, "name", "") or "",
        remote_cmd=getattr(args, "remote_cmd", "uname -a"),
        probe_scan=getattr(args, "probe_scan", False),
    )
    return ns


# --- Combined commands -------------------------------------------------------


def cmd_install(args) -> int:
    mode = _install_mode(args)
    host = _host_from_args(args)
    password = _password_from_args(args)
    _maybe_save_password(args, host, password)

    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== install keyboard (BT service) ===")
        try:
            kb_code = kb.cmd_install_service(_kb_args_view(args))
        except Exception as e:
            print(f"keyboard install error: {e}", file=sys.stderr)
            kb_code = 1
        if kb_code != 0 and mode == "all":
            print(
                "install --all: keyboard failed; skipping pointer (partial not applied).",
                file=sys.stderr,
            )
            return kb_code

    ptr_code = 0
    if mode in ("pointer", "all"):
        print("=== install pointer (paperpointerd) ===")
        print(
            "Note: requires tablet /opt/bin/python3 (Entware). Preflight runs before upload."
        )
        c = None
        try:
            c, _, _ = open_pointer_paramiko(
                host=host, password=password
            )
            from paperpointer.cli import cmd_install as ptr_install

            ptr_code = ptr_install(c)
        except Exception as e:
            print(f"pointer install error: {e}", file=sys.stderr)
            ptr_code = 1
        finally:
            if c is not None:
                c.close()
        if ptr_code != 0 and mode == "all" and kb_code == 0:
            print(
                "install --all: pointer failed after keyboard succeeded "
                "(partial install: keyboard remains).",
                file=sys.stderr,
            )

    if mode == "keyboard":
        return kb_code
    if mode == "pointer":
        return ptr_code
    return kb_code if kb_code != 0 else ptr_code


def cmd_uninstall(args) -> int:
    mode = _install_mode(args)
    host = _host_from_args(args)
    password = _password_from_args(args)
    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== uninstall keyboard ===")
        try:
            kb_code = kb.cmd_uninstall_service(_kb_args_view(args))
        except Exception as e:
            print(f"keyboard uninstall error: {e}", file=sys.stderr)
            kb_code = 1
    ptr_code = 0
    if mode in ("pointer", "all"):
        print("=== uninstall pointer ===")
        c = None
        try:
            c, _, _ = open_pointer_paramiko(host=host, password=password)
            from paperpointer.cli import cmd_uninstall as ptr_uninstall

            ptr_code = ptr_uninstall(c)
        except Exception as e:
            print(f"pointer uninstall error: {e}", file=sys.stderr)
            ptr_code = 1
        finally:
            if c is not None:
                c.close()
    if mode == "keyboard":
        return kb_code
    if mode == "pointer":
        return ptr_code
    return kb_code if kb_code != 0 else ptr_code


def _install_mode(args) -> str:
    if getattr(args, "keyboard", False):
        return "keyboard"
    if getattr(args, "pointer", False):
        return "pointer"
    if getattr(args, "all", False):
        return "all"
    raise CliError("install/uninstall requires one of --keyboard, --pointer, --all")


def cmd_status(args) -> int:
    """Merged status: labeled sections; pointer absent is success."""
    host = _host_from_args(args)
    password = _password_from_args(args)
    _maybe_save_password(args, host, password)

    keyboard_lines: list[str] = []
    kb_code = 0
    ssh = None
    try:
        ssh, _, _ = open_keyboard_ssh(
            host=host,
            password=password,
            timeout=getattr(args, "timeout", 15),
        )
        info = device_mod.detect(ssh)
        state = bluetooth.verify_device_state(ssh, config.load())
        keyboard_lines.append(f"device: {info.get('label')} ({info.get('model')})")
        keyboard_lines.append(f"img_version: {info.get('img_version')}")
        for k, v in state.items():
            keyboard_lines.append(f"{k}: {v}")
    except Exception as e:
        keyboard_lines.append(f"error: {e}")
        kb_code = 1
    finally:
        if ssh is not None:
            ssh.disconnect()

    pointer = classify_pointer_status(
        home_present=False,
        unit_file_present=False,
        is_active=None,
    )
    c = None
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        from paperpointer.sshutil import REMOTE_HOME, UNIT_ETC, UNIT_NAME, run

        out, _, _ = run(
            c,
            f"test -d {REMOTE_HOME} && echo HOME_YES || echo HOME_NO; "
            f"test -f {UNIT_ETC} && echo UNIT_YES || echo UNIT_NO; "
            f"systemctl is-active {UNIT_NAME} 2>/dev/null || echo inactive; "
            f"systemctl is-failed {UNIT_NAME} 2>/dev/null || true",
            timeout=20,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        home_present = any(ln == "HOME_YES" for ln in lines)
        unit_file_present = any(ln == "UNIT_YES" for ln in lines)
        is_failed = any(ln == "failed" for ln in lines)
        is_active = None
        for ln in lines:
            if ln == "active":
                is_active = True
            elif ln in ("inactive", "failed"):
                if is_active is not True:
                    is_active = False
        pointer = classify_pointer_status(
            home_present=home_present,
            unit_file_present=unit_file_present,
            is_active=is_active,
            is_failed=is_failed,
        )
        # Extra detail line for operators
        if not pointer.is_absent:
            pointer.detail = pointer.detail + f" | raw={';'.join(lines)[:200]}"
    except Exception as e:
        # Connection failure is real; probe parse issues for absent tablet path
        # If we never got pointer facts, keep not_installed only when error is "not there"
        # Connection errors are failures.
        pointer = classify_pointer_status(
            home_present=False,
            unit_file_present=False,
            is_active=None,
        )
        # Treat SSH failure after keyboard as failure of combined status only if kb also failed
        # Pointer section reports probe error as detail but if we cannot connect at all
        # and keyboard already failed, merge handles it. If keyboard ok but pointer SSH fails,
        # that is a real failure.
        from core.status_merge import PointerStatus

        pointer = PointerStatus(
            state="unknown",
            detail=f"pointer probe error: {e}",
            exit_code=1,
        )
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    sys.stdout.write(format_status_report(keyboard_lines, pointer))
    return merge_exit_codes(kb_code, pointer)


def cmd_detect(args) -> int:
    """Merged detect: keyboard device detect + pointer detect dump."""
    host = _host_from_args(args)
    password = _password_from_args(args)
    _maybe_save_password(args, host, password)
    print("=== keyboard / device ===")
    kb_code = 0
    try:
        kb_code = kb.cmd_detect(_kb_args_view(args)) or 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        kb_code = 1
    print("=== pointer / inputs ===")
    c = None
    ptr_code = 0
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        from paperpointer.cli import cmd_detect as ptr_detect

        ptr_code = ptr_detect(c)
    except Exception as e:
        print(f"pointer detect error: {e}", file=sys.stderr)
        ptr_code = 1
    finally:
        if c is not None:
            c.close()
    return kb_code if kb_code != 0 else ptr_code


def cmd_pointer(args) -> int:
    """Dispatch ``pointer <cmd>`` subcommands."""
    host = _host_from_args(args)
    password = _password_from_args(args)
    _maybe_save_password(args, host, password)
    c = None
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        from paperpointer.cli import dispatch_pointer

        # Normalize cmd attr for dispatch_pointer
        if not getattr(args, "cmd", None):
            args.cmd = getattr(args, "pointer_cmd", None)
        return dispatch_pointer(args, c)
    finally:
        if c is not None:
            c.close()


# --- Parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperhid",
        description="PaperHid: Bluetooth keyboard + mouse/pointer for reMarkable Paper Pro",
    )
    p.add_argument("--ip", default=None, help="Tablet IP (default 10.11.99.1)")
    p.add_argument("--host", default=None, help="Alias for --ip")
    p.add_argument("--password", default=None, help="SSH password")
    p.add_argument(
        "--save-password",
        action="store_true",
        help="Save password to ~/.paperwriter/config.json",
    )
    p.add_argument("--timeout", type=int, default=15)
    sub = p.add_subparsers(dest="command", required=True)

    # Combined
    sub.add_parser("detect", help="Merged device + pointer detect")
    sub.add_parser("status", help="Merged keyboard + pointer status")

    inst = sub.add_parser(
        "install",
        help="Install keyboard service, pointer daemon, or both",
    )
    g = inst.add_mutually_exclusive_group(required=True)
    g.add_argument("--keyboard", action="store_true", help="BT keyboard service only")
    g.add_argument("--pointer", action="store_true", help="paperpointerd only")
    g.add_argument("--all", action="store_true", help="keyboard then pointer")
    inst.add_argument("--wait", type=int, default=12, help="BT controller wait (keyboard)")

    un = sub.add_parser("uninstall", help="Uninstall keyboard, pointer, or both")
    ug = un.add_mutually_exclusive_group(required=True)
    ug.add_argument("--keyboard", action="store_true")
    ug.add_argument("--pointer", action="store_true")
    ug.add_argument("--all", action="store_true")

    # Keyboard (PaperWriter) surface
    sub.add_parser("install-service", help="Alias: install --keyboard")
    sub.add_parser("uninstall-service", help="Alias: uninstall --keyboard")
    sp = sub.add_parser("ssh")
    sp.add_argument("remote_cmd", nargs="?", default="uname -a")
    sp = sub.add_parser("scan")
    sp.add_argument("--scan-timeout", type=int, default=22)
    sp = sub.add_parser("pair")
    sp.add_argument("--mac", default=None)
    sp.add_argument("--name", default="")
    sp.add_argument("--scan-timeout", type=int, default=22)
    sp = sub.add_parser("save-mac")
    sp.add_argument("--mac", required=True)
    sp.add_argument("--name", default="")
    sp = sub.add_parser("unpair")
    sp.add_argument("--mac", default=None)
    sub.add_parser("refuse-layout")
    sub.add_parser("refuse-native")
    sp = sub.add_parser("diagnose")
    sp.add_argument("--probe-scan", action="store_true")

    # Pointer nested: paperhid pointer <cmd>
    from paperpointer.cli import register_pointer_commands, shared_flag_parser

    ptr = sub.add_parser("pointer", help="Pointer/mouse commands (same as python -m paperpointer)")
    # Nested pointer uses root --password/--host; also allow after subcommand via shared
    shared = shared_flag_parser(for_subparser=True)
    ptr_sub = ptr.add_subparsers(dest="pointer_cmd", required=True)
    register_pointer_commands(ptr_sub, shared)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Normalize host/ip onto args for helpers
    if getattr(args, "host", None) and not getattr(args, "ip", None):
        args.ip = args.host

    handlers = {
        "detect": cmd_detect,
        "status": cmd_status,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "install-service": lambda a: cmd_install(
            argparse.Namespace(**{**vars(a), "keyboard": True, "pointer": False, "all": False})
        ),
        "uninstall-service": lambda a: cmd_uninstall(
            argparse.Namespace(**{**vars(a), "keyboard": True, "pointer": False, "all": False})
        ),
        "ssh": lambda a: kb.cmd_ssh(_kb_args_view(a)),
        "scan": lambda a: kb.cmd_scan(_kb_args_view(a)),
        "pair": lambda a: kb.cmd_pair(_kb_args_view(a)),
        "save-mac": lambda a: kb.cmd_save_mac(_kb_args_view(a)),
        "unpair": lambda a: kb.cmd_unpair(_kb_args_view(a)),
        "refuse-layout": lambda a: kb.cmd_refuse_layout(_kb_args_view(a)),
        "refuse-native": lambda a: kb.cmd_refuse_native(_kb_args_view(a)),
        "diagnose": lambda a: kb.cmd_diagnose(_kb_args_view(a)),
        "pointer": cmd_pointer,
    }
    try:
        if args.command == "pointer":
            # Map nested dest
            args.cmd = args.pointer_cmd
            return cmd_pointer(args)
        return handlers[args.command](args) or 0
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main() or 0)
