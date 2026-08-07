"""Keyboard host commands (scan / pair / BT service).

Part of the unified ``host_cli`` module. Prefer ``python cli.py …``.
Connection + credential save policy lives in ``host_cli.session``.
"""
from __future__ import annotations

import sys

from core import config, service_installer, bluetooth, layout_patcher
from core import device as device_mod
from host_cli.errors import CliError
from host_cli.session import host_from_args, keyboard_session, maybe_save_password, password_from_args


def _with_ssh(args, fn):
    """Run *fn(ssh, cfg, ip)* on a keyboard session.

    When called via the root dispatcher, ``args.save_password`` is forced False
    and the dispatcher saves after a zero exit. Direct callers may set
    ``save_password`` and persist on success here.
    """
    host = host_from_args(args)
    password = password_from_args(args)
    with keyboard_session(args, persist_password=False) as (ssh, host, password):
        code = fn(ssh, config.load(), host) or 0
    if code == 0:
        maybe_save_password(args, host, password)
    return code


def _saved_keyboard_mac(ssh, cfg) -> str:
    """Return the device-side keyboard MAC, falling back to host config."""
    candidates = []
    try:
        out, _, code = ssh.exec(
            f"cat {service_installer.KEYBOARD_MAC_PATH} 2>/dev/null",
            timeout=5,
        )
        if code == 0:
            candidates.append((out or "").strip())
    except Exception:
        pass
    candidates.append((cfg or {}).get("keyboard_mac") or "")
    for candidate in candidates:
        if candidate:
            try:
                return bluetooth.normalize_mac(candidate)
            except ValueError:
                continue
    return ""


def _clear_saved_keyboard(ssh, cfg) -> None:
    ssh.exec(f"rm -f {service_installer.KEYBOARD_MAC_PATH}", timeout=5)
    cfg["keyboard_mac"] = ""
    cfg["keyboard_name"] = ""
    config.save(cfg)


def cmd_detect(args):
    def run(ssh, cfg, ip):
        info = device_mod.detect(ssh)
        # Short first-run summary (not a full sysdump).
        kernel = (info.get("kernel") or "").strip()
        # uname -a → keep only the version token when present
        if kernel.startswith("Linux "):
            parts = kernel.split()
            kernel = parts[2] if len(parts) >= 3 else kernel
        rows = (
            ("device", info.get("label") or info.get("model") or "?"),
            ("model", info.get("model") or ""),
            ("hostname", info.get("hostname") or ""),
            ("firmware", info.get("img_version") or ""),
            ("kernel", kernel),
            ("bt_keyboard", "yes" if info.get("supports_bt_keyboard_service") else "no"),
            ("layout_patch", "yes" if info.get("supports_layout_patch") else "no"),
        )
        for key, value in rows:
            print(f"{key}: {value}")
        return 0
    return _with_ssh(args, run)


def cmd_ssh(args):
    def run(ssh, cfg, ip):
        out, err, code = ssh.exec(args.remote_cmd or "uname -a", timeout=args.timeout)
        sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        return code
    return _with_ssh(args, run)


def cmd_install_service(args):
    def run(ssh, cfg, ip):
        print("installing bluetooth keyboard service...")
        service_installer.install(ssh)
        print(f"waiting up to {args.wait}s for BT controller...")
        try:
            service_installer.wait_for_controller(ssh, timeout=args.wait)
        except RuntimeError as e:
            print(f"warning: {e}", file=sys.stderr)
        state = bluetooth.verify_device_state(ssh, cfg)
        print(f"service_installed: {state['service_installed']}")
        print(f"bt_powered: {state['bt_powered']}")
        print(f"controller_ready: {state.get('controller_ready')}")
        out, _, _ = ssh.exec("lsmod", timeout=10)
        for name in ("btnxpuart", "uhid", "bluetooth"):
            print(f"module_{name}: {'yes' if name in out else 'no'}")
        out, _, _ = ssh.exec("bluetoothctl show", timeout=15)
        powered = any("Powered:" in ln and "yes" in ln.lower() for ln in out.splitlines())
        print(f"bluetoothctl_powered: {powered}")
        cfg["service_installed"] = True
        config.save(cfg)
        if not powered and not state["bt_powered"]:
            print("warning: controller not powered yet", file=sys.stderr)
            return 1
        return 0
    return _with_ssh(args, run)


