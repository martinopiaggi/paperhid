"""Bluetooth ops for Paper Pro NXP — desktop (SSH) and on-device (local)."""
from __future__ import annotations

import logging
import re
import time

from shared.constants import (
    KEYBOARD_MAC_PATH,
    LIB_REMOTE_PATH,
    SCRIPT_DIR,
    SERVICE_NAME,
    SERVICE_PERSISTENT_PATH,
    SCRIPT_REMOTE_PATH,
)
from shared.transport import Transport

log = logging.getLogger("paperhid.bluetooth")

# Strict Bluetooth address: only this form is interpolated into root shell cmds.
_MAC_COLON = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_MAC_DASH = re.compile(r"^([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}$")
_MAC_COMPACT = re.compile(r"^[0-9A-Fa-f]{12}$")


def normalize_mac(mac: str) -> str:
    """Return ``AA:BB:CC:DD:EE:FF`` or raise ``ValueError`` for unsafe input.

    Accepts colon, dash, or 12-hex compact forms. Rejects everything else so
    callers never interpolate free-form strings into root shell commands.
    """
    if mac is None:
        raise ValueError("Bluetooth MAC required")
    raw = str(mac).strip().replace(" ", "")
    if _MAC_COLON.match(raw):
        return raw.upper()
    if _MAC_DASH.match(raw):
        return raw.replace("-", ":").upper()
    if _MAC_COMPACT.match(raw):
        return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).upper()
    raise ValueError(f"invalid Bluetooth MAC: {mac!r}")


_NXP_HINT = (
    "Paper Pro BT controller often sticks after sleep (NXP btnxpuart power-save).\n"
    "Fix: reboot → unlock → wait ~30s → keyboard pairing mode "
    "(disconnect from Windows first) → retry.\n"
    "Do not: modprobe -r btnxpuart  or  hciconfig hci0 down"
)

_NXP_SCAN_BROKEN = (
    "Paper Pro Bluetooth discovery is broken on this firmware.\n\n"
    "The NXP chip powers on, but LE scan HCI (0x2005/0x2041) times out (-110). "
    "BlueZ then reports InProgress and never lists devices.\n\n"
    "Firmware/driver limitation, not a wrong password or MAC.\n"
    "Try: reboot → unlock → wait ~30s → pairing mode → Scan again.\n"
    "Do not: modprobe -r btnxpuart  or  hciconfig hci0 down"
)


