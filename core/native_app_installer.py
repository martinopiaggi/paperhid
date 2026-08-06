"""Install/uninstall PaperHid on-device app via SSH.

Deploys XOVI + AppLoad + app package to Paper Pro/Move, plus a systemd drop-in
that disables xochitl's watchdog during BT ops.
"""
import os
import sys

DEST_DIR = "/home/root/xovi/exthome/appload/paperwriter"
LEGACY_DEST_DIR = "/home/root/xovi/exthome/appload/movewriter"
XOVI_DIR = "/home/root/xovi"
APPLOAD_DIR = "/home/root/xovi/extensions.d/appload"
WATCHDOG_DROPIN_DIR = "/usr/lib/systemd/system/xochitl.service.d"
WATCHDOG_DROPIN_PATH = f"{WATCHDOG_DROPIN_DIR}/zz-paperwriter-overrides.conf"
LEGACY_WATCHDOG_PATH = f"{WATCHDOG_DROPIN_DIR}/zz-movewriter-overrides.conf"
AUTOSTART_SERVICE_PATH = "/usr/lib/systemd/system/paperwriter-xovi.service"
AUTOSTART_SYMLINK_PATH = (
    "/usr/lib/systemd/system/multi-user.target.wants/paperwriter-xovi.service"
)
AUTOSTART_SCRIPT_PATH = "/usr/lib/paperwriter-xovi-autostart.sh"
LEGACY_AUTOSTART_SERVICE = "/usr/lib/systemd/system/movewriter-xovi.service"
LEGACY_AUTOSTART_SCRIPT = "/usr/lib/movewriter-xovi-autostart.sh"
LEGACY_AUTOSTART_SYMLINK = (
    "/usr/lib/systemd/system/multi-user.target.wants/movewriter-xovi.service"
)
EMERGENCY_DROPIN_DIR = "/usr/lib/systemd/system/rm-emergency.service.d"
EMERGENCY_DROPIN_PATH = f"{EMERGENCY_DROPIN_DIR}/zz-paperwriter.conf"
LEGACY_EMERGENCY_PATH = f"{EMERGENCY_DROPIN_DIR}/zz-movewriter.conf"
HOME_STATE_DIR = "/home/root/.paperwriter"
ATTEMPTS_FILE = f"{HOME_STATE_DIR}/xovi-activation-attempts"

VELLUM_ENV = "export PATH=/opt/bin:/opt/sbin:/home/root/.vellum/bin:$PATH; "

APP_FILES = {
    "": ["manifest.json", "resources.rcc"],
    "backend": [
        "entry", "__init__.py", "main.py", "protocol.py", "bluetooth.py",
        "service.py", "layout_patcher.py", "config.py",
    ],
    "shared": [
        "__init__.py", "constants.py", "transport.py",
        "bluetooth.py", "layout_patcher.py", "layouts.py",
    ],
    "qml": [
        "main.qml", "KeyboardSection.qml", "ServiceSection.qml",
        "PasskeyOverlay.qml", "application.qrc",
    ],
    "qml/components": [
        "ActionButton.qml", "Card.qml", "DeviceList.qml", "StatusDot.qml",
    ],
    "tools": ["__init__.py", "generate_qmap.py"],
    "resources": [
        "bt-keyboard.sh", "bt-lib.sh", "bt-resume.sh",
        "paperwriter-bt-bootstrap.sh", "paperwriter-bt-bootstrap.service",
        "remarkable-bt-keyboard.service", "zz-paperwriter-bt.sh",
    ],
}

# Host paths that live outside nativeapp/ (single source of truth).
_HOST_SOURCED = {
    "shared": "shared",
    "tools": "tools",
    "resources": "resources",
}

_FORBIDDEN_UNIT = ("Requires=", "Wants=", "After=", "Before=", "BindsTo=")


def native_app_root():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    in_repo = os.path.join(here, "nativeapp")
    if os.path.isdir(in_repo):
        return in_repo
    if getattr(sys, "_MEIPASS", None):
        bundled = os.path.join(sys._MEIPASS, "nativeapp")
        if os.path.isdir(bundled):
            return bundled
    raise RuntimeError(
        "Cannot locate PaperHid native app source (expected nativeapp/)."
    )


def _resources_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def _with_rw(ssh, fn):
    ssh.exec("mount -o remount,rw /", timeout=5)
    try:
        return fn()
    finally:
        ssh.exec("mount -o remount,ro /", timeout=5)


