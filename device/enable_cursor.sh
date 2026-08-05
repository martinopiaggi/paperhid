#!/bin/sh
# Enable the version-locked QML cursor. XOVI remains deliberately tethered:
# rebooting returns the tablet to stock, as required by XOVI's safety model.
set -eu

SUPPORTED_VERSION="3.28.0.164"
XOVI="/home/root/xovi"
EXT="$XOVI/extensions.d"
INACTIVE="$XOVI/inactive-extensions"
QMD_HOME="$XOVI/exthome/qt-resource-rebuilder"
HOME_PP="/home/root/.paperpointer"
QMD_NAME="paperpointer-cursor.qmd"
QMD_TARGET="$QMD_HOME/$QMD_NAME"
# Optional settings qmd is managed by enable-settings-ui. Re-enabling the
# cursor removes it as a conservative reset; install it again only afterward.
LEGACY_SETTINGS_QMD="$QMD_HOME/paperpointer-settings.qmd"
CURSOR_PIPE="$HOME_PP/cursor.fifo"
PING_LOG="/tmp/paperpointer-cursor-ping"

ARMED=0
BROKER_ADDED=0
COMMAND_ADDED=0
CANARY_PID=""

rollback() {
    code="$1"
    trap - EXIT HUP INT TERM
    set +e
    if [ "$ARMED" -eq 1 ]; then
        echo "rolling back to stock xochitl" >&2
        if [ -n "$CANARY_PID" ]; then
            kill "$CANARY_PID" >/dev/null 2>&1 || true
            wait "$CANARY_PID" >/dev/null 2>&1 || true
        fi
        if [ -f "$HOME_PP/pointer.conf" ]; then
            sed -i 's/^cursor=.*/cursor=0/' "$HOME_PP/pointer.conf" || true
        fi
        systemctl restart paperpointer.service >/dev/null 2>&1 || true
        rm -f "$QMD_TARGET" "$LEGACY_SETTINGS_QMD" "$PING_LOG"
        if [ "$BROKER_ADDED" -eq 1 ]; then
            rm -f "$EXT/xovi-message-broker.so"
        fi
        if [ "$COMMAND_ADDED" -eq 1 ]; then
            rm -f "$EXT/qt-command-executor.so"
        fi
        if [ -x "$XOVI/stock" ]; then
            "$XOVI/stock" >/dev/null 2>&1 || true
        else
            systemctl restart xochitl >/dev/null 2>&1 || true
        fi
        rm -f "$CURSOR_PIPE"
    fi
    exit "$code"
}

fail() {
    code="$1"
    shift
    echo "ERROR: $*" >&2
    exit "$code"
}

trap 'rollback $?' EXIT
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

IMAGE_VERSION=$(sed -n 's/^IMG_VERSION="\{0,1\}\([^" ]*\)"\{0,1\}$/\1/p' /etc/os-release | head -n 1)
[ "$IMAGE_VERSION" = "$SUPPORTED_VERSION" ] ||
    fail 10 "cursor QML supports $SUPPORTED_VERSION; tablet is ${IMAGE_VERSION:-unknown}"
[ -x "$XOVI/start" ] || fail 11 "XOVI is not installed at $XOVI"
[ -f "$EXT/qt-resource-rebuilder.so" ] ||
    fail 12 "qt-resource-rebuilder.so is not active"
[ -f "$HOME_PP/$QMD_NAME" ] || fail 13 "missing $HOME_PP/$QMD_NAME"
# ui-actions are optional (on-device settings panel not shipped in this QMD).
if [ ! -f "$EXT/xovi-message-broker.so" ] &&
   [ ! -f "$INACTIVE/xovi-message-broker.so" ]; then
    fail 14 "xovi-message-broker.so is not installed"
fi
if [ ! -f "$EXT/qt-command-executor.so" ] &&
   [ ! -f "$INACTIVE/qt-command-executor.so" ]; then
    fail 15 "qt-command-executor.so is not installed"
fi
if [ -e "$CURSOR_PIPE" ] && [ ! -p "$CURSOR_PIPE" ]; then
    fail 16 "$CURSOR_PIPE exists but is not a FIFO"
fi

# All compatibility and source checks above are read-only. From this point,
# every mutation is covered by the rollback trap.
ARMED=1
mkdir -p "$EXT" "$INACTIVE" "$QMD_HOME"

if [ ! -f "$EXT/xovi-message-broker.so" ]; then
    BROKER_ADDED=1
    cp -a "$INACTIVE/xovi-message-broker.so" "$EXT/xovi-message-broker.so"
fi
if [ ! -f "$EXT/qt-command-executor.so" ]; then
    COMMAND_ADDED=1
    cp -a "$INACTIVE/qt-command-executor.so" "$EXT/qt-command-executor.so"
fi

# Never co-load the abandoned trampoline or framebuffer constructor override.
# Preserve both outside extensions.d for later forensic comparison.
if [ -f "$EXT/pp-cursor.so" ]; then
    mv -f "$EXT/pp-cursor.so" "$INACTIVE/pp-cursor.so.disabled-paperpointer"