def _parse_device_lines(blob):
    devices, seen = [], set()
    if not blob:
        return devices
    for line in blob.replace("\t", " ").splitlines():
        m = re.match(r"(?:Device\s+)?([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
        if not m:
            continue
        mac, name = m.group(1).upper(), m.group(2).strip()
        if name and mac not in seen:
            seen.add(mac)
            devices.append({"mac": mac, "name": name})
    return devices


def find_device_by_name(devices, name_substr):
    if not name_substr:
        return None
    needle = name_substr.lower()
    for d in devices or []:
        if needle in (d.get("name") or "").lower():
            return d
    return None


def _hci_fail_count(t: Transport):
    out, _, _ = t.run(
        "dmesg | grep -c -E 'Opcode 0x2005 failed|Opcode 0x2041 failed' || echo 0",
        timeout=8,
    )
    try:
        return int(re.findall(r"\d+", out or "0")[-1])
    except Exception:
        return 0


def _runtime_pm_on(t: Transport):
    t.run(
        "echo paperwriter.bt >> /sys/power/wake_lock 2>/dev/null || true; "
        "echo user.lock >> /sys/power/wake_lock 2>/dev/null || true; "
        "chmod 555 /etc/bluetooth 2>/dev/null || true; "
        "DEV=$(readlink -f /sys/class/bluetooth/hci0 2>/dev/null); i=0; "
        'while [ -n "$DEV" ] && [ "$DEV" != "/" ] && [ "$i" -lt 14 ]; do '
        '  [ -f "$DEV/power/control" ] && echo on > "$DEV/power/control" 2>/dev/null; '
        '  [ -f "$DEV/power/autosuspend_delay_ms" ] && echo -1 > "$DEV/power/autosuspend_delay_ms" 2>/dev/null; '
        '  DEV=$(dirname "$DEV"); i=$((i+1)); done; true',
        timeout=10,
    )


def disable_nxp_autosleep(t: Transport):
    out, err, _ = t.run(
        "(hcitool cmd 0x3f 0x23 0x03 0x00 0x00) >/tmp/pw-nxp-ps.out 2>&1 & "
        "CP=$!; sleep 3; kill $CP 2>/dev/null; wait $CP 2>/dev/null; "
        "cat /tmp/pw-nxp-ps.out 2>/dev/null || true",
        timeout=12,
    )
    text = (out or "") + (err or "")
    flat = text.replace("\n", " ")
    ok = ("03 00 00" in flat and "HCI Event" in text) or ("23 03 00" in flat)
    log.info("NXP auto-sleep disable %s", "OK" if ok else f"inconclusive: {flat[:160]}")
    return ok, text


def _wifi_gate(t: Transport, block: bool):
    if block:
        t.run(
            "rfkill block wifi 2>/dev/null || true; "
            "ip link set wlan0 down 2>/dev/null || true; true",
            timeout=8,
        )
    else:
        t.run(
            "ip link set wlan0 up 2>/dev/null || true; "
            "rfkill unblock wifi 2>/dev/null || true; true",
            timeout=8,
        )


def _btlib_init(t: Transport, timeout=30) -> bool:
    if not t.exists(LIB_REMOTE_PATH):
        return False
    script = (
        f"HOME_DIR={SCRIPT_DIR}; "
        f"MAC_FILE={KEYBOARD_MAC_PATH}; "
        f"LOG_FILE={SCRIPT_DIR}/bt.log; "
        f"CONF_STAMP={SCRIPT_DIR}/.bluez-inputconf-v1; "
        f". {LIB_REMOTE_PATH}; "
        f"pw_init_adapter {int(timeout)}"
    )
    _, _, code = t.run(f"/bin/sh -c '{script}'", timeout=timeout + 60)
    return code == 0


def ensure_adapter_ready(t: Transport, timeout=30, gate_wifi=False):
    log.info("BT adapter ready (timeout=%ss)", timeout)
    if _btlib_init(t, timeout=min(30, timeout)):
        if gate_wifi:
            _wifi_gate(t, True)
        return True

    t.run(
        "lsmod | grep -q '^btnxpuart' || modprobe btnxpuart 2>/dev/null; "
        "lsmod | grep -q '^uhid' || modprobe uhid 2>/dev/null; "
        "modprobe bluetooth 2>/dev/null; "
        "systemctl is-active bluetooth >/dev/null || systemctl start bluetooth; true",
        timeout=20,
    )
    _runtime_pm_on(t)
    if gate_wifi:
        _wifi_gate(t, True)

    t.run(
        "bluetoothctl scan off >/dev/null 2>&1 || true; "
        "bluetoothctl power on >/dev/null 2>&1 || true; "
        "bluetoothctl pairable on >/dev/null 2>&1 || true; true",
        timeout=15,
    )

    deadline = time.monotonic() + timeout
    last, ps_done = "", False
    while time.monotonic() < deadline:
        out, _, _ = t.run("bluetoothctl show 2>&1", timeout=10)
        last = out or ""
        if "No default controller" in last:
            t.run(
                "lsmod | grep -q '^btnxpuart' || modprobe btnxpuart 2>/dev/null; "
                "systemctl start bluetooth 2>/dev/null; true",
                timeout=15,
            )
            time.sleep(2)
            continue
        if any("Powered:" in ln and "yes" in ln.lower() for ln in last.splitlines()):
            t.run("bluetoothctl pairable on >/dev/null 2>&1 || true", timeout=8)
            if not ps_done:
                try:
                    disable_nxp_autosleep(t)
                except Exception as e:
                    log.warning("NXP PS disable: %s", e)
                ps_done = True
            return True
        t.run("bluetoothctl power on >/dev/null 2>&1 || true", timeout=8)
        time.sleep(2)

    raise RuntimeError(
        f"Bluetooth adapter not ready.\n\n{_NXP_HINT}\n\nLast: {(last or '')[:240]}"
    )


def verify_device_state(t: Transport, cfg):
    state = {
        "service_installed": False,
        # Distinct facts for status health (PaperHid):
        # service_present = unit/script files exist; service_active = is-active.
        "service_present": False,
        "service_active": False,
        "service_failed": False,
        "keyboard_paired": False,
        "keyboard_connected": False,
        "bt_powered": False,
        "controller_ready": False,
        "discovering": False,
        "pairable": False,
        "keyboard_mac": "",
        "keyboard_name": "",
        "radio_scan_ok": None,
    }
    try:
        out, _, code = t.run(f"systemctl is-active {SERVICE_NAME} 2>/dev/null", timeout=5)
        active_token = (out or "").strip().splitlines()
        active_token = active_token[0].strip().lower() if active_token else ""
        state["service_active"] = code == 0 and active_token == "active"
    except Exception:
        pass
    try:
        out, _, _ = t.run(
            f"systemctl is-failed {SERVICE_NAME} 2>/dev/null || echo unknown",
            timeout=5,
        )
        failed_token = (out or "").strip().splitlines()
        failed_token = failed_token[0].strip().lower() if failed_token else ""
        state["service_failed"] = failed_token == "failed"
    except Exception:
        pass
    try:
        # Home script, /usr unit, and volatile /etc unit all count as present.
        _, _, code = t.run(
            f"test -f {SCRIPT_REMOTE_PATH} -o -f {SERVICE_PERSISTENT_PATH} "
            f"-o -f {SERVICE_VOLATILE_PATH}",
            timeout=5,
        )
        state["service_present"] = code == 0 or state["service_active"]
    except Exception:
        state["service_present"] = state["service_active"]
    # Back-compat for GUI / older callers: "installed" means files or active.
    state["service_installed"] = state["service_present"] or state["service_active"]

    try:
        out, _, _ = t.run("bluetoothctl show", timeout=5)
        for line in out.splitlines():
            low = line.lower()
            if "powered:" in low:
                state["bt_powered"] = "yes" in low
            elif "pairable:" in low:
                state["pairable"] = "yes" in low
            elif "discovering:" in low:
                state["discovering"] = "yes" in low
        state["controller_ready"] = state["bt_powered"]
    except Exception:
        pass

    mac = ""
    try:
        out, _, code = t.run(f"cat {KEYBOARD_MAC_PATH} 2>/dev/null", timeout=5)
        if code == 0:
            mac = out.strip()
    except Exception:
        pass
    mac = mac or (cfg or {}).get("keyboard_mac", "")
    if not mac:
        return state

    state["keyboard_mac"] = mac
    try:
        out, _, _ = t.run("bluetoothctl devices Paired", timeout=5)
        for line in out.strip().splitlines():
            m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
            if m and m.group(1).lower() == mac.lower():
                state["keyboard_paired"] = True
                state["keyboard_name"] = m.group(2).strip()
                break
    except Exception:
        pass

    if state["keyboard_paired"]:
        try:
            out, _, _ = t.run(f"bluetoothctl info {mac}", timeout=5)
            state["keyboard_connected"] = any(
                "Connected:" in ln and "yes" in ln.lower() for ln in out.splitlines()
            )
        except Exception:
            pass

    if not state["keyboard_name"]:
        state["keyboard_name"] = (cfg or {}).get("keyboard_name", "") or mac
    return state


def _clear_discovery(t: Transport):
    t.run(
        "bluetoothctl scan off >/dev/null 2>&1 || true; "
        "busctl call org.bluez /org/bluez/hci0 org.bluez.Adapter1 StopDiscovery "
        "2>/dev/null || true; true",
        timeout=12,
    )
    time.sleep(0.5)


def _scan_window(t: Transport, seconds: int):
    try:
        out, err, _ = t.run(
            f"bluetoothctl --timeout {seconds} scan on 2>&1 || true",
            timeout=seconds + 10,
        )
        return (out or "") + (err or "")
    except TimeoutError:
        return "scan-timeout"
    finally:
        _clear_discovery(t)


def _scan_hard_fail(text, before, after):
    return (
        after > before
        or "Failed to start discovery" in text
        or ("InProgress" in text and "Failed" in text)
        or "No default controller" in text
    )


def _scan_broken(scan_out="", before=0, after=0):
    return RuntimeError(
        f"{_NXP_SCAN_BROKEN}\n\n"
        f"Technical: {(scan_out or '').strip()[:120] or 'scan failed'}; "
        f"HCI timeout count {before}->{after}"
    )


def _stuck(t: Transport | None = None, extra=""):
    parts = [_NXP_HINT]
    if t is not None:
        try:
            parts.append(f"HCI LE scan timeout count in dmesg: {_hci_fail_count(t)}")
        except Exception:
            pass
    if extra:
        parts.append(extra.rstrip())
    return "\n".join(parts)


def probe_radio_scan_health(t: Transport):
    _clear_discovery(t)
    before = _hci_fail_count(t)
    text = _scan_window(t, 4)
    after = _hci_fail_count(t)
    if "InProgress" in text and "Failed" in text:
        return False, "BlueZ StartDiscovery InProgress/desync after HCI scan failure"
    if after > before:
        return False, f"HCI LE scan opcodes timed out (count {before}->{after})"
    if "No default controller" in text:
        return False, "No default controller"
    return True, "scan command accepted"


def scan_devices(t: Transport, timeout=15, gate_wifi=True):
    log.info("scan_devices timeout=%ss", timeout)
    gated = False
    try:
        ensure_adapter_ready(t, timeout=min(18, max(10, timeout)), gate_wifi=False)
        if gate_wifi:
            _wifi_gate(t, True)
            gated = True
            time.sleep(0.4)

        _clear_discovery(t)
        _runtime_pm_on(t)
        before = _hci_fail_count(t)

        probe = _scan_window(t, 3)
        after_probe = _hci_fail_count(t)
        if _scan_hard_fail(probe, before, after_probe):
            cached = _parse_device_lines(t.run("bluetoothctl devices 2>&1", timeout=8)[0])
            if cached:
                log.info("returning %s cached device(s)", len(cached))
                return cached
            raise _scan_broken(probe, before, after_probe)

        remaining = max(5, int(timeout) - 3)
        scan_out = _scan_window(t, remaining)
        out, _, _ = t.run("bluetoothctl devices 2>&1", timeout=10)
        devices = _parse_device_lines(out)
        after = _hci_fail_count(t)

        if not devices and (
            after > before
            or "Failed to start discovery" in scan_out
            or "InProgress" in scan_out
        ):
            raise _scan_broken(scan_out, before, after)

        log.info("scan found %s device(s)", len(devices))
        return devices
    finally:
        if gated:
            _wifi_gate(t, False)


def pair(t: Transport, mac):
    mac = normalize_mac(mac)
    out, err, code = t.run(f"bluetoothctl pair {mac}", timeout=30)
    if code != 0 and "alreadyexists" not in (out + err).lower().replace(" ", ""):
        raise RuntimeError(f"Pair failed: {err or out}")
    return out


def trust(t: Transport, mac):
    mac = normalize_mac(mac)
    out, err, code = t.run(f"bluetoothctl trust {mac}", timeout=10)
    if code != 0:
        raise RuntimeError(f"Trust failed: {err or out}")
    return out


def remove(t: Transport, mac):
    mac = normalize_mac(mac)
    t.run(f"bluetoothctl remove {mac}", timeout=10)


def connect(t: Transport, mac):
    mac = normalize_mac(mac)
    out, err, code = t.run(f"bluetoothctl connect {mac}", timeout=20)
    if code != 0:
        raise RuntimeError(f"Connect failed: {err or out}")
    return out


def get_connection_status(t: Transport, mac):
    mac = normalize_mac(mac)
    out, _, _ = t.run(f"bluetoothctl info {mac}", timeout=10)
    return any("Connected:" in ln and "yes" in ln.lower() for ln in out.splitlines())


def get_device_name(t: Transport, mac):
    mac = normalize_mac(mac)
    try:
        out, _, _ = t.run(f"bluetoothctl info {mac}", timeout=5)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                return line[len("Name:"):].strip()
    except Exception:
        pass
    return ""


def device_known(t: Transport, mac):
    mac = normalize_mac(mac)
    out, _, _ = t.run(f"bluetoothctl info {mac} 2>&1", timeout=10)
    if not out or "not available" in out.lower() or "no default controller" in out.lower():
        return False
    return "Device " in out or "Name:" in out or "Alias:" in out


def _strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def pair_interactive(t: Transport, mac, passkey_callback=None, timeout=60):
    mac = normalize_mac(mac)
    log.info("pair_interactive %s", mac)
    session = t.open_pty("bluetoothctl")
    transcript = []
    try:
        boot = session.read(2)
        if boot:
            transcript.append(boot)

        session.write_line("agent on")
        transcript.append(session.read(1))
        session.write_line("default-agent")
        transcript.append(session.read(1))
        session.write_line(f"pair {mac}")

        deadline = time.monotonic() + timeout
        passkey_sent = False
        last_log = time.monotonic()
        hci_before = _hci_fail_count(t)

        while time.monotonic() < deadline:
            output = session.read(2)
            if not output:
                now = time.monotonic()
                if now - last_log >= 10:
                    log.info("still pairing %s (%ss left)", mac, int(deadline - now))
                    last_log = now
                    try:
                        if _hci_fail_count(t) > hci_before + 2:
                            log.warning("HCI timeouts rising during pair")
                    except Exception:
                        pass
                continue

            transcript.append(output)
            clean = _strip_ansi(output)
            lower = clean.lower()

            if "alreadyexists" in lower.replace(" ", ""):
                return True

            m = re.search(r"confirm passkey\s+(\d+)\s+\(yes/no\)", lower)
            if m:
                if passkey_callback and not passkey_sent:
                    passkey_callback(m.group(1))
                    passkey_sent = True
                session.write_line("yes")
                continue

            m = re.search(r"(?:pin code|passkey):\s*(\d+)", lower)
            if m:
                if passkey_callback and not passkey_sent:
                    passkey_callback(m.group(1))
                    passkey_sent = True
                continue

            if "enter pin code" in lower:
                if passkey_callback and not passkey_sent:
                    passkey_callback("0000")
                    passkey_sent = True
                session.write_line("0000")
                continue

            if "pairing successful" in lower or "paired: yes" in lower:
                return True

            compact = lower.replace(" ", "")
            errors = {
                "not available": (
                    f"Device {mac} not available. Pairing mode + disconnect from PC, "
                    f"then scan again.\n\n{_stuck(t)}"
                ),
                "authenticationfailed": (
                    "Authentication failed — keyboard may need a PIN that "
                    "couldn't be entered automatically"
                ),
                "authenticationcanceled": "Pairing was canceled",
                "authenticationrejected": "Pairing was rejected by the keyboard",
                "connectionrefused": "Connection refused by the keyboard",
            }
            for needle, msg in errors.items():
                if needle in compact or needle in lower:
                    raise RuntimeError(msg)
            if "failed to pair" in lower:
                raise RuntimeError(f"Pairing failed: {clean.strip()[:200]}")

        blob = _strip_ansi("".join(transcript)).replace("\n", " | ")
        raise RuntimeError(
            f"Pairing timed out after {timeout}s.\n\n"
            + _stuck(t, extra=f"Last: {blob[:280]}" if blob.strip() else "")
        )
    finally:
        session.close()


def pair_and_connect(
    t: Transport, mac, old_mac=None, passkey_callback=None, pre_scan=True
):
    mac = normalize_mac(mac)
    log.info("pair_and_connect %s", mac)
    if old_mac:
        old_norm = normalize_mac(old_mac)
        if old_norm != mac:
            remove(t, old_norm)

    ensure_adapter_ready(t, timeout=25, gate_wifi=False)

    try:
        ok, detail = probe_radio_scan_health(t)
    except Exception as e:
        ok, detail = True, f"probe skipped: {e}"
    if not ok:
        if not device_known(t, mac):
            raise RuntimeError(
                f"Paper Pro Bluetooth radio unhealthy — pairing blocked.\n\n"
                f"Probe: {detail}\n\n{_stuck(t)}"
            )
        log.warning("radio unhealthy but %s known — trying pair", mac)

    if pre_scan and not device_known(t, mac):
        try:
            devices = scan_devices(t, timeout=14, gate_wifi=True)
        except RuntimeError:
            if not device_known(t, mac):
                raise
            devices = []
        if not device_known(t, mac):
            names = ", ".join(f"{d['name']} ({d['mac']})" for d in devices[:8]) or "(none)"
            raise RuntimeError(
                f"Keyboard {mac} not discovered. Seen: {names}.\n"
                f"Pairing mode + disconnect from Windows, then retry.\n\n{_stuck(t)}"
            )

    pair_interactive(t, mac, passkey_callback=passkey_callback, timeout=45)
    trust(t, mac)
    try:
        connect(t, mac)
    except RuntimeError as e:
        log.warning("connect failed (%s) — retry", e)
        time.sleep(2)
        connect(t, mac)
    if not get_connection_status(t, mac):
        raise RuntimeError(f"Paired but not connected.\n\n{_stuck(t)}")
    log.info("pair_and_connect OK: %s", mac)


def read_device_keyboard(t: Transport):
    saved_mac = ""
    try:
        out, _, code = t.run(f"cat {KEYBOARD_MAC_PATH} 2>/dev/null", timeout=5)
        if code == 0:
            saved_mac = (out or "").strip()
    except Exception:
        pass

    paired = None
    try:
        out, err, code = t.run("bluetoothctl devices Paired", timeout=5)
        if code == 0 and "No default controller" not in (out + err):
            paired = []
            for line in out.strip().splitlines():
                m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
                if m:
                    paired.append((m.group(1), m.group(2).strip()))
    except Exception:
        paired = None

    if saved_mac:
        if paired is None:
            return saved_mac, ""
        for mac, name in paired:
            if mac.lower() == saved_mac.lower():
                return saved_mac, name
        t.run(f"rm -f {KEYBOARD_MAC_PATH}", timeout=5)

    try:
        out, _, code = t.run("bluetoothctl devices Connected", timeout=5)
        if code == 0:
            for line in out.strip().splitlines():
                m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line.strip())
                if not m:
                    continue
                mac, name = m.group(1), m.group(2).strip()
                try:
                    mac = normalize_mac(mac)
                except ValueError:
                    continue
                info, _, _ = t.run(f"bluetoothctl info {mac}", timeout=5)
                if "input-keyboard" in info or "00001812" in info.lower():
                    t.write_text(KEYBOARD_MAC_PATH, mac + "\n")
                    return mac, name
    except Exception:
        pass
    return "", ""


def reconnect_now(t: Transport, mac=None):
    ensure_adapter_ready(t, timeout=25, gate_wifi=False)
    if not mac:
        try:
            out, _, code = t.run(f"cat {KEYBOARD_MAC_PATH} 2>/dev/null", timeout=5)
            mac = (out or "").strip() if code == 0 else ""
        except Exception:
            mac = ""
    if not mac:
        raise RuntimeError("No keyboard MAC saved")
    mac = normalize_mac(mac)
    if get_connection_status(t, mac):
        return {"mac": mac, "connected": True}
    t.run(
        "(echo scan on; sleep 4; echo scan off) | bluetoothctl >/dev/null 2>&1",
        timeout=12,
    )
    try:
        connect(t, mac)
    except RuntimeError:
        pass
    if not get_connection_status(t, mac):
        raise RuntimeError("Reconnect failed — wake the keyboard and try again")
    return {"mac": mac, "connected": True}


# Alias used by native path
prepare_adapter = ensure_adapter_ready