def _check_unit(content, label):
    for token in _FORBIDDEN_UNIT:
        if token in content:
            raise RuntimeError(f"Refusing {label} containing '{token}'")


def is_installed(ssh):
    try:
        _, _, code = ssh.exec(f"test -d {DEST_DIR}", timeout=5)
        if code == 0:
            return True
        _, _, code = ssh.exec(f"test -d {LEGACY_DEST_DIR}", timeout=5)
        return code == 0
    except Exception:
        return False


def supports_device(ssh):
    try:
        from core import device as device_mod
        return bool(device_mod.detect(ssh).get("supports_native_app"))
    except Exception:
        return False


def install(ssh, status_cb=None):
    def say(msg):
        if status_cb:
            status_cb(msg)

    if not supports_device(ssh):
        raise RuntimeError(
            "Native App (XOVI/AppLoad) install is supported on reMarkable "
            "Paper Pro and Move only."
        )

    root = native_app_root()
    _preflight_space(ssh)

    say("Checking prerequisites...")
    _ensure_entware(ssh, say)
    _ensure_python3(ssh, say)
    _ensure_vellum(ssh, say)

    os_ver = _current_os_version(ssh)
    say(f"Checking AppLoad compatibility with firmware {os_ver or '?'}...")
    _require_appload_for_os(ssh, os_ver, say)

    say("Ensuring XOVI/AppLoad...")
    _ensure_xovi(ssh, say)
    _ensure_appload(ssh, say)
    _vellum_upgrade(ssh, say)
    _vellum_reenable(ssh, say)

    if not _appload_present(ssh):
        raise RuntimeError("AppLoad extension missing after install attempt")

    say("Uploading app files...")
    _upload_app(ssh, root)

    say("Installing crash protection...")
    _install_watchdog_dropin(ssh)
    _install_emergency_override(ssh)

    say("Setting up boot autostart...")
    _install_autostart(ssh)

    _rebuild_hashtable(ssh, say)
    _activate_xovi(ssh, say)
    say("Install complete — open ☰ → AppLoad → PaperHid")


def uninstall(ssh, status_cb=None):
    def say(msg):
        if status_cb:
            status_cb(msg)

    say("Removing app files...")
    ssh.exec(f"rm -rf {DEST_DIR} {LEGACY_DEST_DIR}", timeout=10)
    say("Removing crash protection...")
    _remove_watchdog_dropin(ssh)
    _remove_emergency_override(ssh)
    say("Removing boot autostart...")
    _remove_autostart(ssh)
    say("Restarting interface...")
    ssh.exec("(sleep 1 && systemctl restart xochitl) &", timeout=5)
    say("Uninstall complete")


def _df_avail_kb(ssh, mountpoint):
    out, _, _ = ssh.exec(f"df -k {mountpoint} 2>/dev/null", timeout=5)
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    for ln in lines[1:]:
        parts = ln.split()
        if parts and parts[-1] == mountpoint and len(parts) >= 4:
            for cand in (parts[-3], parts[3] if len(parts) > 3 else None):
                if cand is not None and str(cand).isdigit():
                    return int(cand)
        nums = [p for p in parts if p.isdigit()]
        if len(nums) >= 3:
            return int(nums[2])
    return 0


def _preflight_space(ssh):
    root_free = _df_avail_kb(ssh, "/")
    if root_free < 2048:
        raise RuntimeError(f"Root filesystem has only {root_free} KB free (need ≥ 2 MB).")
    home_free = _df_avail_kb(ssh, "/home")
    if home_free < 80 * 1024:
        raise RuntimeError(
            f"/home has only {home_free // 1024} MB free (need ≥ 80 MB for Python/entware)."
        )


def _xovi_present(ssh):
    _, _, code = ssh.exec(
        f"test -f {XOVI_DIR}/xovi.so && test -x {XOVI_DIR}/start", timeout=5
    )
    return code == 0


def _appload_present(ssh):
    _, _, code = ssh.exec(
        f"test -f {XOVI_DIR}/extensions.d/appload.so || test -d {APPLOAD_DIR}",
        timeout=5,
    )
    return code == 0


def _ensure_opt_mount(ssh):
    ssh.exec(
        "if [ -d /home/root/.entware ]; then "
        "  mount -o remount,rw / 2>/dev/null; mkdir -p /opt; "
        "  mount -o remount,ro / 2>/dev/null; "
        "  mountpoint -q /opt || mount --bind /home/root/.entware /opt; "
        "fi; true",
        timeout=15,
    )


