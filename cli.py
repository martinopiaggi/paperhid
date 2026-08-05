"""PaperHid CLI — Bluetooth keyboard + mouse for reMarkable Paper Pro.

    python cli.py install --keyboard | --pointer | --all
    python cli.py status | scan | pair | …

Password: --password → PAPERHID_PASSWORD → legacy env aliases → saved config.
"""
from __future__ import annotations

import argparse
import sys

from core import config, service_installer, bluetooth, device as device_mod
from core.connection import open_keyboard_ssh, open_pointer_paramiko
from core.credentials import require_password, resolve_host
from core.status_merge import (
    ComponentStatus,
    classify_keyboard_status,
    classify_pointer_status,
    format_status_report,
    merge_exit_codes,
)
from core.ssh_client import SSHClient

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
    """Persist credentials only when explicitly requested.

    Callers must invoke this **after** a successful SSH authentication, never
    before connect, so a typo cannot overwrite a good saved password.
    """
    if not getattr(args, "save_password", False):
        return
    cfg = config.load()
    cfg["ip"] = host
    config.set_password(cfg, password)
    config.save(cfg)


def _kb_args_view(args, *, save_password: bool | None = None):
    """Namespace compatible with keyboard_cli handlers.

    Default: do **not** let nested keyboard_cli save before our own post-auth
    save policy (``save_password=False``). Pass ``True`` only after a successful
    connection when the user requested ``--save-password``.
    """
    host = _host_from_args(args)
    if save_password is None:
        save_password = False
    ns = argparse.Namespace(
        ip=host,
        password=getattr(args, "password", None),
        save_password=save_password,
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
    saved = False

    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== install keyboard (BT service) ===")
        try:
            # keyboard_cli connects first; save only after that handler succeeds
            kb_code = kb.cmd_install_service(
                _kb_args_view(args, save_password=False)
            )
            if kb_code == 0 and not saved:
                _maybe_save_password(args, host, password)
                saved = True
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
            "Note: needs tablet Python. Missing Entware is installed automatically "
            "(tablet Wi-Fi required for first bootstrap)."
        )
        c = None
        try:
            c, _, _ = open_pointer_paramiko(host=host, password=password)
            if not saved:
                _maybe_save_password(args, host, password)
                saved = True
            from paperpointer.cli import cmd_install as ptr_install

            bootstrap = not getattr(args, "no_bootstrap", False)
            ptr_code = ptr_install(c, bootstrap=bootstrap)
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


def cmd_bootstrap_python(args) -> int:
    """Install Entware + Python 3 on a vanilla tablet (pointer prerequisite)."""
    host = _host_from_args(args)
    password = _password_from_args(args)
    c = None
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        _maybe_save_password(args, host, password)
        from paperpointer.cli import bootstrap_tablet_python, preflight_tablet_python

        print(
            "Bootstrapping tablet Python (Entware + python3). "
            "Tablet needs Wi-Fi; this can take several minutes.",
            flush=True,
        )
        bootstrap_tablet_python(c)
        code = preflight_tablet_python(c)
        if code != 0:
            print("bootstrap finished but python still missing", file=sys.stderr)
            return code
        print("OK: tablet Python ready. Next: python cli.py install --pointer")
        return 0
    except Exception as e:
        print(f"bootstrap-python error: {e}", file=sys.stderr)
        return 1
    finally:
        if c is not None:
            c.close()


def cmd_uninstall(args) -> int:
    mode = _install_mode(args)
    host = _host_from_args(args)
    password = _password_from_args(args)
    saved = False
    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== uninstall keyboard ===")
        try:
            kb_code = kb.cmd_uninstall_service(
                _kb_args_view(args, save_password=False)
            )
            if kb_code == 0 and not saved:
                _maybe_save_password(args, host, password)
                saved = True
        except Exception as e:
            print(f"keyboard uninstall error: {e}", file=sys.stderr)
            kb_code = 1
    ptr_code = 0
    if mode in ("pointer", "all"):
        print("=== uninstall pointer ===")
        c = None
        try:
            c, _, _ = open_pointer_paramiko(host=host, password=password)
            if not saved:
                _maybe_save_password(args, host, password)
                saved = True
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


