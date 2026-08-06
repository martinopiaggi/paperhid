"""Service installer for PaperHid on-device app."""
import os
import subprocess
from pathlib import Path

from backend import layout_patcher
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

_HOME_SCRIPTS = (
    SCRIPT_REMOTE_PATH,
    LIB_REMOTE_PATH,
    RESUME_REMOTE_PATH,
    BOOTSTRAP_SCRIPT_REMOTE,
    SLEEP_HOOK_HOME_PATH,
)


def _run(cmd, timeout=10):
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.stdout, result.stderr, result.returncode


def _resources_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def _read_resource(name):
    path = os.path.join(_resources_dir(), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _write_home_scripts():
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    mapping = (
        (SCRIPT_NAME, SCRIPT_REMOTE_PATH),
        (LIB_SCRIPT_NAME, LIB_REMOTE_PATH),
        (RESUME_SCRIPT_NAME, RESUME_REMOTE_PATH),
        (BOOTSTRAP_SCRIPT_NAME, BOOTSTRAP_SCRIPT_REMOTE),
        (SLEEP_HOOK_NAME, SLEEP_HOOK_HOME_PATH),
        (SERVICE_NAME, UNIT_HOME_PATH),
        (BOOTSTRAP_NAME, BOOTSTRAP_HOME_PATH),
    )
    for name, dest in mapping:
        Path(dest).write_text(_read_resource(name))
    for path in _HOME_SCRIPTS:
        os.chmod(path, 0o755)


def install():
    _write_home_scripts()

    service_content = _read_resource(SERVICE_NAME)
    bootstrap_unit = _read_resource(BOOTSTRAP_NAME)
    sleep_hook = _read_resource(SLEEP_HOOK_NAME)

    Path(SERVICE_VOLATILE_PATH).write_text(service_content)
    Path(BOOTSTRAP_VOLATILE_PATH).write_text(bootstrap_unit)

    _run("mount -o remount,rw /", timeout=10)
    try:
        Path(SERVICE_PERSISTENT_PATH).write_text(service_content)
        Path(BOOTSTRAP_PERSISTENT_PATH).write_text(bootstrap_unit)
        os.makedirs(ENABLE_SYMLINK_DIR, exist_ok=True)
        for link, target in (
            (ENABLE_SYMLINK_PATH, SERVICE_PERSISTENT_PATH),
            (BOOTSTRAP_ENABLE_PATH, BOOTSTRAP_PERSISTENT_PATH),
        ):
            try:
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(target, link)
            except OSError:
                pass
        os.makedirs(SLEEP_HOOK_DIR, exist_ok=True)
        Path(SLEEP_HOOK_PATH).write_text(sleep_hook)
        os.chmod(SLEEP_HOOK_PATH, 0o755)
    finally:
        _run("sync", timeout=5)
        _run("mount -o remount,ro /", timeout=10)

    _run("systemctl daemon-reload")
    _run(f"systemctl enable {SERVICE_NAME}", timeout=15)
    _run(f"systemctl enable {BOOTSTRAP_NAME}", timeout=15)
    out, err, code = _run(f"systemctl start {SERVICE_NAME}", timeout=45)
    if code != 0:
        raise RuntimeError(f"Failed to start service: {err or out}")


def uninstall():
    layout_patcher.restore_original()

    for unit in (SERVICE_NAME, BOOTSTRAP_NAME):
        _run(f"systemctl stop {unit}", timeout=15)
        _run(f"systemctl disable {unit}", timeout=10)
    _run(
        "systemctl stop paperwriter-bt-resume.service 2>/dev/null; "
        "rm -f /run/paperwriter-bt-resume.lock; true",
        timeout=10,
    )

    _run("mount -o remount,rw /", timeout=10)
    try:
        for path in (
            SERVICE_PERSISTENT_PATH,
            ENABLE_SYMLINK_PATH,
            BOOTSTRAP_PERSISTENT_PATH,
            BOOTSTRAP_ENABLE_PATH,
            SLEEP_HOOK_PATH,
        ):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    finally:
        _run("sync", timeout=5)
        _run("mount -o remount,ro /", timeout=10)

    for path in (SERVICE_VOLATILE_PATH, BOOTSTRAP_VOLATILE_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    _run(
        "echo paperwriter.bt >> /sys/power/wake_unlock 2>/dev/null; "
        "echo user.lock >> /sys/power/wake_unlock 2>/dev/null; true",
        timeout=5,
    )

    # Keep MAC file and layout backup; only drop scripts/units we own.
    for name in (
        SCRIPT_NAME,
        LIB_SCRIPT_NAME,
        RESUME_SCRIPT_NAME,
        BOOTSTRAP_SCRIPT_NAME,
        SLEEP_HOOK_NAME,
        SERVICE_NAME,
        BOOTSTRAP_NAME,
    ):
        try:
            os.remove(os.path.join(SCRIPT_DIR, name))
        except FileNotFoundError:
            pass

    _run("systemctl daemon-reload", timeout=5)


def save_keyboard_mac(mac):
    from shared.bluetooth import normalize_mac

    Path(KEYBOARD_MAC_PATH).write_text(normalize_mac(mac))


def clear_keyboard_mac():
    try:
        os.remove(KEYBOARD_MAC_PATH)
    except FileNotFoundError:
        pass


def is_active():
    _, _, code = _run(f"systemctl is-active {SERVICE_NAME}", timeout=5)
    return code == 0
