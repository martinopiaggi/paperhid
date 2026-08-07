"""Pointer host operations for PaperHid.

Not a standalone CLI. Invoke via the unified entry::

    python cli.py pointer <command>

Implementation is used by ``host_cli.app`` (session/auth + argparse live there).
"""
from __future__ import annotations

import argparse
import base64
import shlex
import sys
from pathlib import Path

from paperpointer.sshutil import (
    DEFAULT_HOST,
    ENABLE_LINK,
    ENABLE_LINK_ETC,
    REMOTE_HOME,
    UNIT_ETC,
    UNIT_NAME,
    UNIT_USR,
    put_file_atomic,
    put_tree,
    run,
)

ROOT = Path(__file__).resolve().parent.parent
DEVICE_DIR = ROOT / "device"
PPD_DIR = DEVICE_DIR / "ppd"
CURSOR_RATE_MAX = 40
CURSOR_STYLES = ("cross", "win95")
SETTINGS_UI_READY = "paperpointer-settings-qml-3.28.0.164"

# Staged deploy paths — never write live ppd/ before a validated swap.
DAEMON_LIVE = f"{REMOTE_HOME}/paperpointerd.py"
DAEMON_CANDIDATE = f"{REMOTE_HOME}/paperpointerd.py.candidate"
PPD_LIVE = f"{REMOTE_HOME}/ppd"
PPD_NEXT = f"{REMOTE_HOME}/ppd.next"


def _out(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))


def put_daemon_sources(
    c,
    *,
    daemon_remote: str | None = None,
    ppd_remote: str | None = None,
) -> tuple[str, str]:
    """Stage facade + ``ppd/`` to candidate paths (not the live tree).

    Returns ``(daemon_candidate, ppd_next)``. Callers must activate via
    :func:`activate_daemon_sources` so both paths swap/rollback together.
    Writing live ``ppd/`` before a facade swap can leave old facade + new modules.
    """
    remote_daemon = daemon_remote or DAEMON_CANDIDATE
    remote_ppd = ppd_remote or PPD_NEXT
    if not PPD_DIR.is_dir():
        raise FileNotFoundError(f"missing daemon package: {PPD_DIR}")
    put_file_atomic(c, DEVICE_DIR / "paperpointerd.py", remote_daemon, 0o755)
    # Fresh staging dir so deleted modules cannot linger from a prior upload.
    run(c, f"rm -rf -- {shlex.quote(remote_ppd)}")
    put_tree(c, PPD_DIR, remote_ppd)
    return remote_daemon, remote_ppd