def parse_pointer_probe_output(out: str) -> dict:
    """Parse labeled pointer probe stdout from the tablet.

    Remote script emits dedicated markers::

        HOME_YES|HOME_NO
        UNIT_ETC_YES|UNIT_ETC_NO
        UNIT_USR_YES|UNIT_USR_NO
        ACTIVE:<state>     # from systemctl is-active only
        FAILED:<state>     # from systemctl is-failed only

    ``UNIT_YES`` is also accepted (either path) for older fixtures.
    ``unit_file_present`` is true if either ``/etc`` or ``/usr`` unit exists.
    """
    home_present = False
    unit_etc = False
    unit_usr = False
    unit_legacy = False
    is_active = None  # type: bool | None
    is_failed = False
    for raw in (out or "").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln == "HOME_YES":
            home_present = True
        elif ln == "HOME_NO":
            home_present = False
        elif ln == "UNIT_ETC_YES":
            unit_etc = True
        elif ln == "UNIT_ETC_NO":
            unit_etc = False
        elif ln == "UNIT_USR_YES":
            unit_usr = True
        elif ln == "UNIT_USR_NO":
            unit_usr = False
        elif ln == "UNIT_YES":
            unit_legacy = True
        elif ln == "UNIT_NO":
            unit_legacy = False
        elif ln.startswith("ACTIVE:"):
            state = ln.split(":", 1)[1].strip().lower()
            if state == "active":
                is_active = True
            else:
                # inactive, failed, dead, not-found, …
                is_active = False
        elif ln.startswith("FAILED:"):
            state = ln.split(":", 1)[1].strip().lower()
            # is-failed prints "failed" or "active" (not failed) — never use as is_active
            is_failed = state == "failed"
    unit_file_present = unit_etc or unit_usr or unit_legacy
    return {
        "home_present": home_present,
        "unit_file_present": unit_file_present,
        "unit_etc_present": unit_etc,
        "unit_usr_present": unit_usr,
        "is_active": is_active,
        "is_failed": is_failed,
        "raw_lines": [ln.strip() for ln in (out or "").splitlines() if ln.strip()],
    }