def _opkg_usable(ssh) -> bool:
    """True only when Entware ``opkg`` is present *and* runs.

    A partial failed install can leave an ``opkg`` binary (or empty tree) that
    must not be treated as a working package manager.
    """
    _ensure_opt_mount(ssh)
    _, _, code = ssh.exec(
        "export PATH=/opt/bin:/opt/sbin:$PATH; "
        "test -x /opt/bin/opkg && /opt/bin/opkg --version >/dev/null 2>&1",
        timeout=20,
    )
    return code == 0


def _tablet_python_usable(ssh) -> bool:
    """True only when Entware ``/opt/bin/python3`` executes successfully.

    Presence of a binary is not enough; a half-written Entware tree can leave
    a non-runnable python3 that would break paperpointerd at service start.
    """
    _ensure_opt_mount(ssh)
    _, _, code = ssh.exec(
        "test -x /opt/bin/python3 && "
        "/opt/bin/python3 -c 'import sys; assert sys.version_info[0] >= 3'",
        timeout=20,
    )
    return code == 0


def _partial_entware_debris(ssh) -> bool:
    """Detect leftover Entware paths from a previous incomplete install."""
    _, _, code = ssh.exec(
        "test -d /home/root/.entware || test -f /tmp/rmpp_entware.sh",
        timeout=5,
    )
    return code == 0


def _cleanup_partial_entware(ssh, say):
    """Remove an incomplete Entware tree so a later bootstrap can reinstall.

    Only call when :func:`_opkg_usable` is false — never wipe a working Entware
    just because python3 is missing (``opkg install python3`` can be re-run).
    """
    say("Removing incomplete Entware install so bootstrap can retry cleanly...")
    ssh.exec(
        "set +e; "
        "if mountpoint -q /opt 2>/dev/null; then umount /opt 2>/dev/null; fi; "
        "mount -o remount,rw / 2>/dev/null; "
        "rm -rf /home/root/.entware /tmp/rmpp_entware.sh; "
        'if [ -d /opt ] && [ -z "$(ls -A /opt 2>/dev/null)" ]; then rmdir /opt 2>/dev/null; fi; '
        "mount -o remount,ro / 2>/dev/null; true",
        timeout=90,
    )


def _ensure_entware(ssh, say):
    if _opkg_usable(ssh):
        return
    # Broken leftover tree: presence of opkg path alone used to short-circuit
    # reinstall and leave the device unusable for pointer bootstrap.
    if _partial_entware_debris(ssh):
        _cleanup_partial_entware(ssh, say)
    say("Installing entware (package manager)...")
    ssh.exec(
        "mount -o remount,rw / 2>/dev/null; "
        'if [ -d /opt ] && [ -z "$(ls -A /opt 2>/dev/null)" ]; then rmdir /opt 2>/dev/null; fi; '
        'if [ -d /home/root/.entware ] && [ -z "$(ls -A /home/root/.entware 2>/dev/null)" ]; '
        "then rmdir /home/root/.entware 2>/dev/null; fi; "
        "mount -o remount,ro / 2>/dev/null; true",
        timeout=15,
    )
    out, err, _ = ssh.exec(
        "wget --no-check-certificate -O /tmp/rmpp_entware.sh "
        "https://raw.githubusercontent.com/hmenzagh/rmpp-entware/main/rmpp_entware.sh "
        "&& bash /tmp/rmpp_entware.sh --force",
        timeout=360,
    )
    _ensure_opt_mount(ssh)
    if not _opkg_usable(ssh):
        # Do not leave a half-installed tree that a later preflight might
        # mis-read as "entware present".
        try:
            _cleanup_partial_entware(ssh, say)
        except Exception:
            pass
        raise RuntimeError(
            "entware install failed (opkg not usable after install)\n"
            + ((out or "") + "\n" + (err or ""))[-500:]
        )


def _ensure_python3(ssh, say):
    if _tablet_python_usable(ssh):
        return
    say("Installing Python 3...")
    out, err, _ = ssh.exec(
        "export PATH=/opt/bin:/opt/sbin:$PATH && opkg update && opkg install python3",
        timeout=300,
    )
    if not _tablet_python_usable(ssh):
        raise RuntimeError(
            "Python 3 install failed (/opt/bin/python3 not usable)\n"
            + ((out or "") + "\n" + (err or ""))[-500:]
        )


