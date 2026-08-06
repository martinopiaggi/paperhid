import os
import time

from core import layout_patcher
from core.logutil import get_logger
from shared.constants import (
    BOOTSTRAP_ENABLE_PATH,
    BOOTSTRAP_HOME_PATH,
    BOOTSTRAP_NAME,
    BOOTSTRAP_PERSISTENT_PATH,
    BOOTSTRAP_SCRIPT_NAME,
    BOOTSTRAP_SCRIPT_REMOTE,
    BOOTSTRAP_VOLATILE_PATH,
    ENABLE_SYMLINK_DIR,
    ENABLE_SYMLINK_PATH,
    KEYBOARD_MAC_PATH,
    LEGACY_HOME_DIRS,
    LEGACY_KEYBOARD_MAC_PATHS,
    LIB_REMOTE_PATH,
    LIB_SCRIPT_NAME,
    RESUME_REMOTE_PATH,
    RESUME_SCRIPT_NAME,
    SCRIPT_DIR,
    SCRIPT_NAME,
    SCRIPT_REMOTE_PATH,
    SERVICE_NAME,
    SERVICE_PERSISTENT_PATH,
    SERVICE_VOLATILE_PATH,
    SLEEP_HOOK_DIR,
    SLEEP_HOOK_HOME_PATH,
    SLEEP_HOOK_NAME,
    SLEEP_HOOK_PATH,
    UNIT_HOME_PATH,
)

log = get_logger("service_installer")

_HOME_SCRIPTS = (
    SCRIPT_REMOTE_PATH,
    LIB_REMOTE_PATH,
    RESUME_REMOTE_PATH,
    BOOTSTRAP_SCRIPT_REMOTE,
    SLEEP_HOOK_HOME_PATH,
)

_MIN_ROOT_FREE_KB = 1024


