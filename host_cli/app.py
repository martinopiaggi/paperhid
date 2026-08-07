"""Unified PaperHid host CLI (keyboard + pointer + combined commands).

Public entry: ``python cli.py …`` or ``python -m host_cli …``.
"""
from __future__ import annotations

import argparse
import sys

from core import config, bluetooth, device as device_mod
from core.connection import open_keyboard_ssh, open_pointer_paramiko
from core.status_merge import (
    ComponentStatus,
    classify_keyboard_status,
    classify_pointer_status,
    format_status_report,
    merge_exit_codes,
)
from host_cli import keyboard as kb
from host_cli.errors import CliError
from host_cli.session import (
    host_from_args,
    keyboard_session,
    maybe_save_password,
    password_from_args,
    pointer_session,
)

# Re-export names tests/patches historically found on the root cli module.
_host_from_args = host_from_args
_password_from_args = password_from_args
_maybe_save_password = maybe_save_password
_pointer_session = pointer_session
_keyboard_session = keyboard_session


def _kb_args_view(args):
    """Namespace for keyboard handlers.

    The unified CLI owns credential persistence, so delegated handlers always
    connect with ``save_password=False``.
    """
    host = host_from_args(args)
    return argparse.Namespace(
        ip=host,
        host=host,
        password=getattr(args, "password", None),
        save_password=False,
        timeout=getattr(args, "timeout", 15),
        wait=getattr(args, "wait", 12),
        scan_timeout=getattr(args, "scan_timeout", 5),
        mac=getattr(args, "mac", None),
        name=getattr(args, "name", "") or "",
        remote_cmd=getattr(args, "remote_cmd", "uname -a"),
        probe_scan=getattr(args, "probe_scan", False),
    )


def _run_keyboard_command(args, handler) -> int:
    """Run a keyboard command; save credentials only when the command succeeds."""
    code = handler(_kb_args_view(args)) or 0
    if code == 0:
        maybe_save_password(args, host_from_args(args), password_from_args(args))
    return code


def _mode_exit(mode: str, kb_code: int, ptr_code: int) -> int:
    if mode == "keyboard":
        return kb_code
    if mode == "pointer":
        return ptr_code
    return kb_code if kb_code != 0 else ptr_code


def cmd_install(args) -> int:
    mode = _install_mode(args)

    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== install keyboard (BT service) ===")
        try:
            kb_code = _run_keyboard_command(args, kb.cmd_install_service)
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
        try:
            from paperpointer.cli import cmd_install as ptr_install

            bootstrap = not getattr(args, "no_bootstrap", False)
            with pointer_session(args) as c:
                ptr_code = ptr_install(c, bootstrap=bootstrap)
        except Exception as e:
            print(f"pointer install error: {e}", file=sys.stderr)
            ptr_code = 1
        if ptr_code != 0 and mode == "all" and kb_code == 0:
            print(
                "install --all: pointer failed after keyboard succeeded "
                "(partial install: keyboard remains).",
                file=sys.stderr,
            )

    return _mode_exit(mode, kb_code, ptr_code)


def cmd_bootstrap_python(args) -> int:
    """Install Entware + Python 3 on a vanilla tablet (pointer prerequisite)."""
    try:
        from paperpointer.cli import bootstrap_tablet_python, preflight_tablet_python

        print(
            "Bootstrapping tablet Python (Entware + python3). "
            "Tablet needs Wi-Fi; this can take several minutes.",
            flush=True,
        )
        with pointer_session(args) as c:
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


def cmd_uninstall(args) -> int:
    mode = _install_mode(args)
    kb_code = 0
    if mode in ("keyboard", "all"):
        print("=== uninstall keyboard ===")
        try:
            kb_code = _run_keyboard_command(args, kb.cmd_uninstall_service)
        except Exception as e:
            print(f"keyboard uninstall error: {e}", file=sys.stderr)
            kb_code = 1
    ptr_code = 0
    if mode in ("pointer", "all"):
        print("=== uninstall pointer ===")
        try:
            from paperpointer.cli import cmd_uninstall as ptr_uninstall

            with pointer_session(args) as c:
                ptr_code = ptr_uninstall(c)
        except Exception as e:
            print(f"pointer uninstall error: {e}", file=sys.stderr)
            ptr_code = 1
    return _mode_exit(mode, kb_code, ptr_code)