def cmd_status(args) -> int:
    """Merged status: labeled sections; optional/residual components exit 0."""
    host = _host_from_args(args)
    password = _password_from_args(args)

    keyboard_lines: list[str] = []
    keyboard = ComponentStatus(
        state="unknown",
        detail="keyboard probe not run",
        exit_code=0,
        label="keyboard",
    )
    ssh = None
    try:
        ssh, _, _ = open_keyboard_ssh(
            host=host,
            password=password,
            timeout=getattr(args, "timeout", 15),
        )
        _maybe_save_password(args, host, password)
        info = device_mod.detect(ssh)
        state = bluetooth.verify_device_state(ssh, config.load())
        present = bool(state.get("service_present") or state.get("service_active"))
        keyboard = classify_keyboard_status(
            service_present=present,
            service_active=bool(state.get("service_active")) if present else None,
            service_failed=bool(state.get("service_failed")),
        )
        keyboard_lines.append(f"device: {info.get('label')} ({info.get('model')})")
        keyboard_lines.append(f"img_version: {info.get('img_version')}")
        for k, v in state.items():
            keyboard_lines.append(f"{k}: {v}")
    except Exception as e:
        keyboard_lines.append(f"error: {e}")
        keyboard = ComponentStatus(
            state="unknown",
            detail=f"keyboard probe error: {e}",
            exit_code=1,
            label="keyboard",
        )
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
        _maybe_save_password(args, host, password)
        from paperpointer.sshutil import (
            REMOTE_HOME,
            UNIT_ETC,
            UNIT_NAME,
            UNIT_USR,
            run,
        )

        # Probe both unit install locations; labeled ACTIVE/FAILED only.
        out, _, _ = run(
            c,
            f"test -d {REMOTE_HOME} && echo HOME_YES || echo HOME_NO; "
            f"test -f {UNIT_ETC} && echo UNIT_ETC_YES || echo UNIT_ETC_NO; "
            f"test -f {UNIT_USR} && echo UNIT_USR_YES || echo UNIT_USR_NO; "
            f'printf "ACTIVE:%s\\n" "$(systemctl is-active {UNIT_NAME} 2>/dev/null || echo inactive)"; '
            f'printf "FAILED:%s\\n" "$(systemctl is-failed {UNIT_NAME} 2>/dev/null || echo unknown)"',
            timeout=20,
        )
        facts = parse_pointer_probe_output(out)
        pointer = classify_pointer_status(
            home_present=facts["home_present"],
            unit_file_present=facts["unit_file_present"],
            is_active=facts["is_active"],
            is_failed=facts["is_failed"],
        )
        if pointer.state not in ("not_installed",):
            pointer.detail = (
                pointer.detail + f" | raw={';'.join(facts['raw_lines'])[:200]}"
            )
    except Exception as e:
        pointer = ComponentStatus(
            state="unknown",
            detail=f"pointer probe error: {e}",
            exit_code=1,
            label="pointer",
        )
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    sys.stdout.write(
        format_status_report(keyboard_lines, pointer, keyboard=keyboard)
    )
    return merge_exit_codes(keyboard, pointer)


def cmd_detect(args) -> int:
    """Merged detect: keyboard device detect + pointer detect dump."""
    host = _host_from_args(args)
    password = _password_from_args(args)
    print("=== keyboard / device ===")
    kb_code = 0
    try:
        # Nested CLI connects; save after success only
        kb_code = kb.cmd_detect(_kb_args_view(args, save_password=False)) or 0
        if kb_code == 0:
            _maybe_save_password(args, host, password)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        kb_code = 1
    print("=== pointer / inputs ===")
    c = None
    ptr_code = 0
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        _maybe_save_password(args, host, password)
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
    c = None
    try:
        c, _, _ = open_pointer_paramiko(host=host, password=password)
        _maybe_save_password(args, host, password)
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
    g.add_argument(
        "--pointer",
        action="store_true",
        help="mouse daemon (auto-installs tablet Python if missing)",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="keyboard then pointer (recommended first install)",
    )
    inst.add_argument("--wait", type=int, default=12, help="BT controller wait (keyboard)")
    inst.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Do not auto-install Entware/Python if missing (pointer)",
    )

    un = sub.add_parser("uninstall", help="Uninstall keyboard, pointer, or both")
    ug = un.add_mutually_exclusive_group(required=True)
    ug.add_argument("--keyboard", action="store_true")
    ug.add_argument("--pointer", action="store_true")
    ug.add_argument("--all", action="store_true")

    isvc = sub.add_parser("install-service", help="Alias: install --keyboard")
    isvc.add_argument(
        "--wait",
        type=int,
        default=12,
        help="BT controller wait seconds",
    )
    sub.add_parser("uninstall-service", help="Alias: uninstall --keyboard")
    sub.add_parser(
        "bootstrap-python",
        help="Install Entware + Python 3 on tablet (mouse prerequisite)",
    )
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
            argparse.Namespace(
                **{
                    **vars(a),
                    "keyboard": True,
                    "pointer": False,
                    "all": False,
                    "no_bootstrap": True,
                }
            )
        ),
        "uninstall-service": lambda a: cmd_uninstall(
            argparse.Namespace(**{**vars(a), "keyboard": True, "pointer": False, "all": False})
        ),
        "bootstrap-python": cmd_bootstrap_python,
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
