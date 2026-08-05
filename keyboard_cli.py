"""Keyboard-only CLI helpers used by PaperHid (scan / pair / service).

Password: --password, PAPERHID_PASSWORD (or legacy aliases), or config.
Never prints the password. Does not write it unless --save-password.
"""
from __future__ import annotations

import argparse
import os
import sys

from core.ssh_client import SSHClient
from core import config, service_installer, bluetooth, layout_patcher, native_app_installer
from core import device as device_mod


class CliError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _resolve_password(args, cfg):
    """CLI --password, PAPERHID_PASSWORD (legacy aliases), or config."""
    from core.credentials import resolve_password

    # Prefer monorepo resolver; fall back to cfg only when resolver empty
    # (resolver already includes config when use_config=True).
    pw = resolve_password(getattr(args, "password", None), use_config=True)
    if pw:
        return pw
    return config.get_password(cfg)


def _connect(args):
    cfg = config.load()
    ip = args.ip or cfg.get("ip") or "10.11.99.1"
    password = _resolve_password(args, cfg)
    if not password:
        raise CliError(
            "no password (use --password, PAPERHID_PASSWORD, or saved config)"
        )
    ssh = SSHClient()
    ssh.connect(ip, password, timeout=args.timeout)
    if args.save_password:
        cfg["ip"] = ip
        config.set_password(cfg, password)
        config.save(cfg)
    return ssh, cfg, ip


def _with_ssh(args, fn):
    ssh, cfg, ip = _connect(args)
    try:
        return fn(ssh, cfg, ip)
    finally:
        ssh.disconnect()


def cmd_detect(args):
    def run(ssh, cfg, ip):
        info = device_mod.detect(ssh)
        for k in (
            "model", "label", "hostname", "img_version", "kernel",
            "supports_bt_keyboard_service", "supports_layout_patch",
            "supports_native_app", "raw_model",
        ):
            print(f"{k}: {info.get(k, '')}")
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
        if not mac:
            print("scanning for keyboard...")
            devices = bluetooth.scan_devices(ssh, timeout=args.scan_timeout)
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
            print(f"selected: {name} ({mac})")
        print(f"pairing {mac}...")
        bluetooth.pair_and_connect(ssh, mac, old_mac=cfg.get("keyboard_mac") or None)
        service_installer.save_keyboard_mac(ssh, mac)
        cfg["keyboard_mac"] = mac
        cfg["keyboard_name"] = name or mac
        cfg["service_installed"] = True
        config.save(cfg)
        print(f"paired_ok: {mac} {name}")
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
        mac = args.mac or cfg.get("keyboard_mac") or ""
        if mac:
            bluetooth.remove(ssh, mac)
            print(f"removed {mac}")
        ssh.exec(f"rm -f {service_installer.KEYBOARD_MAC_PATH}", timeout=5)
        cfg["keyboard_mac"] = ""
        cfg["keyboard_name"] = ""
        config.save(cfg)
        print("keyboard cleared")
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


def cmd_refuse_native(args):
    def run(ssh, cfg, ip):
        try:
            native_app_installer.install(ssh)
            print("error: native install was allowed", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(f"refused_ok: {e}")
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


def build_parser():
    p = argparse.ArgumentParser(description="PaperHid keyboard CLI (reMarkable Paper Pro)")
    p.add_argument("--ip", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--save-password", action="store_true")
    p.add_argument("--timeout", type=int, default=15)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("detect")
    sp = sub.add_parser("ssh")
    sp.add_argument("remote_cmd", nargs="?", default="uname -a")
    sp = sub.add_parser("install-service")
    sp.add_argument("--wait", type=int, default=12)
    sub.add_parser("uninstall-service")
    sub.add_parser("status")
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
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "detect": cmd_detect,
        "ssh": cmd_ssh,
        "install-service": cmd_install_service,
        "uninstall-service": cmd_uninstall_service,
        "status": cmd_status,
        "scan": cmd_scan,
        "pair": cmd_pair,
        "save-mac": cmd_save_mac,
        "unpair": cmd_unpair,
        "refuse-layout": cmd_refuse_layout,
        "refuse-native": cmd_refuse_native,
        "diagnose": cmd_diagnose,
    }
    try:
        return handlers[args.command](args)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
