"""Install Entware + Python 3 on the tablet (pointer daemon prerequisite).

Extracted from the former AppLoad installer so pointer bootstrap does not
depend on the removed native app package.
"""
from __future__ import annotations


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
        raise RuntimeError(
            f"Root filesystem has only {root_free} KB free (need at least 2 MB)."
        )
    home_free = _df_avail_kb(ssh, "/home")
    if home_free < 80 * 1024:
        raise RuntimeError(
            f"/home has only {home_free // 1024} MB free "
            "(need at least 80 MB for Python/entware)."
        )


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
    """True only when Entware ``opkg`` is present *and* runs."""
    _ensure_opt_mount(ssh)
    _, _, code = ssh.exec(
        "export PATH=/opt/bin:/opt/sbin:$PATH; "
        "test -x /opt/bin/opkg && /opt/bin/opkg --version >/dev/null 2>&1",
        timeout=20,
    )
    return code == 0


def _tablet_python_usable(ssh) -> bool:
    """True only when Entware ``/opt/bin/python3`` executes successfully."""
    _ensure_opt_mount(ssh)
    _, _, code = ssh.exec(
        "test -x /opt/bin/python3 && "
        "/opt/bin/python3 -c 'import sys; assert sys.version_info[0] >= 3'",
        timeout=20,
    )
    return code == 0


def _partial_entware_debris(ssh) -> bool:
    _, _, code = ssh.exec(
        "test -d /home/root/.entware || test -f /tmp/rmpp_entware.sh",
        timeout=5,
    )
    return code == 0


def _cleanup_partial_entware(ssh, say):
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
    internet (Wi-Fi) for the first bootstrap. Safe to re-run.
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
        try:
            if not _opkg_usable(ssh) and _partial_entware_debris(ssh):
                _cleanup_partial_entware(ssh, say)
        except Exception:
            pass
        raise
    if not _tablet_python_usable(ssh):
        raise RuntimeError(
            "Tablet Python still missing after bootstrap "
            "(/opt/bin/python3). Check tablet internet (Wi-Fi) and free space."
        )
    say("Tablet Python ready (/opt/bin/python3)")