def _install_mode(args) -> str:
    if getattr(args, "keyboard", False):
        return "keyboard"
    if getattr(args, "pointer", False):
        return "pointer"
    if getattr(args, "all", False):
        return "all"
    raise CliError("install/uninstall requires one of --keyboard, --pointer, --all")


def cmd_set_layout(args) -> int:
    """Apply a keyboard language layout (libepaper patch on Paper Pro / Move)."""
    from core import layout_patcher
    from shared.layouts import KEYBOARD_LAYOUTS, LAYOUT_MAP

    raw = (getattr(args, "layout", None) or "").strip()
    if not raw:
        print("error: --layout required (key or display name)", file=sys.stderr)
        print("keys:", ", ".join(k for _, k in KEYBOARD_LAYOUTS), file=sys.stderr)
        return 2
    if raw in LAYOUT_MAP:
        key = LAYOUT_MAP[raw]
        display = raw
    else:
        key = raw.lower().replace(" ", "_")
        display = next((n for n, k in KEYBOARD_LAYOUTS if k == key), key)
        if key not in {k for _, k in KEYBOARD_LAYOUTS}:
            print(f"error: unknown layout {raw!r}", file=sys.stderr)
            print(
                "Try a key (e.g. it, us, de) or display name (e.g. Italian).",
                file=sys.stderr,
            )
            return 2

    try:
        print(f"=== set-layout {display} ({key}) ===")
        with keyboard_session(args) as (ssh, _, _):
            layout_patcher.apply_layout(ssh, key, status_cb=print)
        cfg = config.load()
        cfg["keyboard_layout"] = display
        config.save(cfg)
        print(f"OK: layout={key}")
        return 0
    except Exception as e:
        print(f"set-layout error: {e}", file=sys.stderr)
        return 1


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
    marker_values = {
        "HOME_YES": ("home_present", True),
        "HOME_NO": ("home_present", False),
        "UNIT_ETC_YES": ("unit_etc_present", True),
        "UNIT_ETC_NO": ("unit_etc_present", False),
        "UNIT_USR_YES": ("unit_usr_present", True),
        "UNIT_USR_NO": ("unit_usr_present", False),
        "UNIT_YES": ("unit_legacy_present", True),
        "UNIT_NO": ("unit_legacy_present", False),
    }
    facts = {
        "home_present": False,
        "unit_etc_present": False,
        "unit_usr_present": False,
        "unit_legacy_present": False,
        "is_active": None,
        "is_failed": False,
    }
    raw_lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    seen: set[str] = set()
    for line in raw_lines:
        if line in marker_values:
            key, value = marker_values[line]
            facts[key] = value
            seen.add(line.rsplit("_", 1)[0])
        elif line.startswith("ACTIVE:"):
            facts["is_active"] = line.partition(":")[2].strip().lower() == "active"
            seen.add("ACTIVE")
        elif line.startswith("FAILED:"):
            facts["is_failed"] = line.partition(":")[2].strip().lower() == "failed"
            seen.add("FAILED")

    facts["unit_file_present"] = any(
        facts[key]
        for key in ("unit_etc_present", "unit_usr_present", "unit_legacy_present")
    )
    facts["complete"] = (
        "HOME" in seen
        and ("UNIT" in seen or {"UNIT_ETC", "UNIT_USR"} <= seen)
        and {"ACTIVE", "FAILED"} <= seen
    )
    facts["raw_lines"] = raw_lines
    return facts