def ensure_tablet_python(ssh, status_cb=None):
    """Install Entware + Python 3 on the tablet if missing.

    Required by paperpointerd (mouse). Needs free space on /home and tablet
    internet (Wi‑Fi) for the first bootstrap. Safe to re-run.

    Partial Entware trees are never treated as success: ``opkg`` and
    ``/opt/bin/python3`` must both *execute*. Failed Entware installs are
    cleaned up so the next attempt is a clean bootstrap, not a false ready
    state.
    """
    def say(msg):
        if status_cb:
            status_cb(msg)
        else:
            print(msg)

    _preflight_space(ssh)
    try:
        _ensure_entware(ssh, say)
        _ensure_python3(ssh, say)
    except Exception:
        # If Entware itself is broken after failure, clear debris for retry.
        # Leave a working opkg alone when only python install failed.
        try:
            if not _opkg_usable(ssh) and _partial_entware_debris(ssh):
                _cleanup_partial_entware(ssh, say)
        except Exception:
            pass
        raise
    if not _tablet_python_usable(ssh):
        raise RuntimeError(
            "Tablet Python still missing after bootstrap "
            "(/opt/bin/python3). Check tablet internet (Wi‑Fi) and free space."
        )
    say("Tablet Python ready (/opt/bin/python3)")


def _ensure_vellum(ssh, say):
    _, _, code = ssh.exec(
        "command -v vellum || test -x /home/root/.vellum/bin/vellum", timeout=5
    )
    if code == 0:
        return
    say("Installing Vellum...")
    ssh.exec(
        "wget --no-check-certificate -O /tmp/bootstrap.sh "
        "https://github.com/vellum-dev/vellum-cli/releases/latest/download/bootstrap.sh "
        "&& bash /tmp/bootstrap.sh && rm -f /tmp/bootstrap.sh",
        timeout=180,
    )
    _, _, code = ssh.exec(
        "export PATH=/home/root/.vellum/bin:$PATH; command -v vellum", timeout=5
    )
    if code != 0:
        raise RuntimeError("Vellum install failed")


def _ensure_xovi(ssh, say):
    if _xovi_present(ssh):
        return
    say("Installing XOVI...")
    ssh.exec(f"{VELLUM_ENV} vellum add xovi", timeout=300)
    if not _xovi_present(ssh):
        raise RuntimeError("XOVI install failed")


def _ensure_appload(ssh, say):
    if _appload_present(ssh):
        return
    say("Installing AppLoad...")
    ssh.exec(f"{VELLUM_ENV} vellum add appload", timeout=300)
    if not _appload_present(ssh):
        raise RuntimeError("AppLoad install failed")


def _current_os_version(ssh):
    out, _, _ = ssh.exec('. /etc/os-release && echo "$IMG_VERSION"', timeout=5)
    return (out or "").strip()


def _require_appload_for_os(ssh, os_ver, say):
    """Abort if no AppLoad package matches this firmware (avoids xochitl crash-loop)."""
    if not os_ver:
        say("Warning: could not read IMG_VERSION; continuing")
        return

    probe_out, probe_err, probe_code = ssh.exec(
        f"{VELLUM_ENV} vellum add appload 2>&1", timeout=120
    )
    probe = ((probe_out or "") + "\n" + (probe_err or "")).strip()
    low = probe.lower()

    if probe_code == 0 or "ok:" in low or "installed" in low or "up to date" in low:
        say("AppLoad package available for this firmware")
        return

    if "no version" in low and "compatible" in low:
        raise RuntimeError(
            f"No AppLoad package supports firmware {os_ver} yet.\n\n"
            f"On-device UI cannot install safely until AppLoad adds {os_ver}.\n"
            f"Bluetooth keyboard service is unaffected.\n\n"
            f"(Latest published AppLoad requires remarkable-os < 3.28.)"
        )

    try:
        major_minor = tuple(int(x) for x in os_ver.split(".")[:2])
    except ValueError:
        major_minor = (0, 0)
    if major_minor >= (3, 28):
        raise RuntimeError(
            f"Cannot verify AppLoad for firmware {os_ver}.\n"
            f"Refusing install to avoid a UI crash-loop.\n"
            f"Bluetooth keyboard service is unaffected.\n"
            f"Vellum said:\n{probe[-400:]}"
        )
    say("Note: AppLoad compatibility probe inconclusive; continuing")


def _vellum_upgrade(ssh, say):
    say("Updating components for current firmware...")
    ssh.exec(f"{VELLUM_ENV} yes | vellum upgrade", timeout=300)


