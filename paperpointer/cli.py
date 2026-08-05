from __future__ import annotations

import argparse
import base64
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
    connect,
    password_from_env,
    put_file_atomic,
    put_tree,
    run,
)

ROOT = Path(__file__).resolve().parent.parent
DEVICE_DIR = ROOT / "device"
CURSOR_RATE_MAX = 40
CURSOR_STYLES = ("cross", "win95")
SETTINGS_UI_READY = "paperpointer-settings-qml-3.28.0.164"


def _out(text: str) -> None:
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))


def cmd_detect(c) -> int:
    out, _, _ = run(
        c,
        "uname -a; echo ---; cat /etc/os-release 2>/dev/null | sed -n '1,8p'; "
        "echo ---; lsmod | grep -E 'btnxp|uhid|uinput|bluetooth' || true; "
        "echo ---; cat /proc/bus/input/devices; "
        "echo ---; ls -la /dev/uinput /dev/input/ 2>&1; "
        "echo ---; bluetoothctl devices Paired 2>/dev/null; "
        "echo ---; cat /home/root/.paperwriter-keyboard 2>/dev/null; "
        "systemctl is-active remarkable-bt-keyboard bluetooth 2>&1; "
        "echo ---; command -v /opt/bin/python3; /opt/bin/python3 -V 2>&1",
        timeout=30,
    )
    _out(out)
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
  echo '-- cursor-related maps --'
  grep -E 'xovi|pp-cursor|framebuffer-spy' "/proc/$XO/maps" 2>/dev/null || true
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
logread 2>/dev/null | grep -iE 'paperpointer|commandexecutor|qml|pp-cursor|framebuffer-spy|xovi' | tail -n 160 || true
journalctl -u xochitl --no-pager -n 300 2>/dev/null | grep -iE 'paperpointer|commandexecutor|qml|error|xovi' | tail -n 160 || true
"""
    out, err, code = run(c, script, timeout=45)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_bt_status(c) -> int:
    """Collect read-only BlueZ, PaperWriter, and HID diagnostics."""
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
echo '=== PaperWriter files ==='
ls -la /home/root/.paperwriter /home/root/.paperwriter-keyboard 2>&1
systemctl cat remarkable-bt-keyboard 2>&1
echo '-- PaperWriter logs --'
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

    paperpointerd requires ``/opt/bin/python3`` (Entware). A home-backed
    ``/home/root/.entware`` bind is accepted. Returns 0 when ready, 2 when
    missing — and does not upload anything.
    """
    script = r"""
set +e
if [ -x /opt/bin/python3 ]; then
  echo "tablet_python: ok (/opt/bin/python3)"
  /opt/bin/python3 -V 2>&1 || true
  exit 0
fi
if [ -x /home/root/.entware/bin/python3 ]; then
  mkdir -p /opt
  mountpoint -q /opt || mount --bind /home/root/.entware /opt
fi
if [ -x /opt/bin/python3 ]; then
  echo "tablet_python: ok (entware bind)"
  /opt/bin/python3 -V 2>&1 || true
  exit 0
fi
echo "ERROR: tablet Python missing — need /opt/bin/python3 (Entware)."
echo "Pointer install will not upload until this is fixed."
echo "Supported paths:"
echo "  1) Install Entware for Paper Pro (rmpp-entware), then: opkg install python3"
echo "  2) PaperHid/PaperWriter native-app path (installs Entware+Python as a side effect)"
echo "  3) See README 'Tablet Python for pointer'"
exit 2
"""
    out, err, code = run(c, script, timeout=30)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_install(c) -> int:
    if not DEVICE_DIR.is_dir():
        print("missing device/", file=sys.stderr)
        return 1
    # Preflight before any upload so a fresh tablet fails cleanly.
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
  echo "ERROR: need /opt/bin/python3 (Entware). Install via PaperWriter native path or rmpp-entware."
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
    """Upload skins, set cursor_style via allow-listed script, restart daemon."""
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
    run(c, f"mkdir -p {REMOTE_HOME}/ui-actions {REMOTE_HOME}/cursors")
    put_file_atomic(c, lib, f"{REMOTE_HOME}/ui-actions/lib.sh", 0o755)
    put_file_atomic(c, script, f"{REMOTE_HOME}/ui-actions/set-cursor-style.sh", 0o755)
    if cursors.is_dir():
        for path in sorted(cursors.iterdir()):
            if path.is_file() and path.name != "README.md":
                put_file_atomic(c, path, f"{REMOTE_HOME}/cursors/{path.name}", 0o644)
    put_file_atomic(
        c, DEVICE_DIR / "paperpointerd.py", f"{REMOTE_HOME}/paperpointerd.py", 0o755
    )
    out, err, code = run(
        c,
        f"sed -i 's/\\r$//' {REMOTE_HOME}/ui-actions/*.sh; "
        f"chmod 755 {REMOTE_HOME}/ui-actions/*.sh; "
        f"{REMOTE_HOME}/ui-actions/set-cursor-style.sh {style}",
        timeout=30,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


def cmd_cursor_rate(c, hz: int) -> int:
    """Transactionally deploy the daemon, change its rate, and restart it."""
    hz = max(1, min(CURSOR_RATE_MAX, int(hz)))
    candidate = f"{REMOTE_HOME}/paperpointerd.py.candidate"
    put_file_atomic(
        c,
        DEVICE_DIR / "paperpointerd.py",
        candidate,
        0o755,
    )
    script = f"""
set -eu
DAEMON={REMOTE_HOME}/paperpointerd.py
CANDIDATE={candidate}
CONF={REMOTE_HOME}/pointer.conf
OLD_DAEMON="$DAEMON.rollback.$$"
OLD_CONF="$CONF.rollback.$$"
ARMED=0

rollback() {{
  code=$?
  trap - EXIT HUP INT TERM
  set +e
  if [ "$ARMED" -eq 1 ]; then
    mv -f "$OLD_DAEMON" "$DAEMON"
    mv -f "$OLD_CONF" "$CONF"
    systemctl restart {UNIT_NAME} >/dev/null 2>&1
  fi
  rm -f "$CANDIDATE" "$OLD_DAEMON" "$OLD_CONF"
  exit "$code"
}}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[ -f "$DAEMON" ] || {{ echo 'ERROR: daemon not installed' >&2; exit 2; }}
[ -f "$CONF" ] || {{ echo 'ERROR: pointer.conf not installed' >&2; exit 2; }}
/opt/bin/python3 -c 'import runpy; n=runpy.run_path("{candidate}"); assert n["CURSOR_HZ_MAX"] >= {hz}'
cp -p "$DAEMON" "$OLD_DAEMON"
cp -p "$CONF" "$OLD_CONF"
ARMED=1
mv -f "$CANDIDATE" "$DAEMON"
chmod 0755 "$DAEMON"
if grep -q '^cursor_hz=' "$CONF"; then
  sed -i 's/^cursor_hz=.*/cursor_hz={hz}/' "$CONF"
else
  echo 'cursor_hz={hz}' >> "$CONF"
fi
systemctl restart {UNIT_NAME}
sleep 2
systemctl is-active --quiet {UNIT_NAME}
/opt/bin/python3 -c 'import runpy; n=runpy.run_path("{REMOTE_HOME}/paperpointerd.py"); assert n["load_conf"]("{REMOTE_HOME}/pointer.conf")["cursor_hz"] == {hz}'
PID=$(systemctl show -p MainPID --value {UNIT_NAME})
[ "${{PID:-0}}" -gt 0 ] && [ -d "/proc/$PID" ]

ARMED=0
rm -f "$OLD_DAEMON" "$OLD_CONF"
trap - EXIT HUP INT TERM
grep '^cursor_hz=' "$CONF"
echo "paperpointer pid=$PID"
"""
    out, err, code = run(c, script, timeout=30)
    _out(out)
    if err.strip():
        sys.stderr.write(err)
    return code


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


def cmd_enable_fb(c) -> int:
    """Load XOVI + framebuffer-spy (no AppLoad, no pp-cursor). Restarts xochitl."""
    put_tree(c, DEVICE_DIR, REMOTE_HOME, preserve_existing={"pointer.conf"})
    out, err, code = run(
        c,
        f"sed -i 's/\\r$//' {REMOTE_HOME}/enable_fb_spy.sh; "
        f"chmod 755 {REMOTE_HOME}/enable_fb_spy.sh; "
        f"{REMOTE_HOME}/enable_fb_spy.sh",
        timeout=120,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err[:2000])
    return code


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


def cmd_enable_settings_ui(c) -> int:
    """Install and canary the optional Settings > Help controls."""
    qmd = DEVICE_DIR / "paperpointer-settings.qmd"
    installer = DEVICE_DIR / "enable_settings_ui.sh"
    ui_actions = DEVICE_DIR / "ui-actions"
    cursors = DEVICE_DIR / "cursors"
    if not qmd.is_file() or not installer.is_file() or not ui_actions.is_dir():
        print("missing settings UI device files", file=sys.stderr)
        return 1

    run(c, f"mkdir -p {REMOTE_HOME}/ui-actions {REMOTE_HOME}/cursors")
    put_file_atomic(c, qmd, f"{REMOTE_HOME}/paperpointer-settings.qmd", 0o644)
    put_file_atomic(c, installer, f"{REMOTE_HOME}/enable_settings_ui.sh", 0o755)
    for path in ui_actions.iterdir():
        if path.is_file():
            put_file_atomic(c, path, f"{REMOTE_HOME}/ui-actions/{path.name}", 0o755)
    if cursors.is_dir():
        for path in sorted(cursors.iterdir()):
            if path.is_file() and path.name != "README.md":
                put_file_atomic(c, path, f"{REMOTE_HOME}/cursors/{path.name}", 0o644)

    out, err, code = run(
        c,
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
systemctl is-active --quiet {UNIT_NAME}

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
        "ERROR: Settings > Help has not loaded the PaperPointer UI "
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


def cmd_fb_config(c) -> int:
    out, err, code = run(
        c,
        f"/opt/bin/python3 {REMOTE_HOME}/fb_cursor.py config 2>&1; "
        f"echo ---; ls -la /dev/shm/pp-cursor 2>&1; "
        f"xxd /dev/shm/pp-cursor 2>/dev/null | head -n 2; "
        f"tr '\\0' '\\n' < /proc/$(ps w | grep '[x]ochitl --system' | awk '{{print $1}}' | head -n1)/environ 2>/dev/null | grep LD_PRELOAD || true",
        timeout=20,
    )
    _out(out)
    if err.strip():
        sys.stderr.write(err[:1000])
    return code


def cmd_stock_ui(c) -> int:
    """Disable the cursor transport and return to stock xochitl."""
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
if ! systemctl restart {UNIT_NAME}; then
  echo 'WARNING: PaperPointer daemon did not restart; continuing UI recovery' >&2
  WARN=1
fi
rm -f "$QMD" "$SETTINGS_QMD" "$FIFO" || WARN=1

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
if [ "$WARN" -ne 0 ]; then
  echo 'WARNING: stock UI recovered, but daemon cleanup needs attention' >&2
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
    """Kick PaperWriter-style reconnect for saved keyboard MAC(s)."""
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


# Exact pointer command inventory (pin 4174a57 + monorepo preflight).
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
    "enable-fb",
    "enable-cursor",
    "enable-settings-ui",
    "disable-settings-ui",
    "settings-ui-check",
    "fb-config",
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
        help="SSH password (preferred env: PAPERWRITER_PASSWORD; also PAPERPOINTER_PASSWORD)",
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
    if cmd == "detect":
        return cmd_detect(connection)
    if cmd == "probe":
        return cmd_probe(connection)
    if cmd == "bt-status":
        return cmd_bt_status(connection)
    if cmd == "bt-recover":
        return cmd_bt_recover(connection)
    if cmd == "install":
        return cmd_install(connection)
    if cmd == "uninstall":
        return cmd_uninstall(connection)
    if cmd == "status":
        return cmd_status(connection)
    if cmd == "restart":
        return cmd_restart(connection)
    if cmd == "cursor-rate":
        return cmd_cursor_rate(connection, args.hz)
    if cmd == "cursor-style":
        return cmd_cursor_style(connection, args.style)
    if cmd == "input-monitor":
        return cmd_input_monitor(connection, max(1, min(30, args.seconds)))
    if cmd == "watch":
        return cmd_watch(connection)
    if cmd == "reconnect":
        return cmd_reconnect(connection)
    if cmd == "test-tap":
        return cmd_test_tap(connection, args.x, args.y)
    if cmd == "enable-fb":
        return cmd_enable_fb(connection)
    if cmd == "enable-cursor":
        return cmd_enable_cursor(connection)
    if cmd == "enable-settings-ui":
        return cmd_enable_settings_ui(connection)
    if cmd == "disable-settings-ui":
        return cmd_disable_settings_ui(connection)
    if cmd == "settings-ui-check":
        return cmd_settings_ui_check(connection)
    if cmd == "fb-config":
        return cmd_fb_config(connection)
    if cmd == "stock-ui":
        return cmd_stock_ui(connection)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parent_shared = shared_flag_parser(for_subparser=False)
    p = argparse.ArgumentParser(
        prog="paperpointer",
        description="PaperHid pointer (mouse/touchpad → touch)",
        parents=[parent_shared],
    )
    p.set_defaults(host=DEFAULT_HOST, password=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    register_pointer_commands(sub, shared_flag_parser(for_subparser=True))
    return p


def main(argv=None) -> int:
    # Shared flags work before or after the subcommand:
    #   paperpointer --password X reconnect
    #   paperpointer reconnect --password X
    p = build_parser()
    args = p.parse_args(argv)
    host = getattr(args, "host", None) or DEFAULT_HOST
    password = password_from_env(getattr(args, "password", None))
    c = connect(host, password)
    try:
        return dispatch_pointer(args, c)
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