def cmd_status(args) -> int:
    """Merged status: labeled sections; optional/residual components exit 0."""
    keyboard_lines: list[str] = []
    keyboard = ComponentStatus(
        state="unknown",
        detail="keyboard probe not run",
        exit_code=0,
        label="keyboard",
    )
    try:
        with keyboard_session(args) as (ssh, _, _):
            info = device_mod.detect(ssh)
            cfg = config.load()
            state = bluetooth.verify_device_state(ssh, cfg)
            if not state.get("keyboard_mac") and cfg.get("keyboard_mac"):
                cfg["keyboard_mac"] = ""
                cfg["keyboard_name"] = ""
                config.save(cfg)
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

    pointer = classify_pointer_status(
        home_present=False,
        unit_file_present=False,
        is_active=None,
    )
    settings_lines: list[str] | None = None
    try:
        from paperpointer.sshutil import (
            REMOTE_HOME,
            UNIT_ETC,
            UNIT_NAME,
            UNIT_USR,
            run,
        )

        with pointer_session(args) as c:
            out, err, code = run(
                c,
                f"test -d {REMOTE_HOME} && echo HOME_YES || echo HOME_NO; "
                f"test -f {UNIT_ETC} && echo UNIT_ETC_YES || echo UNIT_ETC_NO; "
                f"test -f {UNIT_USR} && echo UNIT_USR_YES || echo UNIT_USR_NO; "
                f'printf "ACTIVE:%s\\n" "$(systemctl is-active {UNIT_NAME} 2>/dev/null || echo inactive)"; '
                f'printf "FAILED:%s\\n" "$(systemctl is-failed {UNIT_NAME} 2>/dev/null || echo unknown)"',
                timeout=20,
            )
            if code != 0:
                detail = (err or out or "no diagnostic output").strip()
                raise RuntimeError(f"pointer probe failed ({code}): {detail[:240]}")
            facts = parse_pointer_probe_output(out)
            if not facts["complete"]:
                raise RuntimeError(
                    "pointer probe returned incomplete output: "
                    + ";".join(facts["raw_lines"])[:240]
                )
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
            settings_lines = _probe_settings_ui(run, c)
    except Exception as e:
        pointer = ComponentStatus(
            state="unknown",
            detail=f"pointer probe error: {e}",
            exit_code=1,
            label="pointer",
        )
        settings_lines = [f"error: {e}"]

    sys.stdout.write(
        format_status_report(keyboard_lines, pointer, keyboard=keyboard)
    )
    if settings_lines is not None:
        sys.stdout.write("=== settings_ui ===\n")
        for ln in settings_lines:
            sys.stdout.write(f"{ln}\n")
    return merge_exit_codes(keyboard, pointer)


def _probe_settings_ui(run, c) -> list[str]:
    """Return human lines for Settings → Help / XOVI state."""
    out, _, _ = run(
        c,
        "set +e; "
        "QMD=/home/root/xovi/exthome/qt-resource-rebuilder/paperpointer-settings.qmd; "
        "SRC=/home/root/.paperpointer/paperpointer-settings.qmd; "
        "test -f \"$QMD\" && echo QMD_ACTIVE=yes || echo QMD_ACTIVE=no; "
        "test -f \"$SRC\" && echo QMD_STAGED=yes || echo QMD_STAGED=no; "
        "test -x /home/root/xovi/start && echo XOVI_START=yes || echo XOVI_START=no; "
        "test -p /run/xovi-mb && echo XOVI_MB=yes || echo XOVI_MB=no; "
        "pidof xochitl >/dev/null && echo XOCHITL=yes || echo XOCHITL=no",
        timeout=15,
    )
    flags = {}
    for ln in (out or "").splitlines():
        if "=" in ln:
            k, v = ln.strip().split("=", 1)
            flags[k] = v
    lines = [
        f"qmd_active: {flags.get('QMD_ACTIVE', '?')}",
        f"qmd_staged: {flags.get('QMD_STAGED', '?')}",
        f"xovi_start: {flags.get('XOVI_START', '?')}",
        f"xovi_broker: {flags.get('XOVI_MB', '?')}",
        f"xochitl: {flags.get('XOCHITL', '?')}",
    ]
    active = flags.get("QMD_ACTIVE") == "yes"
    broker = flags.get("XOVI_MB") == "yes"
    staged = flags.get("QMD_STAGED") == "yes"
    if active and broker:
        lines.append("state: ok")
        lines.append(
            "detail: Settings -> Help should show PaperHid (open Help once if unsure)"
        )
    elif staged or active:
        lines.append("state: needs_enable")
        lines.append(
            "detail: PaperHid Help files present but XOVI not tethered "
            "(Help looks stock / empty)"
        )
        lines.append("fix: python cli.py settings-ui")
    elif flags.get("XOVI_START") == "yes":
        lines.append("state: not_installed")
        lines.append("detail: XOVI present; Settings panel not installed yet")
        lines.append("fix: python cli.py settings-ui")
    else:
        lines.append("state: not_installed")
        lines.append(
            "detail: optional Settings UI not installed "
            "(firmware 3.28.0.164 + XOVI required)"
        )
        lines.append("fix: install XOVI, then python cli.py settings-ui")
    return lines