def cmd_uninstall_service(args):
    def run(ssh, cfg, ip):
        print("uninstalling bluetooth keyboard service...")
        service_installer.uninstall(ssh)
        cfg["service_installed"] = False
        cfg["keyboard_mac"] = ""
        cfg["keyboard_name"] = ""
        config.save(cfg)
        print("uninstalled")
        return 0
    return _with_ssh(args, run)


def cmd_status(args):
    def run(ssh, cfg, ip):
        info = device_mod.detect(ssh)
        state = bluetooth.verify_device_state(ssh, cfg)
        if not state.get("keyboard_mac") and cfg.get("keyboard_mac"):
            cfg["keyboard_mac"] = ""
            cfg["keyboard_name"] = ""
            config.save(cfg)
        print(f"device: {info.get('label')} ({info.get('model')})")
        print(f"img_version: {info.get('img_version')}")
        for k, v in state.items():
            print(f"{k}: {v}")
        return 0
    return _with_ssh(args, run)


def cmd_scan(args):
    def run(ssh, cfg, ip):
        print(f"scanning {args.scan_timeout}s (ensuring adapter ready)...")
        devices = bluetooth.scan_devices(ssh, timeout=args.scan_timeout)
        print(f"found: {len(devices)}")
        for d in devices:
            print(f"{d['mac']}\t{d['name']}")
        return 0
    return _with_ssh(args, run)


def cmd_pair(args):
    def run(ssh, cfg, ip):
        mac, name = args.mac, args.name or ""
        # After a successful discovery scan we already know the device — skip
        # the second full scan inside pair_and_connect (saves ~10–20s).
        discovered = False
        if not mac:
            print("scanning for device (keyboard or mouse)...")
            devices = bluetooth.scan_devices(
                ssh,
                timeout=args.scan_timeout,
                until_name=name or None,
            )
            print(f"found: {len(devices)}")
            for d in devices:
                print(f"  {d['mac']}\t{d['name']}")
            if name:
                match = bluetooth.find_device_by_name(devices, name)
            else:
                match = devices[0] if len(devices) == 1 else None
                if not match:
                    for d in devices:
                        n = (d["name"] or "").lower()
                        if any(k in n for k in ("key", "clvx", "clev", "board", "kb")):
                            match = d
                            break
            if not match:
                print("error: no matching device; pass --mac or --name", file=sys.stderr)
                return 1
            mac, name = match["mac"], match["name"]
            discovered = True
            print(f"selected: {name} ({mac})")
        elif bluetooth.device_known(ssh, mac):
            discovered = True
        print(f"pairing {mac}...")
        # Multi-device: never unpair other HID devices when bonding a new one.
        # Keyboard replacement (old keyboard → new keyboard) is handled below.
        bluetooth.pair_and_connect(
            ssh,
            mac,
            old_mac=None,
            replace_previous=False,
            pre_scan=not discovered,
        )
        info = bluetooth.get_device_info(ssh, mac)
        if not name:
            name = bluetooth.get_device_name(ssh, mac) or mac
        role = bluetooth.classify_device_role(info, name)
        print(f"role: {role}")
        mac = bluetooth.normalize_mac(mac)

        if role == "pointer":
            # Mouse/touchpad: leave keyboard MAC alone; clear if it wrongly
            # pointed at this pointer (legacy bug).
            if _saved_keyboard_mac(ssh, cfg) == mac:
                _clear_saved_keyboard(ssh, cfg)
                print("cleared keyboard MAC (was this pointer)")
            print(f"paired_ok: {mac} {name} (pointer — keyboard unchanged)")
            print(
                "note: keyboard + mouse can stay paired together; "
                "re-pair keyboard only if it was removed by an older client"
            )
            return 0

        if role == "unknown":
            print(
                "warning: could not classify as keyboard or pointer; "
                "paired but not saved as keyboard MAC. "
                "If this is a keyboard: python cli.py save-mac --mac " + mac,
                file=sys.stderr,
            )
            print(f"paired_ok: {mac} {name} (unknown role)")
            return 0

        # role == keyboard: update reconnect MAC; replace previous keyboard only.
        old_kb = _saved_keyboard_mac(ssh, cfg)
        if old_kb and old_kb != mac:
            old_info = bluetooth.get_device_info(ssh, old_kb)
            old_role = bluetooth.classify_device_role(old_info)
            if old_role == "pointer":
                print(
                    f"note: previous keyboard MAC {old_kb} is a pointer; "
                    "leaving it paired"
                )
            elif old_role == "keyboard":
                print(f"replacing previous keyboard {old_kb}")
                try:
                    bluetooth.remove(ssh, old_kb)
                except Exception as e:
                    print(f"warning: could not remove old keyboard: {e}")

        service_installer.save_keyboard_mac(ssh, mac)
        cfg["keyboard_mac"] = mac
        cfg["keyboard_name"] = name or mac
        cfg["service_installed"] = True
        config.save(cfg)
        print(f"paired_ok: {mac} {name} (keyboard)")
        return 0
    return _with_ssh(args, run)