def _resources_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def _read_resource(name):
    path = os.path.join(_resources_dir(), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _remount_rw(ssh):
    ssh.exec("mount -o remount,rw /", timeout=10)


def _remount_ro(ssh):
    ssh.exec("sync", timeout=10)
    ssh.exec("mount -o remount,ro /", timeout=10)


def _root_free_kb(ssh):
    """Available KB on / (0 if unreadable). BusyBox may wrap device names."""
    out, _, _ = ssh.exec("df -k / 2>/dev/null", timeout=5)
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    for ln in lines[1:]:
        parts = ln.split()
        if parts and parts[-1] == "/" and len(parts) >= 4:
            if str(parts[-3]).isdigit():
                return int(parts[-3])
        nums = [p for p in parts if p.isdigit()]
        if len(nums) >= 3:
            return int(nums[2])
    return 0


def _upload_home_scripts(ssh):
    """Push all home-canonical scripts from host resources."""
    ssh.exec(f"mkdir -p {SCRIPT_DIR}", timeout=10)

    mapping = (
        (SCRIPT_NAME, SCRIPT_REMOTE_PATH),
        (LIB_SCRIPT_NAME, LIB_REMOTE_PATH),
        (RESUME_SCRIPT_NAME, RESUME_REMOTE_PATH),
        (BOOTSTRAP_SCRIPT_NAME, BOOTSTRAP_SCRIPT_REMOTE),
        (SLEEP_HOOK_NAME, SLEEP_HOOK_HOME_PATH),
    )
    for name, remote in mapping:
        ssh.upload_string(_read_resource(name), remote)

    for path in _HOME_SCRIPTS:
        ssh.exec(f"chmod +x {path}", timeout=5)

    service_content = _read_resource(SERVICE_NAME)
    bootstrap_unit = _read_resource(BOOTSTRAP_NAME)
    ssh.upload_string(service_content, UNIT_HOME_PATH)
    ssh.upload_string(bootstrap_unit, BOOTSTRAP_HOME_PATH)
    return service_content, bootstrap_unit


def _seed_volatile(ssh, service_content, bootstrap_unit, force=False):
    """Write /etc units. Returns True if anything was written."""
    wrote = False
    for content, path in (
        (service_content, SERVICE_VOLATILE_PATH),
        (bootstrap_unit, BOOTSTRAP_VOLATILE_PATH),
    ):
        if not force:
            _, _, code = ssh.exec(f"test -f {path}", timeout=5)
            if code == 0:
                continue
        ssh.upload_string(content, path)
        wrote = True
    return wrote


def _seed_persistent(ssh, service_content, bootstrap_unit):
    """Write units + sleep hook to /usr. Raises on hard failure after remount."""
    free = _root_free_kb(ssh)
    if free < _MIN_ROOT_FREE_KB:
        raise RuntimeError(
            f"Root filesystem has only {free} KB free "
            f"(need ≥ {_MIN_ROOT_FREE_KB} KB). Free space, then retry."
        )

    _remount_rw(ssh)
    try:
        ssh.upload_string(service_content, SERVICE_PERSISTENT_PATH)
        ssh.upload_string(bootstrap_unit, BOOTSTRAP_PERSISTENT_PATH)
        ssh.exec(f"mkdir -p {ENABLE_SYMLINK_DIR}", timeout=5)
        ssh.exec(
            f"ln -sf {SERVICE_PERSISTENT_PATH} {ENABLE_SYMLINK_PATH}", timeout=5
        )
        ssh.exec(
            f"ln -sf {BOOTSTRAP_PERSISTENT_PATH} {BOOTSTRAP_ENABLE_PATH}",
            timeout=5,
        )

        hook = _read_resource(SLEEP_HOOK_NAME)
        ssh.exec(f"mkdir -p {SLEEP_HOOK_DIR}", timeout=5)
        ssh.upload_string(hook, SLEEP_HOOK_PATH)
        ssh.exec(f"chmod 755 {SLEEP_HOOK_PATH}", timeout=5)
    finally:
        _remount_ro(ssh)


def _persistent_missing(ssh):
    _, _, svc = ssh.exec(f"test -f {SERVICE_PERSISTENT_PATH}", timeout=5)
    _, _, boot = ssh.exec(f"test -f {BOOTSTRAP_PERSISTENT_PATH}", timeout=5)
    _, _, hook = ssh.exec(f"test -x {SLEEP_HOOK_PATH}", timeout=5)
    return svc != 0 or boot != 0 or hook != 0


def _service_inactive(ssh):
    _, _, code = ssh.exec(f"systemctl is-active {SERVICE_NAME}", timeout=5)
    return code != 0


def _enable_units(ssh):
    ssh.exec(f"systemctl enable {SERVICE_NAME} {BOOTSTRAP_NAME}", timeout=15)


def _start_service(ssh):
    out, err, code = ssh.exec(f"systemctl start {SERVICE_NAME}", timeout=45)
    if code != 0:
        ssh.exec("systemctl daemon-reload", timeout=15)
        out, err, code = ssh.exec(f"systemctl start {SERVICE_NAME}", timeout=45)
        if code != 0:
            raise RuntimeError(f"Failed to start service: {err or out}")


def _verify_install(ssh):
    """Confirm units are known to systemd and the main service is active."""
    problems = []

    for path, label in (
        (SCRIPT_REMOTE_PATH, "bt-keyboard.sh missing"),
        (LIB_REMOTE_PATH, "bt-lib.sh missing"),
        (RESUME_REMOTE_PATH, "bt-resume.sh missing"),
    ):
        test = "-x" if path != LIB_REMOTE_PATH else "-f"
        _, _, code = ssh.exec(f"test {test} {path}", timeout=5)
        if code != 0:
            problems.append(label)

    out, _, _ = ssh.exec(
        f"systemctl is-enabled {SERVICE_NAME} 2>&1; "
        f"systemctl is-enabled {BOOTSTRAP_NAME} 2>&1; "
        f"systemctl is-active {SERVICE_NAME} 2>&1",
        timeout=10,
    )
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    svc_en = lines[0] if len(lines) > 0 else ""
    boot_en = lines[1] if len(lines) > 1 else ""
    svc_act = lines[2] if len(lines) > 2 else ""

    ok_enabled = ("enabled", "enabled-runtime", "static", "indirect")
    if svc_en not in ok_enabled:
        problems.append(f"keyboard unit not enabled ({svc_en or 'unknown'})")
    if boot_en not in ok_enabled:
        problems.append(f"bootstrap unit not enabled ({boot_en or 'unknown'})")
    if svc_act != "active":
        problems.append(f"keyboard service not active ({svc_act or 'unknown'})")

    _, _, hook_code = ssh.exec(f"test -x {SLEEP_HOOK_PATH}", timeout=5)
    _, _, usr_code = ssh.exec(f"test -f {SERVICE_PERSISTENT_PATH}", timeout=5)

    if problems:
        raise RuntimeError("Service install incomplete: " + "; ".join(problems))

    notes = []
    if usr_code != 0:
        notes.append("/usr unit missing (survives this boot via /etc only)")
    if hook_code != 0:
        notes.append("resume sleep-hook missing (reconnect after sleep may need loop)")
    if notes:
        log.warning("install verify notes: %s", "; ".join(notes))
        return "active (" + "; ".join(notes) + ")"
    return "active"


def sync_to_device(ssh, force=False):
    """Upload scripts/units and ensure the service is enabled.

    force=True  — full install: always re-seed /etc+/usr, start, verify.
                  Returns status string.
    force=False — connect-time refresh: re-seed missing pieces only,
                  start only if inactive. Returns True if service was started.
    """
    service_content, bootstrap_unit = _upload_home_scripts(ssh)
    need_reload = _seed_volatile(ssh, service_content, bootstrap_unit, force=force)

    if force or _persistent_missing(ssh):
        try:
            _seed_persistent(ssh, service_content, bootstrap_unit)
            need_reload = True
        except RuntimeError:
            if force:
                raise
            log.warning("sync_to_device: /usr seed failed (low space?)")
        except Exception as e:
            log.warning("sync_to_device: /usr seed failed: %s", e)

    if need_reload:
        ssh.exec("systemctl daemon-reload", timeout=15)

    _enable_units(ssh)

    if force or _service_inactive(ssh):
        _start_service(ssh)
        status = _verify_install(ssh)
        return status if force else True

    return "active" if force else False


def install(ssh):
    """Install script + units to home (canonical) and seed /usr + /etc."""
    log.info("installing PaperHid BT service")
    status = sync_to_device(ssh, force=True)
    log.info("install ok: %s", status)
    return status


def ensure_installed(ssh):
    """Re-seed units/scripts if missing after overlay reset; start if inactive.

    Safe to call on every connect. Does not restart bluetooth aggressively.
    """
    _, _, code = ssh.exec(f"test -x {SCRIPT_REMOTE_PATH}", timeout=5)
    if code != 0:
        install(ssh)
        return True
    return bool(sync_to_device(ssh, force=False))


def uninstall(ssh):
    layout_patcher.restore_original(ssh)

    legacy_bootstrap = "movewriter-bt-bootstrap.service"
    for unit in (SERVICE_NAME, BOOTSTRAP_NAME, legacy_bootstrap):
        ssh.exec(f"systemctl stop {unit}", timeout=15)
        ssh.exec(f"systemctl disable {unit}", timeout=10)
    ssh.exec(
        "systemctl stop paperwriter-bt-resume.service 2>/dev/null; "
        "rm -f /run/paperwriter-bt-resume.lock 2>/dev/null; true",
        timeout=10,
    )

    try:
        _remount_rw(ssh)
        try:
            ssh.exec(f"rm -f {SERVICE_PERSISTENT_PATH}", timeout=5)
            ssh.exec(f"rm -f {ENABLE_SYMLINK_PATH}", timeout=5)
            ssh.exec(f"rm -f {BOOTSTRAP_PERSISTENT_PATH}", timeout=5)
            ssh.exec(f"rm -f {BOOTSTRAP_ENABLE_PATH}", timeout=5)
            ssh.exec(f"rm -f {SLEEP_HOOK_PATH}", timeout=5)
            ssh.exec(
                f"rm -f /usr/lib/systemd/system/{legacy_bootstrap} "
                f"{ENABLE_SYMLINK_DIR}/{legacy_bootstrap}",
                timeout=5,
            )
        finally:
            _remount_ro(ssh)
    except Exception:
        pass

    ssh.exec(f"rm -f {SERVICE_VOLATILE_PATH}", timeout=5)
    ssh.exec(f"rm -f {BOOTSTRAP_VOLATILE_PATH}", timeout=5)
    ssh.exec(f"rm -f /etc/systemd/system/{legacy_bootstrap}", timeout=5)

    ssh.exec(
        "echo paperwriter.bt >> /sys/power/wake_unlock 2>/dev/null; "
        "echo user.lock >> /sys/power/wake_unlock 2>/dev/null; true",
        timeout=5,
    )

    legacy_homes = " ".join(LEGACY_HOME_DIRS)
    legacy_macs = " ".join(LEGACY_KEYBOARD_MAC_PATHS)
    ssh.exec(f"rm -rf {SCRIPT_DIR} {legacy_homes}", timeout=10)
    ssh.exec(f"rm -f {KEYBOARD_MAC_PATH} {legacy_macs}", timeout=5)

    ssh.exec("systemctl daemon-reload", timeout=10)


def save_keyboard_mac(ssh, mac):
    """Write keyboard MAC to device so the service can auto-reconnect on boot."""
    from shared.bluetooth import normalize_mac

    ssh.upload_string(normalize_mac(mac), KEYBOARD_MAC_PATH)


def wait_for_controller(ssh, timeout=40):
    """Wait until bluetoothctl sees a powered controller."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            ssh.exec(
                "lsmod | grep -q '^btnxpuart' || modprobe btnxpuart 2>/dev/null; "
                "lsmod | grep -q '^uhid' || modprobe uhid 2>/dev/null; "
                "systemctl is-active bluetooth >/dev/null || systemctl start bluetooth 2>/dev/null; "
                "true",
                timeout=15,
            )
            out, _, _ = ssh.exec("bluetoothctl show 2>&1", timeout=10)
            last = out or ""
            powered = any(
                "Powered:" in ln and "yes" in ln.lower() for ln in last.splitlines()
            )
            if powered:
                ssh.exec("bluetoothctl pairable on 2>/dev/null || true", timeout=8)
                return True
            ssh.exec("bluetoothctl power on 2>/dev/null || true", timeout=8)
        except Exception as e:
            last = str(e)
        time.sleep(2)

    raise RuntimeError(
        "Bluetooth adapter not ready after "
        f"{timeout}s. Reboot the tablet, wait ~30s after UI is up, then retry. "
        f"Last show output: {(last or '')[:300]}"
    )