def cmd_settings_ui(args) -> int:
    """Install or refresh Settings → Help (first-time setup and re-enable)."""
    print("=== Settings -> Help UI ===")
    print(
        "Installs or refreshes the PaperHid Help panel and starts XOVI "
        "(first-time setup, or after freeze / stock reboot / empty Help)."
    )
    try:
        from paperpointer.cli import cmd_enable_settings_ui

        with pointer_session(args) as c:
            code = cmd_enable_settings_ui(c)
        if code == 0:
            print(
                "OK: open Settings -> Help on the tablet (fresh open).\n"
                "Optional: python cli.py pointer settings-ui-check"
            )
        return code
    except Exception as e:
        print(f"settings-ui error: {e}", file=sys.stderr)
        return 1


cmd_repair_ui = cmd_settings_ui


def cmd_detect(args) -> int:
    """Merged detect: keyboard device detect + pointer detect dump."""
    print("=== keyboard / device ===")
    try:
        kb_code = _run_keyboard_command(args, kb.cmd_detect)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        kb_code = 1
    print("=== pointer / inputs ===")
    try:
        from paperpointer.cli import cmd_detect as ptr_detect

        with pointer_session(args) as c:
            ptr_code = ptr_detect(c)
    except Exception as e:
        print(f"pointer detect error: {e}", file=sys.stderr)
        ptr_code = 1
    return kb_code if kb_code != 0 else ptr_code


def cmd_pointer(args) -> int:
    """Dispatch ``pointer <cmd>`` subcommands."""
    from paperpointer.cli import dispatch_pointer

    if not getattr(args, "cmd", None):
        args.cmd = getattr(args, "pointer_cmd", None)
    with pointer_session(args) as c:
        return dispatch_pointer(args, c)


def shared_root_flags(*, for_subparser: bool = False) -> argparse.ArgumentParser:
    """SSH/auth flags that work before *or* after the subcommand."""
    shared = argparse.ArgumentParser(add_help=False)
    default = argparse.SUPPRESS if for_subparser else None
    save_default = argparse.SUPPRESS if for_subparser else False
    timeout_default = argparse.SUPPRESS if for_subparser else 15
    shared.add_argument("--ip", default=default, help="Tablet IP (default 10.11.99.1)")
    shared.add_argument("--host", default=default, help="Alias for --ip")
    shared.add_argument("--password", default=default, help="SSH password")
    shared.add_argument(
        "--save-password",
        action="store_true",
        default=save_default,
        help="Save password to ~/.paperwriter/config.json",
    )
    shared.add_argument("--timeout", type=int, default=timeout_default)
    return shared