def _vellum_reenable(ssh, say):
    say("Restoring system modifications...")
    ssh.exec(f"{VELLUM_ENV} yes | vellum reenable", timeout=120)


def _rebuild_hashtable(ssh, say):
    out, _, code = ssh.exec(
        "test -s /home/root/xovi/exthome/qt-resource-rebuilder/hashtab && echo HAS",
        timeout=5,
    )
    if code == 0 and "HAS" in (out or ""):
        say("Interface resources already built — skipping rebuild")
        return

    bounded = (
        'HT=/home/root/xovi/exthome/qt-resource-rebuilder/hashtab; '
        'rm -f "$HT"; '
        '( echo "" | bash /home/root/xovi/rebuild_hashtable >/tmp/pw_rb.log 2>&1 ) & '
        'i=0; while [ $i -lt 25 ]; do sleep 4; '
        'if [ -f "$HT" ] && grep -q "Hashtab saved" /tmp/pw_rb.log 2>/dev/null; then break; fi; '
        'i=$((i+1)); done; '
        'pkill -9 -f rebuild_hashtable 2>/dev/null; kill -9 $(pidof xochitl) 2>/dev/null; sleep 1; '
        'if [ -f "$HT" ] && grep -q "Hashtab saved" /tmp/pw_rb.log 2>/dev/null; '
        'then echo PW_OK; else echo PW_FAIL; cat /tmp/pw_rb.log 2>/dev/null | tail -n 20; fi'
    )
    for n in (1, 2):
        say(
            "Rebuilding interface resources (~1-2 min; screen will flicker)"
            + ("..." if n == 1 else " — retrying...")
        )
        out, _, _ = ssh.exec(bounded, timeout=150)
        if "PW_OK" in (out or ""):
            return
    ssh.exec("systemctl start xochitl.service", timeout=20)
    raise RuntimeError(
        "Couldn't rebuild interface resources — xochitl hung during rebuild. "
        "Stopped before activation. Bluetooth keyboard service is unaffected."
    )


def _activate_xovi(ssh, say):
    say("Activating PaperHid...")
    ssh.exec(f"mkdir -p {HOME_STATE_DIR}", timeout=5)
    ssh.exec(f"rm -f {ATTEMPTS_FILE} 2>/dev/null || true", timeout=5)
    ssh.exec(f"bash {AUTOSTART_SCRIPT_PATH}", timeout=90)
    out, _, _ = ssh.exec(
        'sleep 5; XPID=$(pidof xochitl); '
        'if [ -n "$XPID" ] && tr "\\0" "\\n" </proc/$XPID/environ 2>/dev/null | grep -q xovi.so; '
        'then echo PW_XOVI_OK; else echo PW_XOVI_NO; fi',
        timeout=25,
    )
    if "PW_XOVI_OK" not in (out or ""):
        raise RuntimeError(
            "XOVI didn't activate after install. Try rebooting and reinstalling. "
            "Bluetooth keyboard service is unaffected."
        )


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_app_file(root, subdir, name):
    """Resolve a packaged file; host-sourced dirs beat nativeapp copies."""
    repo = _repo_root()
    if subdir in _HOST_SOURCED:
        host = os.path.join(repo, _HOST_SOURCED[subdir], name)
        if os.path.isfile(host):
            return host
    if subdir:
        return os.path.join(root, subdir, name)
    return os.path.join(root, name)