fi
if [ -f "$EXT/framebuffer-spy.so" ]; then
    mv -f "$EXT/framebuffer-spy.so" \
        "$INACTIVE/framebuffer-spy.so.disabled-paperpointer"
fi

if [ ! -p "$CURSOR_PIPE" ]; then
    mkfifo "$CURSOR_PIPE"
fi
chmod 0600 "$CURSOR_PIPE"

QMD_TMP="$QMD_HOME/.$QMD_NAME.tmp.$$"
cp "$HOME_PP/$QMD_NAME" "$QMD_TMP"
chmod 0644 "$QMD_TMP"
mv -f "$QMD_TMP" "$QMD_TARGET"
# Never leave the experimental settings patch that crash-looped MainView.
rm -f "$LEGACY_SETTINGS_QMD"

echo "starting tethered XOVI cursor for xochitl $IMAGE_VERSION"
"$XOVI/start" || fail 20 "XOVI start failed"

i=0
XO=""
while [ "$i" -lt 20 ]; do
    XO=$(pidof xochitl 2>/dev/null | awk '{print $1}')
    if [ -n "$XO" ] && [ -p /run/xovi-mb ] &&
       grep -q 'qt-resource-rebuilder.so' "/proc/$XO/maps" 2>/dev/null &&
       grep -q 'xovi-message-broker.so' "/proc/$XO/maps" 2>/dev/null &&
       grep -q 'qt-command-executor.so' "/proc/$XO/maps" 2>/dev/null; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
[ -n "$XO" ] || fail 21 "xochitl did not start"
[ -p /run/xovi-mb ] || fail 22 "XOVI message broker did not start"
grep -q 'qt-resource-rebuilder.so' "/proc/$XO/maps" 2>/dev/null ||
    fail 23 "Qt resource rebuilder is not mapped"
grep -q 'xovi-message-broker.so' "/proc/$XO/maps" 2>/dev/null ||
    fail 24 "XOVI message broker is not mapped"
grep -q 'qt-command-executor.so' "/proc/$XO/maps" 2>/dev/null ||
    fail 25 "Qt command executor is not mapped"

# Exercise the persistent FIFO and QProcess path with an invisible packet.
# MainView construction takes about 20 seconds on Paper Pro, even though all
# native extension maps appear much earlier. Keep the writer bounded at 30s.
printf '0.500000,0.500000,0\n' > "$CURSOR_PIPE" &
CANARY_PID=$!
i=0
while kill -0 "$CANARY_PID" >/dev/null 2>&1 && [ "$i" -lt 30 ]; do
    i=$((i + 1))
    sleep 1
done
if kill -0 "$CANARY_PID" >/dev/null 2>&1; then
    kill "$CANARY_PID" >/dev/null 2>&1 || true
    wait "$CANARY_PID" >/dev/null 2>&1 || true
    CANARY_PID=""
    fail 26 "QML cursor FIFO reader did not accept the canary"
fi
wait "$CANARY_PID" || fail 26 "cursor FIFO canary write failed"
CANARY_PID=""

# The broker is safe for this bounded health check, but never for motion: its
# upstream input loop leaks one xochitl descriptor per command.
i=0
while [ "$i" -lt 5 ]; do
    if /opt/bin/python3 "$HOME_PP/paperpointerd.py" cursor-ping >"$PING_LOG" 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$i" -ge 5 ]; then
    cat "$PING_LOG" >&2 2>/dev/null || true
    fail 27 "cursor QML health check failed"
fi

XO_CHECK=$(pidof xochitl 2>/dev/null | awk '{print $1}')
[ "$XO" = "$XO_CHECK" ] || fail 28 "xochitl restarted during the canary"

if grep -q '^cursor=' "$HOME_PP/pointer.conf"; then
    sed -i 's/^cursor=.*/cursor=1/' "$HOME_PP/pointer.conf" ||
        fail 29 "could not enable cursor in pointer.conf"
else
    echo 'cursor=1' >> "$HOME_PP/pointer.conf" ||
        fail 29 "could not enable cursor in pointer.conf"
fi
systemctl restart paperpointer.service || fail 30 "paperpointer restart failed"

i=0
while [ "$i" -lt 10 ]; do
    if systemctl is-active --quiet paperpointer.service &&
       ps w | grep -q '[c]at /home/root/.paperpointer/cursor.fifo'; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
systemctl is-active --quiet paperpointer.service ||
    fail 31 "paperpointer service did not stay active"
ps w | grep -q '[c]at /home/root/.paperpointer/cursor.fifo' ||
    fail 32 "QML cursor FIFO reader did not start"

XO_CHECK=$(pidof xochitl 2>/dev/null | awk '{print $1}')
[ "$XO" = "$XO_CHECK" ] || fail 33 "xochitl restarted after cursor startup"

ARMED=0
rm -f "$PING_LOG"
trap - EXIT HUP INT TERM
echo "OK: QML cursor ready (xochitl pid $XO)"
echo "Recovery from host: python -m paperpointer.cli stock-ui"