def build_parser() -> argparse.ArgumentParser:
    parent_shared = shared_root_flags(for_subparser=False)
    p = argparse.ArgumentParser(
        prog="paperhid",
        description="PaperHid: Bluetooth keyboard + mouse/pointer for reMarkable Paper Pro",
        parents=[parent_shared],
    )
    p.set_defaults(ip=None, host=None, password=None, save_password=False, timeout=15)
    child_shared = shared_root_flags(for_subparser=True)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", parents=[child_shared], help="Merged device + pointer detect")
    sub.add_parser("status", parents=[child_shared], help="Merged keyboard + pointer status")

    inst = sub.add_parser(
        "install",
        parents=[child_shared],
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

    un = sub.add_parser(
        "uninstall",
        parents=[child_shared],
        help="Uninstall keyboard, pointer, or both",
    )
    ug = un.add_mutually_exclusive_group(required=True)
    ug.add_argument("--keyboard", action="store_true")
    ug.add_argument("--pointer", action="store_true")
    ug.add_argument("--all", action="store_true")

    sub.add_parser(
        "bootstrap-python",
        parents=[child_shared],
        help="Install Entware + Python 3 on tablet (mouse prerequisite)",
    )
    sp = sub.add_parser("ssh", parents=[child_shared])
    sp.add_argument("remote_cmd", nargs="?", default="uname -a")
    sp = sub.add_parser("scan", parents=[child_shared])
    sp.add_argument(
        "--scan-timeout",
        type=int,
        default=5,
        help="BLE discovery seconds (default 5; early-exits when --name matches)",
    )
    sp = sub.add_parser("pair", parents=[child_shared])
    sp.add_argument("--mac", default=None)
    sp.add_argument("--name", default="")
    sp.add_argument(
        "--scan-timeout",
        type=int,
        default=5,
        help="BLE discovery seconds when resolving --name (default 5; early-exit on match)",
    )
    sp = sub.add_parser("save-mac", parents=[child_shared])
    sp.add_argument("--mac", required=True)
    sp.add_argument("--name", default="")
    sp = sub.add_parser("unpair", parents=[child_shared])
    sp.add_argument("--mac", default=None)
    sub.add_parser("refuse-layout", parents=[child_shared])
    sp = sub.add_parser(
        "set-layout",
        parents=[child_shared],
        help="Apply keyboard language layout (Paper Pro / Move)",
    )
    sp.add_argument(
        "--layout",
        required=True,
        help="Layout key (it, us, de, ...) or display name (Italian, ...)",
    )
    sub.add_parser(
        "settings-ui",
        parents=[child_shared],
        help=(
            "Install or refresh Settings -> Help (first-time after XOVI, "
            "or re-enable after freeze/empty Help)"
        ),
    )
    sub.add_parser(
        "repair-ui",
        parents=[child_shared],
        help="Alias for settings-ui (same command)",
    )
    sp = sub.add_parser("diagnose", parents=[child_shared])
    sp.add_argument("--probe-scan", action="store_true")

    from paperpointer.cli import register_pointer_commands, shared_flag_parser

    ptr = sub.add_parser("pointer", parents=[child_shared], help="Pointer/mouse commands")
    shared = shared_flag_parser(for_subparser=True)
    ptr_sub = ptr.add_subparsers(dest="pointer_cmd", required=True)
    register_pointer_commands(ptr_sub, shared)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "host", None) and not getattr(args, "ip", None):
        args.ip = args.host

    keyboard_handlers = {
        "ssh": kb.cmd_ssh,
        "scan": kb.cmd_scan,
        "pair": kb.cmd_pair,
        "save-mac": kb.cmd_save_mac,
        "unpair": kb.cmd_unpair,
        "refuse-layout": kb.cmd_refuse_layout,
        "diagnose": kb.cmd_diagnose,
    }
    handlers = {
        "detect": cmd_detect,
        "status": cmd_status,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "bootstrap-python": cmd_bootstrap_python,
        "set-layout": cmd_set_layout,
        "settings-ui": cmd_settings_ui,
        "repair-ui": cmd_settings_ui,
        "pointer": cmd_pointer,
    }
    try:
        if args.command == "pointer":
            args.cmd = args.pointer_cmd
            return cmd_pointer(args)
        if args.command in keyboard_handlers:
            return _run_keyboard_command(args, keyboard_handlers[args.command])
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