def cmd_save_mac(args):
    def run(ssh, cfg, ip):
        if not args.mac:
            print("error: --mac required", file=sys.stderr)
            return 2
        service_installer.save_keyboard_mac(ssh, args.mac)
        cfg["keyboard_mac"] = args.mac
        if args.name:
            cfg["keyboard_name"] = args.name
        config.save(cfg)
        print(f"saved mac {args.mac}")
        return 0
    return _with_ssh(args, run)


def cmd_unpair(args):
    def run(ssh, cfg, ip):
        try:
            mac = bluetooth.normalize_mac(args.mac) if args.mac else _saved_keyboard_mac(ssh, cfg)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if not mac:
            print("error: no MAC (pass --mac)", file=sys.stderr)
            return 2

        bluetooth.remove(ssh, mac)
        print(f"removed {mac}")

        if _saved_keyboard_mac(ssh, cfg) == mac:
            _clear_saved_keyboard(ssh, cfg)
            print("keyboard MAC cleared")
        else:
            print("keyboard MAC left unchanged (other device removed)")
        return 0
    return _with_ssh(args, run)


def cmd_refuse_layout(args):
    def run(ssh, cfg, ip):
        info = device_mod.detect(ssh)
        print(
            f"device: {info.get('label')} "
            f"supports_layout_patch={info.get('supports_layout_patch')}"
        )
        try:
            data = ssh.download_bytes(layout_patcher.LIBEPAPER_PATH)
        except Exception as e:
            print(f"error: cannot download libepaper.so: {e}", file=sys.stderr)
            return 1
        found = layout_patcher.find_keymap_offset(data)
        if not found:
            print("error: keymap table not found in libepaper.so", file=sys.stderr)
            return 1
        off, count = found
        print(f"keymap_ok: offset=0x{off:x} entries={count} size={len(data)}")
        return 0
    return _with_ssh(args, run)


def cmd_diagnose(args):
    def run(ssh, cfg, ip):
        info = device_mod.detect(ssh)
        print(f"device: {info.get('label')} ({info.get('model')})")
        print(f"img_version: {info.get('img_version')}")
        for cmd, label in [
            ("cat /proc/uptime", "uptime"),
            ("systemctl is-active bluetooth remarkable-bt-keyboard.service 2>&1 || true", "services"),
            ("lsmod | grep -E 'btnxp|uhid|bluetooth|iw61' || true", "modules"),
            ("bluetoothctl show 2>&1 | grep -E 'Controller|Powered|Pairable|Discovering' || true", "adapter"),
            ("hciconfig hci0 2>&1 || true", "hci"),
            (
                "dmesg | grep -i -E 'FW Download|ChipID|Opcode 0x2005|Opcode 0x2041|ps_state' "
                "| tail -n 25 || true",
                "dmesg_bt",
            ),
            ("rfkill list 2>&1 || true", "rfkill"),
        ]:
            out, err, _ = ssh.exec(cmd, timeout=15)
            print(f"--- {label} ---")
            sys.stdout.write(out or "")
            if err:
                sys.stdout.write(err)
        if args.probe_scan:
            print("--- probe_scan ---")
            try:
                ok, detail = bluetooth.probe_radio_scan_health(ssh)
                print(f"scan_health: {'ok' if ok else 'BROKEN'}")
                print(f"detail: {detail}")
            except Exception as e:
                print(f"probe_error: {e}")
                return 1
            return 0 if ok else 2
        return 0
    return _with_ssh(args, run)