def _upload_app(ssh, root):
    host_resources = _resources_dir()
    subdirs = set(APP_FILES) - {""}
    for sub in sorted(subdirs):
        ssh.exec(f"mkdir -p {DEST_DIR}/{sub}", timeout=5)
    ssh.exec(f"mkdir -p {DEST_DIR}", timeout=5)

    for subdir, files in APP_FILES.items():
        for name in files:
            local = _local_app_file(root, subdir, name)
            remote = f"{DEST_DIR}/{subdir}/{name}" if subdir else f"{DEST_DIR}/{name}"
            if not os.path.isfile(local):
                if name == "resources.rcc":
                    raise RuntimeError(
                        f"Missing {local} — rebuild nativeapp/resources.rcc "
                        "(pyside6-rcc --binary -o resources.rcc qml/application.qrc)"
                    )
                continue
            with open(local, "rb") as f:
                data = f.read()
            if name.endswith((".py", ".sh", ".qml", ".qrc", ".json", ".service")) or name == "entry":
                data = data.replace(b"\r\n", b"\n")
            ssh.upload_bytes(data, remote)

    ssh.exec(f"chmod +x {DEST_DIR}/backend/entry", timeout=5)
    ssh.exec(f"mkdir -p {HOME_STATE_DIR}", timeout=5)
    for name in (
        "bt-keyboard.sh", "bt-lib.sh", "bt-resume.sh",
        "paperwriter-bt-bootstrap.sh", "zz-paperwriter-bt.sh",
        "remarkable-bt-keyboard.service", "paperwriter-bt-bootstrap.service",
    ):
        src = os.path.join(host_resources, name)
        if not os.path.isfile(src):
            continue
        with open(src, "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        ssh.upload_bytes(data, f"{HOME_STATE_DIR}/{name}")
        if name.endswith(".sh"):
            ssh.exec(f"chmod +x {HOME_STATE_DIR}/{name}", timeout=5)


def _install_dropin(ssh, local_name, dest_dir, dest_path, legacy_path, label):
    local = os.path.join(_resources_dir(), local_name)
    with open(local, "r") as f:
        content = f.read().replace("\r\n", "\n")
    _check_unit(content, label)

    def write():
        ssh.exec(f"mkdir -p {dest_dir}", timeout=5)
        ssh.upload_string(content, dest_path)
        if legacy_path:
            ssh.exec(f"rm -f {legacy_path}", timeout=5)

    _with_rw(ssh, write)
    ssh.exec("systemctl daemon-reload", timeout=10)


def _remove_paths(ssh, *paths):
    def rm():
        ssh.exec("rm -f " + " ".join(paths), timeout=5)
    try:
        _with_rw(ssh, rm)
    finally:
        ssh.exec("systemctl daemon-reload", timeout=10)


def _install_watchdog_dropin(ssh):
    _install_dropin(
        ssh, "xochitl-nowatchdog.conf",
        WATCHDOG_DROPIN_DIR, WATCHDOG_DROPIN_PATH, LEGACY_WATCHDOG_PATH,
        "xochitl drop-in",
    )


def _remove_watchdog_dropin(ssh):
    _remove_paths(ssh, WATCHDOG_DROPIN_PATH, LEGACY_WATCHDOG_PATH)


def _install_emergency_override(ssh):
    _install_dropin(
        ssh, "rm-emergency-override.conf",
        EMERGENCY_DROPIN_DIR, EMERGENCY_DROPIN_PATH, LEGACY_EMERGENCY_PATH,
        "rm-emergency override",
    )


def _remove_emergency_override(ssh):
    _remove_paths(ssh, EMERGENCY_DROPIN_PATH, LEGACY_EMERGENCY_PATH)


def _install_autostart(ssh):
    with open(os.path.join(_resources_dir(), "paperwriter-xovi-autostart.sh")) as f:
        script = f.read().replace("\r\n", "\n")
    with open(os.path.join(_resources_dir(), "paperwriter-xovi.service")) as f:
        service = f.read().replace("\r\n", "\n")
    _check_unit(service, "autostart service")

    def write():
        ssh.upload_string(script, AUTOSTART_SCRIPT_PATH)
        ssh.exec(f"chmod +x {AUTOSTART_SCRIPT_PATH}", timeout=5)
        ssh.upload_string(service, AUTOSTART_SERVICE_PATH)
        ssh.exec("mkdir -p /usr/lib/systemd/system/multi-user.target.wants", timeout=5)
        ssh.exec(f"ln -sf {AUTOSTART_SERVICE_PATH} {AUTOSTART_SYMLINK_PATH}", timeout=5)
        ssh.exec(
            f"rm -f {LEGACY_AUTOSTART_SERVICE} {LEGACY_AUTOSTART_SCRIPT} "
            f"{LEGACY_AUTOSTART_SYMLINK}",
            timeout=5,
        )

    _with_rw(ssh, write)
    ssh.exec("systemctl daemon-reload", timeout=10)
    ssh.exec("systemctl enable paperwriter-xovi.service", timeout=15)


def _remove_autostart(ssh):
    _remove_paths(
        ssh,
        AUTOSTART_SCRIPT_PATH, AUTOSTART_SERVICE_PATH, AUTOSTART_SYMLINK_PATH,
        LEGACY_AUTOSTART_SERVICE, LEGACY_AUTOSTART_SCRIPT, LEGACY_AUTOSTART_SYMLINK,
    )