def activate_daemon_sources(
    c,
    *,
    daemon_candidate: str | None = None,
    ppd_candidate: str | None = None,
    cursor_hz: int | None = None,
    restart: bool = True,
    require_installed: bool = True,
) -> int:
    """Validate staged facade+ppd, swap both live, joint rollback on failure.

    Optional ``cursor_hz`` updates ``pointer.conf`` in the same armed transaction.
    """
    daemon_cand = daemon_candidate or DAEMON_CANDIDATE
    ppd_cand = ppd_candidate or PPD_NEXT
    conf = f"{REMOTE_HOME}/pointer.conf"
    hz_assert = ""
    conf_update = ""
    if cursor_hz is not None:
        hz = max(1, min(CURSOR_RATE_MAX, int(cursor_hz)))
        # Single quotes: this line is embedded in a double-quoted shell -c string.
        hz_assert = f"assert n.get('CURSOR_HZ_MAX', 0) >= {hz}\n"
        conf_update = f"""
if grep -q '^cursor_hz=' "$CONF"; then
  sed -i 's/^cursor_hz=.*/cursor_hz={hz}/' "$CONF"
else
  echo 'cursor_hz={hz}' >> "$CONF"
fi
"""
    require_block = ""
    if require_installed:
        # Pre-split installs may lack live ppd/; staged swap still installs it.
        require_block = """
[ -f "$DAEMON" ] || { echo 'ERROR: daemon not installed' >&2; exit 2; }
"""
    if cursor_hz is not None:
        require_block += """
[ -f "$CONF" ] || { echo 'ERROR: pointer.conf not installed' >&2; exit 2; }
"""
    restart_block = ""
    if restart:
        restart_block = f"""
systemctl restart {UNIT_NAME}
sleep 2
systemctl is-active --quiet {UNIT_NAME}
PID=$(systemctl show -p MainPID --value {UNIT_NAME})
[ "${{PID:-0}}" -gt 0 ] && [ -d "/proc/$PID" ]
echo "paperpointer pid=$PID"
"""
    conf_check = ""
    if cursor_hz is not None:
        conf_check = f"""
/opt/bin/python3 -c 'import runpy; n=runpy.run_path("{DAEMON_LIVE}"); assert n["load_conf"]("{conf}")["cursor_hz"] == {int(cursor_hz)}'
grep '^cursor_hz=' "$CONF"
"""

    script = f"""
set -eu
DAEMON={shlex.quote(DAEMON_LIVE)}
CANDIDATE={shlex.quote(daemon_cand)}
PPD={shlex.quote(PPD_LIVE)}
PPD_NEXT={shlex.quote(ppd_cand)}
CONF={shlex.quote(conf)}
OLD_DAEMON="$DAEMON.rollback.$$"
OLD_PPD="$PPD.rollback.$$"
OLD_CONF="$CONF.rollback.$$"
VAL={shlex.quote(REMOTE_HOME)}/.daemon-validate.$$
ARMED=0
HAD_PPD=0
HAD_CONF=0

rollback() {{
  code=$?
  trap - EXIT HUP INT TERM
  set +e
  if [ "$ARMED" -eq 1 ]; then
    if [ -f "$OLD_DAEMON" ]; then
      mv -f "$OLD_DAEMON" "$DAEMON"
    fi
    # Always drop partially swapped live ppd; restore prior tree if we had one.
    rm -rf "$PPD"
    if [ "$HAD_PPD" -eq 1 ] && [ -d "$OLD_PPD" ]; then
      mv -f "$OLD_PPD" "$PPD"
    fi
    if [ "$HAD_CONF" -eq 1 ] && [ -f "$OLD_CONF" ]; then
      mv -f "$OLD_CONF" "$CONF"
    fi
    systemctl restart {UNIT_NAME} >/dev/null 2>&1 || true
  fi
  rm -rf "$VAL" "$CANDIDATE" "$PPD_NEXT" "$OLD_DAEMON" "$OLD_PPD" "$OLD_CONF"
  exit "$code"
}}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[ -f "$CANDIDATE" ] || {{ echo 'ERROR: daemon candidate missing' >&2; exit 2; }}
[ -d "$PPD_NEXT" ] || {{ echo 'ERROR: ppd.next package missing' >&2; exit 2; }}
{require_block}

# Validate facade against the staged package (not live ppd/).
rm -rf "$VAL"
mkdir -p "$VAL"
cp -p "$CANDIDATE" "$VAL/paperpointerd.py"
ln -sfn "$PPD_NEXT" "$VAL/ppd"
/opt/bin/python3 -c "
import runpy
n = runpy.run_path('$VAL/paperpointerd.py')
assert 'load_conf' in n
assert 'CURSOR_HZ_MAX' in n
{hz_assert}"

if [ -f "$DAEMON" ]; then
  cp -p "$DAEMON" "$OLD_DAEMON"
fi
if [ -d "$PPD" ]; then
  HAD_PPD=1
  rm -rf "$OLD_PPD"
  mv "$PPD" "$OLD_PPD"
fi
if [ -f "$CONF" ]; then
  HAD_CONF=1
  cp -p "$CONF" "$OLD_CONF"
fi

ARMED=1
mv -f "$CANDIDATE" "$DAEMON"
chmod 0755 "$DAEMON"
mv -f "$PPD_NEXT" "$PPD"
{conf_update}
{restart_block}
{conf_check}

ARMED=0
rm -rf "$VAL" "$OLD_DAEMON" "$OLD_PPD" "$OLD_CONF"
trap - EXIT HUP INT TERM
echo "OK: daemon + ppd activated"
"""
    out, err, code = run(c, script, timeout=45)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_detect(c) -> int:
    """Short first-run pointer probe (key: value lines, not a sysdump).

    Verbose dumps live under ``pointer probe`` / ``pointer bt-status``.
    ``bluetoothctl`` is always time-bounded (can hang on wedged NXP BT).
    """
    script = r"""
set +e
_bt() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 4 bluetoothctl "$@" 2>/dev/null
    return $?
  fi
  bluetoothctl "$@" 2>/dev/null &
  _bp=$!
  sleep 4
  kill "$_bp" 2>/dev/null
  wait "$_bp" 2>/dev/null
  return 0
}

# All input device names, comma-separated (host picks touch/pen labels).
inputs=$(grep '^N: Name=' /proc/bus/input/devices 2>/dev/null | sed 's/^N: Name="//;s/"$//' | tr '\n' ',' | sed 's/,$//')
echo "INPUTS=${inputs:-none}"
echo "UINPUT=$([ -e /dev/uinput ] && echo yes || echo no)"
if [ -e /dev/input/touchscreen0 ]; then
  echo "TS_NODE=$(readlink -f /dev/input/touchscreen0 2>/dev/null || echo /dev/input/touchscreen0)"
else
  echo "TS_NODE=none"
fi

mods=$(lsmod 2>/dev/null | awk 'NR>1 && $1 ~ /btnxp|uhid|uinput|bluetooth/ {printf "%s%s", (n++?",":""), $1}')
echo "BT_MODULES=${mods:-none}"

echo "KB_SERVICE=$(systemctl is-active remarkable-bt-keyboard 2>/dev/null || echo unknown)"
echo "BT_SERVICE=$(systemctl is-active bluetooth 2>/dev/null || echo unknown)"
echo "KB_MAC=$(tr -d '\r\n' < /home/root/.paperwriter-keyboard 2>/dev/null || true)"
echo "PAIRED=$(_bt devices Paired 2>/dev/null | awk '{print $2}' | tr '\n' ',' | sed 's/,$//')"

if [ -x /opt/bin/python3 ]; then
  echo "PYTHON=yes"
  echo "PYTHON_VER=$(/opt/bin/python3 -V 2>&1)"
else
  echo "PYTHON=no"
  echo "PYTHON_VER="
fi
echo "POINTER_HOME=$([ -d /home/root/.paperpointer ] && echo yes || echo no)"
echo "POINTER_SERVICE=$(systemctl is-active paperpointer.service 2>/dev/null || echo unknown)"
"""
    try:
        out, _, _ = run(c, script, timeout=20)
    except TimeoutError as exc:
        print(f"pointer detect timed out: {exc}", file=sys.stderr)
        print(
            "hint: tablet BT stack may be wedged; try reboot, or "
            "python cli.py pointer bt-status",
            file=sys.stderr,
        )
        return 1

    facts: dict[str, str] = {}
    for line in (out or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            facts[k.strip()] = v.strip()

    inputs = [n for n in (facts.get("INPUTS") or "").split(",") if n and n != "none"]
    touch = next((n for n in inputs if "touch" in n.lower()), "none")
    pen = next(
        (n for n in inputs if any(t in n.lower() for t in ("marker", "pen", "stylus"))),
        "none",
    )

    py = facts.get("PYTHON", "?")
    py_ver = facts.get("PYTHON_VER", "")
    if py == "yes" and py_ver:
        py_line = py_ver
    elif py == "no":
        py_line = "missing (install --pointer bootstraps)"
    else:
        py_line = py

    kb_mac = facts.get("KB_MAC") or "none"
    paired = facts.get("PAIRED") or "none"

    rows = (
        ("touch", touch),
        ("pen", pen),
        ("uinput", facts.get("UINPUT", "?")),
        ("touchscreen", facts.get("TS_NODE", "?")),
        ("bt_modules", facts.get("BT_MODULES") or "none"),
        ("keyboard_service", facts.get("KB_SERVICE", "?")),
        ("bluetooth", facts.get("BT_SERVICE", "?")),
        ("keyboard_mac", kb_mac if kb_mac else "none"),
        ("paired", paired if paired else "none"),
        ("python3", py_line),
        ("pointer_home", facts.get("POINTER_HOME", "?")),
        ("pointer_service", facts.get("POINTER_SERVICE", "?")),
    )
    for key, value in rows:
        print(f"{key}: {value}")
    return 0


def cmd_probe(c) -> int:
    """Collect read-only display, xochitl, and cursor-extension diagnostics."""
    script = f"""
set +e
echo '=== versions ==='
uname -a
cat /etc/os-release 2>/dev/null
echo '=== xochitl ==='
XO=$(pidof xochitl | awk '{{print $1}}')
echo "pid=${{XO:-none}}"
if [ -n "$XO" ]; then
  ps w | grep "^ *$XO " | head -n 1
  echo '-- preload --'
  tr '\\0' '\\n' < "/proc/$XO/environ" 2>/dev/null | grep -E '^(LD_PRELOAD|QT_|QML_)' || true
  echo '-- xovi-related maps --'
  grep -E 'xovi' "/proc/$XO/maps" 2>/dev/null || true
fi
echo '=== xovi ==='
ls -la /home/root/xovi /home/root/xovi/extensions.d /home/root/xovi/inactive-extensions 2>&1
ls -la /run/xovi-mb /run/xovi-mb-out 2>&1
echo '-- extension home files --'
find /home/root/xovi/exthome -maxdepth 3 -type f 2>/dev/null | sort
echo '-- hashtab --'
ls -lh /home/root/xovi/exthome/qt-resource-rebuilder/hashtab 2>/dev/null
echo '=== cursor transport ==='
ls -la /run/xovi-mb /run/xovi-mb-out 2>&1
ls -la {REMOTE_HOME}/cursor.fifo /home/root/xovi/exthome/qt-resource-rebuilder/paperpointer-cursor.qmd 2>&1
ls -la {REMOTE_HOME}/ui-actions {REMOTE_HOME}/cursors {REMOTE_HOME}/cursor_style 2>&1
grep -E '^(cursor|cursor_hz|cursor_lag_ms|cursor_hide_ms|cursor_style|accel)=' {REMOTE_HOME}/pointer.conf 2>/dev/null
ps w | grep -E 'enable_cursor|paperpointer-cursor|paperpointer-settings|python3 -c|[c]at .*cursor' || true
systemctl is-active {UNIT_NAME} 2>&1
systemctl is-enabled {UNIT_NAME} 2>&1
echo '-- Qt network modules --'
find /usr/lib /opt/lib \\( -iname '*websocket*' -o -path '*qml/QtWebSockets*' \\) 2>/dev/null | head -n 80
echo '-- compilers --'
for t in qmake6 qmake cmake g++ gcc clang; do
  printf '%s=' "$t"
  command -v "$t" || true
done
echo '=== display ==='
ls -la /dev/dri 2>&1
for f in /sys/class/drm/card*-*/modes /sys/class/drm/card*-*/status; do
  [ -f "$f" ] && echo "$f: $(tr '\\n' ' ' < "$f")"
done
echo '=== tooling ==='
for t in readelf objdump nm strings gdb gdbserver; do
  printf '%s=' "$t"
  command -v "$t" || true
done
echo '=== recent cursor log ==='
logread 2>/dev/null | grep -iE 'paperpointer|commandexecutor|qml|xovi' | tail -n 160 || true
journalctl -u xochitl --no-pager -n 300 2>/dev/null | grep -iE 'paperpointer|commandexecutor|qml|error|xovi' | tail -n 160 || true
"""
    out, err, code = run(c, script, timeout=45)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_bt_status(c) -> int:
    """Collect read-only BlueZ, PaperHid keyboard, and HID diagnostics."""
    script = """
set +e
echo '=== controllers ==='
bluetoothctl show
echo '=== paired devices ==='
bluetoothctl devices Paired
for m in $(bluetoothctl devices Paired | awk '{print $2}'); do
  echo "--- $m ---"
  bluetoothctl info "$m"
done
echo '=== services ==='
systemctl status bluetooth remarkable-bt-keyboard --no-pager -l 2>&1
echo '=== processes ==='
ps w | grep -E 'bluetoothd|btattach|btnxpuart|paperwriter|remarkable-bt' | grep -v grep
echo '=== PaperHid keyboard files (~/.paperwriter) ==='
ls -la /home/root/.paperwriter /home/root/.paperwriter-keyboard 2>&1
systemctl cat remarkable-bt-keyboard 2>&1
echo '-- PaperHid keyboard logs --'
tail -n 120 /home/root/.paperwriter/bt.log 2>/dev/null
tail -n 120 /home/root/.paperwriter/resume.log 2>/dev/null
echo '-- resume helper --'
sed -n '1,240p' /home/root/.paperwriter/bt-resume.sh 2>/dev/null
echo '=== recent service journal ==='
journalctl -u remarkable-bt-keyboard -u bluetooth --no-pager -n 120 2>&1
echo '=== recent HID/kernel messages ==='
dmesg | grep -iE 'bluetooth|uhid|hid|btnx|hci' | tail -n 120
echo '=== input nodes ==='
cat /proc/bus/input/devices
"""
    out, err, code = run(c, script, timeout=45)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_bt_recover(c) -> int:
    """Recover a false-connected BLE HID without deleting the pairing."""
    script = f"""
set -u
MAC=$(tr -d ' \\t\\r\\n' < /home/root/.paperwriter-keyboard 2>/dev/null)
if [ -z "$MAC" ]; then
  echo 'ERROR: no saved keyboard MAC' >&2
  exit 2
fi

has_hid() {{
  grep -q 'CLVX S | Channel 2 Keyboard' /proc/bus/input/devices &&
  grep -q 'CLVX S | Channel 2 Touchpad' /proc/bus/input/devices
}}

echo "recovering $MAC"
if has_hid; then
  echo 'HID input nodes already present'
  exit 0
fi

# BlueZ can retain Connected=yes after HOG discovery failed.  Tear down that
# logical connection, restart bluetoothd, and make one fresh GATT connection.
bluetoothctl disconnect "$MAC" >/dev/null 2>&1 &
CTL_PID=$!
sleep 3
kill "$CTL_PID" 2>/dev/null || true
wait "$CTL_PID" 2>/dev/null || true
systemctl restart bluetooth
sleep 3
bluetoothctl power on >/dev/null 2>&1 &
CTL_PID=$!
sleep 2
kill "$CTL_PID" 2>/dev/null || true
wait "$CTL_PID" 2>/dev/null || true
bluetoothctl connect "$MAC" >/tmp/paperpointer-bt-connect.log 2>&1 &
CTL_PID=$!

i=0
while [ "$i" -lt 30 ]; do
  if has_hid; then
    kill "$CTL_PID" 2>/dev/null || true
    wait "$CTL_PID" 2>/dev/null || true
    echo 'OK: keyboard and touchpad HID nodes restored'
    systemctl restart {UNIT_NAME} 2>/dev/null || true
    /opt/bin/python3 {REMOTE_HOME}/paperpointerd.py list 2>/dev/null || true
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done

kill "$CTL_PID" 2>/dev/null || true
wait "$CTL_PID" 2>/dev/null || true
echo 'ERROR: BlueZ reconnect completed without HID input nodes' >&2
cat /tmp/paperpointer-bt-connect.log 2>/dev/null || true
bluetoothctl info "$MAC" 2>/dev/null | grep -E 'Connected:|Paired:|Trusted:|Battery' || true
journalctl -u bluetooth --no-pager -n 30 2>/dev/null | tail -n 30
exit 3
"""
    out, err, code = run(c, script, timeout=65)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def preflight_tablet_python(c) -> int:
    """Check tablet-side Python before uploading pointer files.

    paperpointerd requires a *runnable* ``/opt/bin/python3`` (Entware). A
    home-backed ``/home/root/.entware`` bind is accepted. Presence of a
    binary alone is not enough (partial Entware installs). Returns 0 when
    ready, 2 when missing — and does not upload anything.
    """
    script = r"""
set +e
_py_ok() {
  [ -x /opt/bin/python3 ] || return 1
  /opt/bin/python3 -c 'import sys; assert sys.version_info[0] >= 3' 2>/dev/null
}
if _py_ok; then
  echo "tablet_python: ok (/opt/bin/python3)"
  /opt/bin/python3 -V 2>&1 || true
  exit 0
fi
if [ -x /home/root/.entware/bin/python3 ]; then
  mkdir -p /opt
  mountpoint -q /opt || mount --bind /home/root/.entware /opt
fi
if _py_ok; then
  echo "tablet_python: ok (entware bind)"
  /opt/bin/python3 -V 2>&1 || true
  exit 0
fi
# Distinguish partial debris from a clean vanilla tablet.
if [ -d /home/root/.entware ] || [ -x /opt/bin/python3 ] || [ -x /opt/bin/opkg ]; then
  echo "ERROR: tablet Python incomplete — Entware debris present but /opt/bin/python3 not usable."
  echo "A partial bootstrap is not a working install. Clean and re-run:"
  echo "  python cli.py bootstrap-python"
  exit 2
fi
echo "ERROR: tablet Python missing — need /opt/bin/python3 (Entware)."
echo "Pointer install will not upload until this is fixed."
echo "Fix: re-run install (auto-bootstrap) with tablet Wi-Fi on, or:"
echo "  python cli.py bootstrap-python"
exit 2
"""
    out, err, code = run(c, script, timeout=30)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


class _ParamikoExec:
    """Adapt a raw Paramiko client to the ``.exec(cmd, timeout=)`` API."""

    def __init__(self, c):
        self._c = c

    def exec(self, cmd, timeout=30):
        return run(self._c, cmd, timeout=timeout)


def bootstrap_tablet_python(c, status_cb=None) -> None:
    """Install Entware + Python 3 over an open Paramiko session."""
    from core.tablet_python import ensure_tablet_python

    ensure_tablet_python(_ParamikoExec(c), status_cb=status_cb)


def cmd_install(c, *, bootstrap: bool = True) -> int:
    if not DEVICE_DIR.is_dir():
        print("missing device/", file=sys.stderr)
        return 1
    # Preflight before any upload. On vanilla tablets, bootstrap Entware+Python.
    pre = preflight_tablet_python(c)
    if pre != 0 and bootstrap:
        print(
            "Tablet Python missing — bootstrapping Entware + python3 "
            "(tablet needs Wi-Fi / internet; ~2-5 min)...",
            flush=True,
        )
        try:
            bootstrap_tablet_python(c)
        except Exception as e:
            print(f"bootstrap failed: {e}", file=sys.stderr)
            print(
                "install aborted: could not install tablet Python (no upload).\n"
                "Turn on tablet Wi-Fi, free ~80 MB on /home, then:\n"
                "  python cli.py bootstrap-python\n"
                "  python cli.py install --pointer",
                file=sys.stderr,
            )
            return 2
        pre = preflight_tablet_python(c)
    if pre != 0:
        print(
            "install aborted: tablet Python prerequisite not met (no upload).",
            file=sys.stderr,
        )
        return pre
    print(f"uploading -> {REMOTE_HOME}")
    put_tree(c, DEVICE_DIR, REMOTE_HOME, preserve_existing={"pointer.conf"})
    run(c, f"chmod 755 {REMOTE_HOME}/run.sh {REMOTE_HOME}/paperpointerd.py")

    script = f"""
set -e
modprobe uinput 2>/dev/null || true
# bind entware if needed
if [ ! -x /opt/bin/python3 ] && [ -x /home/root/.entware/bin/python3 ]; then
  mkdir -p /opt
  mountpoint -q /opt || mount --bind /home/root/.entware /opt
fi
if [ ! -x /opt/bin/python3 ]; then
  echo "ERROR: need /opt/bin/python3 (Entware). Run: python cli.py bootstrap-python"
  exit 2
fi
cp {REMOTE_HOME}/{UNIT_NAME} {UNIT_ETC}
chmod 0644 {UNIT_ETC}
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf {UNIT_ETC} {ENABLE_LINK_ETC}
systemctl daemon-reload
systemctl is-enabled --quiet {UNIT_NAME}
systemctl restart {UNIT_NAME}
sleep 1
systemctl is-active {UNIT_NAME}
echo "enabled=$(systemctl is-enabled {UNIT_NAME})"
echo OK
"""
    out, err, code = run(c, script, timeout=60)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    if code != 0:
        print(f"install failed code={code}", file=sys.stderr)
        return code
    print("installed and started")
    return 0


def cmd_uninstall(c) -> int:
    script = f"""
systemctl stop {UNIT_NAME} 2>/dev/null || true
systemctl disable {UNIT_NAME} 2>/dev/null || true
rm -f {UNIT_ETC} {ENABLE_LINK_ETC}
if mount -o remount,rw / 2>/dev/null; then
  rm -f {UNIT_USR} {ENABLE_LINK}
  sync
  mount -o remount,ro / 2>/dev/null || true
fi
systemctl daemon-reload
# leave files in home unless --purge later
echo stopped
"""
    out, _, _ = run(c, script)
    print(out.strip())
    return 0


def cmd_status(c) -> int:
    out, _, _ = run(
        c,
        f"systemctl is-active {UNIT_NAME} 2>&1; "
        f"systemctl is-enabled {UNIT_NAME} 2>&1; echo; "
        f"systemctl status {UNIT_NAME} --no-pager -l 2>&1 | sed -n '1,25p'; "
        f"echo ---; ls -la {REMOTE_HOME} 2>&1; "
        f"echo --- LOG; tail -n 40 {REMOTE_HOME}/pointer.log 2>/dev/null || true; "
        f"echo --- INPUT; cat /proc/bus/input/devices; "
        f"echo --- SOURCES; /opt/bin/python3 {REMOTE_HOME}/paperpointerd.py list 2>/dev/null || true",
        timeout=45,
    )
    _out(out)
    return 0


def cmd_restart(c) -> int:
    out, err, code = run(c, f"systemctl restart {UNIT_NAME}; sleep 1; systemctl is-active {UNIT_NAME}")
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_cursor_style(c, style: str) -> int:
    """Upload skins, activate staged daemon+ppd, set style via allow-listed script."""
    style = (style or "").strip().lower()
    if style not in CURSOR_STYLES:
        print(f"style must be one of: {', '.join(CURSOR_STYLES)}", file=sys.stderr)
        return 2
    cursors = DEVICE_DIR / "cursors"
    script = DEVICE_DIR / "ui-actions" / "set-cursor-style.sh"
    lib = DEVICE_DIR / "ui-actions" / "lib.sh"
    if not script.is_file() or not lib.is_file():
        print("missing ui-actions for cursor-style", file=sys.stderr)
        return 2
    ui_bin = DEVICE_DIR / "paperhid-ui"
    paperhid_home = "/home/root/.paperhid"
    run(c, f"mkdir -p {REMOTE_HOME}/ui-actions {REMOTE_HOME}/cursors {paperhid_home}")
    put_file_atomic(c, lib, f"{REMOTE_HOME}/ui-actions/lib.sh", 0o755)
    put_file_atomic(c, script, f"{REMOTE_HOME}/ui-actions/set-cursor-style.sh", 0o755)
    if ui_bin.is_file():
        put_file_atomic(c, ui_bin, f"{paperhid_home}/paperhid-ui", 0o755)
    if cursors.is_dir():
        for path in sorted(cursors.iterdir()):
            if path.is_file() and path.name != "README.md":
                put_file_atomic(c, path, f"{REMOTE_HOME}/cursors/{path.name}", 0o644)
    put_daemon_sources(c)
    # Style path restarts the daemon after conf write (via paperhid-ui).
    act = activate_daemon_sources(c, restart=False)
    if act != 0:
        return act
    out, err, code = run(
        c,
        f"sed -i 's/\\r$//' {paperhid_home}/paperhid-ui {REMOTE_HOME}/ui-actions/*.sh 2>/dev/null; "
        f"chmod 755 {paperhid_home}/paperhid-ui {REMOTE_HOME}/ui-actions/*.sh; "
        f"{paperhid_home}/paperhid-ui set-cursor-style {style}",
        timeout=30,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_cursor_rate(c, hz: int) -> int:
    """Stage facade+ppd, validate, swap both, set rate, restart (joint rollback)."""
    hz = max(1, min(CURSOR_RATE_MAX, int(hz)))
    put_daemon_sources(c)
    return activate_daemon_sources(c, cursor_hz=hz, restart=True)


def cmd_input_monitor(c, seconds: int) -> int:
    """Capture framed key events from all detected pointer nodes."""
    source = f'''import os, select, struct, sys, time
sys.path.insert(0, {REMOTE_HOME!r})
import paperpointerd as pp
items = pp.list_sources()
opened = []
for path, kind, name in items:
    try:
        opened.append((os.open(path, os.O_RDONLY | os.O_NONBLOCK), path, kind, name))
    except OSError as exc:
        print("open failed", path, repr(exc), flush=True)
print("monitoring", [(p, k, n) for _, p, k, n in opened], flush=True)
frames = {{fd: {{"dx": 0, "dy": 0, "keys": []}} for fd, *_ in opened}}
by_fd = {{fd: (path, kind, name) for fd, path, kind, name in opened}}
deadline = time.monotonic() + {seconds}
while opened and time.monotonic() < deadline:
    ready, _, _ = select.select([fd for fd, *_ in opened], [], [], 0.5)
    for fd in ready:
        try:
            data = os.read(fd, pp.EVENT_SIZE * 256)
        except BlockingIOError:
            continue
        frame = frames[fd]
        for off in range(0, len(data) - pp.EVENT_SIZE + 1, pp.EVENT_SIZE):
            _, _, etype, code, value = struct.unpack_from(pp.EVENT_FMT, data, off)
            if etype == pp.EV_REL and code == pp.REL_X:
                frame["dx"] += value
            elif etype == pp.EV_REL and code == pp.REL_Y:
                frame["dy"] += value
            elif etype == pp.EV_KEY:
                frame["keys"].append((code, value))
            elif etype == pp.EV_SYN and code == pp.SYN_DROPPED:
                print(by_fd[fd][0], "SYN_DROPPED", flush=True)
                frame.update(dx=0, dy=0, keys=[])
            elif etype == pp.EV_SYN and code == pp.SYN_REPORT:
                if frame["keys"]:
                    print(by_fd[fd][0], "delta", frame["dx"], frame["dy"],
                          "keys", frame["keys"], flush=True)
                frame.update(dx=0, dy=0, keys=[])
for fd, *_ in opened:
    os.close(fd)
'''
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    command = (
        "/opt/bin/python3 -c \"import base64;"
        f"exec(base64.b64decode('{encoded}'))\""
    )
    out, err, code = run(c, command, timeout=seconds + 10)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_test_tap(c, x=None, y=None) -> int:
    args = ""
    if x is not None and y is not None:
        args = f"{int(x)} {int(y)}"
    out, err, code = run(
        c,
        f"modprobe uinput 2>/dev/null; "
        f"/opt/bin/python3 {REMOTE_HOME}/paperpointerd.py tap {args}",
        timeout=20,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_watch(c) -> int:
    out, _, _ = run(
        c,
        "echo 'Paired:'; bluetoothctl devices Paired; "
        "echo; echo 'Connected check:'; "
        "for m in $(bluetoothctl devices Paired | awk '{print $2}'); do "
        "  echo -n \"$m \"; bluetoothctl info $m | grep -E 'Name:|Connected:'; "
        "done; "
        "echo; echo 'Input nodes:'; cat /proc/bus/input/devices; "
        "echo; echo 'Pointer sources:'; "
        f"/opt/bin/python3 {REMOTE_HOME}/paperpointerd.py list 2>/dev/null || true",
        timeout=40,
    )
    _out(out)
    return 0


def cmd_enable_cursor(c) -> int:
    """Install and health-check the version-locked XOVI QML cursor."""
    put_tree(c, DEVICE_DIR, REMOTE_HOME, preserve_existing={"pointer.conf"})
    out, err, code = run(
        c,
        f"sed -i 's/\\r$//' {REMOTE_HOME}/enable_cursor.sh; "
        f"chmod 755 {REMOTE_HOME}/enable_cursor.sh; "
        f"{REMOTE_HOME}/enable_cursor.sh",
        timeout=180,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err[:3000])
    return code


def _put_settings_layout_runtime(c, paperhid_home: str) -> None:
    """Ship on-device set_layout.py + shared/tools packages under .paperhid/py."""
    set_layout = DEVICE_DIR / "set_layout.py"
    if set_layout.is_file():
        put_file_atomic(c, set_layout, f"{paperhid_home}/set_layout.py", 0o755)
    py_root = f"{paperhid_home}/py"
    run(c, f"mkdir -p {py_root}/shared {py_root}/tools")
    shared_dir = ROOT / "shared"
    tools_dir = ROOT / "tools"
    for name in (
        "__init__.py",
        "constants.py",
        "transport.py",
        "layout_patcher.py",
        "layouts.py",
    ):
        path = shared_dir / name
        if path.is_file():
            put_file_atomic(c, path, f"{py_root}/shared/{name}", 0o644)
    for name in ("__init__.py", "generate_qmap.py"):
        path = tools_dir / name
        if path.is_file():
            put_file_atomic(c, path, f"{py_root}/tools/{name}", 0o644)


def cmd_enable_settings_ui(c) -> int:
    """Install and canary the optional Settings > Help controls."""
    qmd = DEVICE_DIR / "paperpointer-settings.qmd"
    installer = DEVICE_DIR / "enable_settings_ui.sh"
    ui_bin = DEVICE_DIR / "paperhid-ui"
    ui_actions = DEVICE_DIR / "ui-actions"
    cursors = DEVICE_DIR / "cursors"
    paperhid_home = "/home/root/.paperhid"
    if not qmd.is_file() or not installer.is_file() or not ui_bin.is_file():
        print("missing settings UI device files", file=sys.stderr)
        return 1

    run(c, f"mkdir -p {paperhid_home} {REMOTE_HOME}/ui-actions {REMOTE_HOME}/cursors")
    put_file_atomic(c, qmd, f"{REMOTE_HOME}/paperpointer-settings.qmd", 0o644)
    put_file_atomic(c, installer, f"{REMOTE_HOME}/enable_settings_ui.sh", 0o755)
    put_file_atomic(c, ui_bin, f"{paperhid_home}/paperhid-ui", 0o755)
    _put_settings_layout_runtime(c, paperhid_home)
    # Legacy thin wrappers still used by host CLI cursor-style path.
    if ui_actions.is_dir():
        for path in ui_actions.iterdir():
            if path.is_file():
                put_file_atomic(c, path, f"{REMOTE_HOME}/ui-actions/{path.name}", 0o755)
    if cursors.is_dir():
        for path in sorted(cursors.iterdir()):
            if path.is_file() and path.name != "README.md":
                put_file_atomic(c, path, f"{REMOTE_HOME}/cursors/{path.name}", 0o644)

    out, err, code = run(
        c,
        f"sed -i 's/\\r$//' {paperhid_home}/paperhid-ui {paperhid_home}/set_layout.py "
        f"{REMOTE_HOME}/enable_settings_ui.sh 2>/dev/null; "
        f"chmod 755 {paperhid_home}/paperhid-ui {paperhid_home}/set_layout.py "
        f"{REMOTE_HOME}/enable_settings_ui.sh; "
        f"{REMOTE_HOME}/enable_settings_ui.sh",
        timeout=120,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err[:3000])
    return code


def cmd_disable_settings_ui(c) -> int:
    """Remove only the optional settings patch, preserving the cursor."""
    script = f"""
set -eu
XOVI=/home/root/xovi
QMD=$XOVI/exthome/qt-resource-rebuilder/paperpointer-settings.qmd
BACKUP=$XOVI/exthome/qt-resource-rebuilder/.paperpointer-settings.qmd.disable.$$
CURSOR_QMD=$XOVI/exthome/qt-resource-rebuilder/paperpointer-cursor.qmd
CURSOR_PIPE={REMOTE_HOME}/cursor.fifo
ARMED=0

xochitl_pid() {{
  set -- $(pidof xochitl 2>/dev/null || true)
  [ "$#" -gt 0 ] && printf '%s\\n' "$1"
}}
has_xovi() {{
  pid="$1"
  [ -n "$pid" ] && [ -p /run/xovi-mb ] &&
    grep -q 'qt-resource-rebuilder.so' "/proc/$pid/maps" 2>/dev/null
}}
wait_for_stable_xochitl() {{
  stable_for="$1"
  timeout="$2"
  candidate=''
  stable=0
  elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    pid=$(xochitl_pid)
    if has_xovi "$pid"; then
      if [ "$pid" = "$candidate" ]; then
        stable=$((stable + 1))
      else
        candidate="$pid"
        stable=1
      fi
      if [ "$stable" -ge "$stable_for" ]; then
        XO="$candidate"
        return 0
      fi
    else
      candidate=''
      stable=0
    fi
    elapsed=$((elapsed + 1))
    sleep 1
  done
  return 1
}}
start_xovi() {{
  systemctl reset-failed xochitl.service 2>/dev/null || true
  "$XOVI/start"
}}

rollback() {{
  code=$?
  trap - EXIT HUP INT TERM
  set +e
  if [ "$ARMED" -eq 1 ] && [ -f "$BACKUP" ]; then
    mv -f "$BACKUP" "$QMD"
    systemctl reset-failed xochitl.service 2>/dev/null || true
    "$XOVI/start" >/tmp/paperpointer-settings-disable-rollback.log 2>&1 || true
  fi
  rm -f "$BACKUP"
  exit "$code"
}}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ ! -f "$QMD" ]; then
  echo 'OK: settings UI already disabled'
  trap - EXIT HUP INT TERM
  exit 0
fi
mv "$QMD" "$BACKUP"
ARMED=1
start_xovi >/tmp/paperpointer-settings-disable.log 2>&1
wait_for_stable_xochitl 10 60

if [ -f "$CURSOR_QMD" ] && [ -p "$CURSOR_PIPE" ]; then
  printf '0.500000,0.500000,0\\n' >"$CURSOR_PIPE" &
  CANARY_PID=$!
  i=0
  while kill -0 "$CANARY_PID" >/dev/null 2>&1 && [ "$i" -lt 30 ]; do
    i=$((i + 1))
    sleep 1
  done
  if kill -0 "$CANARY_PID" >/dev/null 2>&1; then
    kill "$CANARY_PID" >/dev/null 2>&1 || true
    wait "$CANARY_PID" >/dev/null 2>&1 || true
    exit 23
  fi
  wait "$CANARY_PID"
  /opt/bin/python3 {REMOTE_HOME}/paperpointerd.py cursor-ping
fi
wait_for_stable_xochitl 15 45
# Pointer daemon is optional (keyboard-only installs have Settings without it).

ARMED=0
rm -f "$BACKUP"
trap - EXIT HUP INT TERM
echo "OK: settings UI disabled; cursor session preserved pid=$XO"
"""
    out, err, code = run(c, script, timeout=150)
    _out(out)
    if err.strip():
        sys.stderr.write(err[:3000])
    return code


def cmd_settings_ui_check(c) -> int:
    """Require the lazily-loaded Settings > Help QML health endpoint."""
    script = f"""
/opt/bin/python3 - <<'PY'
import sys
sys.path.insert(0, {REMOTE_HOME!r})
from paperpointerd import call_ui_broker

expected = {SETTINGS_UI_READY!r}
response = call_ui_broker("paperpointer.settings.ping", timeout=3.0)
if response != expected:
    print(
        "ERROR: Settings > Help has not loaded the PaperHid settings UI "
        f"(response={{response!r}})",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(response)
PY
"""
    out, err, code = run(c, script, timeout=10)
    _out(out)
    if err.strip():
        sys.stderr.write(err[:1000])
    return code


def cmd_stock_ui(c) -> int:
    """Remove only the cursor overlay; keep Settings > Help if installed."""
    script = f"""
set -u
QMD=/home/root/xovi/exthome/qt-resource-rebuilder/paperpointer-cursor.qmd
SETTINGS_QMD=/home/root/xovi/exthome/qt-resource-rebuilder/paperpointer-settings.qmd
FIFO={REMOTE_HOME}/cursor.fifo
WARN=0

if [ -f {REMOTE_HOME}/pointer.conf ]; then
  if ! sed -i 's/^cursor=.*/cursor=0/' {REMOTE_HOME}/pointer.conf; then
    echo 'WARNING: could not disable cursor in pointer.conf' >&2
    WARN=1
  fi
fi
if systemctl cat {UNIT_NAME} >/dev/null 2>&1; then
  if ! systemctl restart {UNIT_NAME}; then
    echo 'WARNING: PaperHid pointer daemon did not restart; continuing UI recovery' >&2
    WARN=1
  fi
fi
# Cursor overlay only — never delete the Settings panel QMD here.
rm -f "$QMD" "$FIFO" || WARN=1

if [ -f "$SETTINGS_QMD" ]; then
  # Keep XOVI tethered so Settings > Help continues to work.
  systemctl reset-failed xochitl.service 2>/dev/null || true
  if ! /home/root/xovi/start >/tmp/paperpointer-stock-ui.log 2>&1; then
    cat /tmp/paperpointer-stock-ui.log >&2 2>/dev/null || true
    exit 4
  fi
  sleep 4
  XO=$(pidof xochitl 2>/dev/null | awk '{{print $1}}')
  [ -n "$XO" ] || {{ echo 'ERROR: xochitl did not start after cursor remove' >&2; exit 5; }}
  rm -f /tmp/paperpointer-stock-ui.log
  echo "OK: cursor overlay removed; Settings UI preserved (xochitl pid=$XO)"
else
  if ! /home/root/xovi/stock >/tmp/paperpointer-stock-ui.log 2>&1; then
    cat /tmp/paperpointer-stock-ui.log >&2 2>/dev/null || true
    exit 4
  fi
  sleep 4
  XO=$(pidof xochitl 2>/dev/null | awk '{{print $1}}')
  [ -n "$XO" ] || {{ echo 'ERROR: stock xochitl did not start' >&2; exit 5; }}
  if grep -q '/home/root/xovi/' "/proc/$XO/maps" 2>/dev/null; then
    echo 'ERROR: xochitl still has XOVI mappings' >&2
    exit 6
  fi
  rm -f /tmp/paperpointer-stock-ui.log
  echo "OK: stock xochitl pid=$XO; cursor publisher disabled"
fi
if [ "$WARN" -ne 0 ]; then
  echo 'WARNING: cursor recovery finished, but daemon cleanup needs attention' >&2
fi
"""
    out, err, code = run(
        c,
        script,
        timeout=60,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err[:2000])
    return code


def cmd_reconnect(c) -> int:
    """Kick PaperHid keyboard reconnect for saved MAC(s)."""
    script = r"""
MAC=$(cat /home/root/.paperwriter-keyboard 2>/dev/null | tr -d ' \t\r\n')
if [ -f /home/root/.paperwriter/bt-lib.sh ]; then
  . /home/root/.paperwriter/bt-lib.sh
  pw_ps_disable
  pw_power_on 4
fi
bluetoothctl power on >/dev/null 2>&1 || true
(echo scan on; sleep 6; echo scan off) | bluetoothctl >/dev/null 2>&1 &
SP=$!
sleep 2
for m in $MAC C2:8B:E9:E7:7B:D5 C2:B3:C3:30:75:FF; do
  [ -n "$m" ] || continue
  echo "connect $m"
  bluetoothctl connect "$m" || true
  sleep 3
  bluetoothctl info "$m" 2>/dev/null | grep -E 'Name:|Connected:' || true
done
wait $SP 2>/dev/null || true
echo ---
cat /proc/bus/input/devices
"""
    out, err, code = run(c, script, timeout=90)
    _out(out)
    if err.strip():
        sys.stderr.write(err[:2000])
    return 0


# Supported pointer subcommands under ``python cli.py pointer …``.
POINTER_SIMPLE_COMMANDS = (
    "detect",
    "probe",
    "bt-status",
    "bt-recover",
    "install",
    "uninstall",
    "status",
    "restart",
    "watch",
    "reconnect",
    "enable-cursor",
    "enable-settings-ui",
    "disable-settings-ui",
    "settings-ui-check",
    "stock-ui",
)
POINTER_ALL_COMMANDS = POINTER_SIMPLE_COMMANDS + (
    "test-tap",
    "cursor-rate",
    "cursor-style",
    "input-monitor",
)


def shared_flag_parser(*, for_subparser: bool = False) -> argparse.ArgumentParser:
    """Shared flags work before or after the subcommand.

    Subparsers use ``default=argparse.SUPPRESS`` so a flag given *before* the
    subcommand (on the parent) is not wiped by the child default.
    """
    shared = argparse.ArgumentParser(add_help=False)
    default = argparse.SUPPRESS if for_subparser else None
    host_default = argparse.SUPPRESS if for_subparser else DEFAULT_HOST
    shared.add_argument("--host", default=host_default)
    shared.add_argument(
        "--password",
        default=default,
        help="SSH password (PAPERHID_PASSWORD; legacy PAPERWRITER_/PAPERPOINTER_ also accepted)",
    )
    return shared


def register_pointer_commands(
    sub: argparse._SubParsersAction,
    shared: argparse.ArgumentParser | None = None,
) -> None:
    """Register the full pointer command inventory on *sub*."""
    if shared is None:
        shared = shared_flag_parser(for_subparser=True)
    for name in POINTER_SIMPLE_COMMANDS:
        sub.add_parser(name, parents=[shared])

    t = sub.add_parser("test-tap", parents=[shared])
    t.add_argument("x", nargs="?", type=int)
    t.add_argument("y", nargs="?", type=int)

    rate = sub.add_parser("cursor-rate", parents=[shared])
    rate.add_argument(
        "hz",
        type=int,
        choices=range(1, CURSOR_RATE_MAX + 1),
        metavar="HZ",
        help=f"bounded publish rate (1-{CURSOR_RATE_MAX}; shipped default 30)",
    )

    style_p = sub.add_parser("cursor-style", parents=[shared])
    style_p.add_argument(
        "style",
        choices=CURSOR_STYLES,
        help="cursor skin: cross (default geometry) or win95 (PNG arrow)",
    )

    monitor = sub.add_parser("input-monitor", parents=[shared])
    monitor.add_argument("seconds", nargs="?", type=int, default=12)


def dispatch_pointer(args, connection) -> int:
    """Dispatch a parsed pointer subcommand to the handler.

    *connection* must be a raw Paramiko ``SSHClient`` (v1 designated type for
    pointer operations). Callers own connect/close.
    """
    cmd = getattr(args, "cmd", None) or getattr(args, "pointer_cmd", None)
    if cmd in POINTER_SIMPLE_COMMANDS:
        handler = globals()[f"cmd_{cmd.replace('-', '_')}"]
        return handler(connection)
    if cmd == "cursor-rate":
        return cmd_cursor_rate(connection, args.hz)
    if cmd == "cursor-style":
        return cmd_cursor_style(connection, args.style)
    if cmd == "input-monitor":
        return cmd_input_monitor(connection, max(1, min(30, args.seconds)))
    if cmd == "test-tap":
        return cmd_test_tap(connection, args.x, args.y)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Build a pointer-only parser (library/test helper; use ``cli.py pointer``)."""
    parent_shared = shared_flag_parser(for_subparser=False)
    p = argparse.ArgumentParser(
        prog="python cli.py pointer",
        description="PaperHid pointer (mouse/touchpad -> touch)",
        parents=[parent_shared],
    )
    p.set_defaults(host=DEFAULT_HOST, password=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    register_pointer_commands(sub, shared_flag_parser(for_subparser=True))
    return p
