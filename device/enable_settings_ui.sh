#!/bin/sh
# Install the optional Settings > Help PaperHid controls independently of
# the cursor patch. Any failed canary restores the previous QMD and XOVI state.
set -eu

SUPPORTED_VERSION="3.28.0.164"
XOVI="/home/root/xovi"
EXT="$XOVI/extensions.d"
QMD_HOME="$XOVI/exthome/qt-resource-rebuilder"
HOME_PP="/home/root/.paperpointer"
HOME_PH="/home/root/.paperhid"
UI_BIN="$HOME_PH/paperhid-ui"
QMD_SOURCE="$HOME_PP/paperpointer-settings.qmd"
QMD_TARGET="$QMD_HOME/paperpointer-settings.qmd"
QMD_BACKUP="$QMD_HOME/.paperpointer-settings.qmd.rollback.$$"
CURSOR_QMD="$QMD_HOME/paperpointer-cursor.qmd"
CURSOR_PIPE="$HOME_PP/cursor.fifo"
HAD_QMD=0
ARMED=0

fail() {
    code="$1"
    shift
    echo "ERROR: $*" >&2
    exit "$code"
}

xochitl_pid() {
    set -- $(pidof xochitl 2>/dev/null || true)
    [ "$#" -gt 0 ] && printf '%s\n' "$1"
}

xochitl_has_extensions() {
    pid="$1"
    [ -n "$pid" ] && [ -p /run/xovi-mb ] &&
        grep -q 'qt-resource-rebuilder.so' "/proc/$pid/maps" 2>/dev/null &&
        grep -q 'xovi-message-broker.so' "/proc/$pid/maps" 2>/dev/null &&
        grep -q 'qt-command-executor.so' "/proc/$pid/maps" 2>/dev/null
}

# XOVI deliberately restarts xochitl. A changed PID is therefore not a
# failure; accept the first mapped instance that remains stable for the
# requested window, and only then run the cursor canary.
wait_for_stable_xochitl() {
    stable_for="$1"
    timeout="$2"
    candidate=""
    stable=0
    elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        pid=$(xochitl_pid)
        if xochitl_has_extensions "$pid"; then
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
            candidate=""
            stable=0
        fi
        elapsed=$((elapsed + 1))
        sleep 1
    done
    return 1
}

start_xovi() {
    # A prior failed Xochitl launch can trip systemd's rate limiter.
    systemctl reset-failed xochitl.service 2>/dev/null || true
    "$XOVI/start"
}

rollback() {
    code="$1"
    trap - EXIT HUP INT TERM
    set +e
    if [ "$ARMED" -eq 1 ]; then
        echo "rolling back PaperHid settings UI" >&2
        rm -f "$QMD_TARGET"
        if [ "$HAD_QMD" -eq 1 ] && [ -f "$QMD_BACKUP" ]; then
            mv -f "$QMD_BACKUP" "$QMD_TARGET"
        fi
        systemctl reset-failed xochitl.service 2>/dev/null || true
        "$XOVI/start" >/tmp/paperpointer-settings-rollback.log 2>&1 || true
    fi
    rm -f "$QMD_BACKUP"
    exit "$code"
}

trap 'rollback $?' EXIT
trap 'rollback 129' HUP
trap 'rollback 130' INT
trap 'rollback 143' TERM

IMAGE_VERSION=$(sed -n 's/^IMG_VERSION="\{0,1\}\([^" ]*\)"\{0,1\}$/\1/p' /etc/os-release | head -n 1)
[ "$IMAGE_VERSION" = "$SUPPORTED_VERSION" ] ||
    fail 10 "settings QML supports $SUPPORTED_VERSION; tablet is ${IMAGE_VERSION:-unknown}"
[ -x "$XOVI/start" ] || fail 11 "XOVI is not installed at $XOVI"
[ -f "$EXT/qt-resource-rebuilder.so" ] || fail 12 "qt-resource-rebuilder.so is not active"
[ -f "$EXT/xovi-message-broker.so" ] || fail 13 "xovi-message-broker.so is not active"
[ -f "$EXT/qt-command-executor.so" ] || fail 14 "qt-command-executor.so is not active"
[ -f "$QMD_SOURCE" ] || fail 15 "missing $QMD_SOURCE"
[ -x "$UI_BIN" ] || fail 16 "missing executable $UI_BIN (PaperHid UI helper)"

mkdir -p "$QMD_HOME"
if [ -f "$QMD_TARGET" ]; then
    cp -p "$QMD_TARGET" "$QMD_BACKUP"
    HAD_QMD=1
fi
ARMED=1

QMD_TMP="$QMD_HOME/.paperpointer-settings.qmd.tmp.$$"
cp "$QMD_SOURCE" "$QMD_TMP"
chmod 0644 "$QMD_TMP"
mv -f "$QMD_TMP" "$QMD_TARGET"

echo "starting XOVI with optional PaperHid Settings > Help UI"
start_xovi || fail 20 "XOVI start failed"
wait_for_stable_xochitl 10 60 ||
    fail 21 "xochitl did not become stable with the required XOVI extensions"

# If the cursor is installed, prove the independent settings patch did not
# break MainView or its FIFO reader.
if [ -f "$CURSOR_QMD" ] && [ -p "$CURSOR_PIPE" ]; then
    printf '0.500000,0.500000,0\n' >"$CURSOR_PIPE" &
    CANARY_PID=$!
    i=0
    while kill -0 "$CANARY_PID" >/dev/null 2>&1 && [ "$i" -lt 30 ]; do
        i=$((i + 1))
        sleep 1
    done
    if kill -0 "$CANARY_PID" >/dev/null 2>&1; then
        kill "$CANARY_PID" >/dev/null 2>&1 || true
        wait "$CANARY_PID" >/dev/null 2>&1 || true
        fail 23 "cursor FIFO reader did not accept the compatibility canary"
    fi
    wait "$CANARY_PID" || fail 23 "cursor FIFO compatibility canary failed"
    /opt/bin/python3 "$HOME_PP/paperpointerd.py" cursor-ping ||
        fail 24 "cursor health check failed with settings UI installed"
fi

# Verify the post-canary instance, rather than comparing it with a pre-XOVI
# PID that is expected to have changed during installation.
wait_for_stable_xochitl 15 45 ||
    fail 25 "xochitl became unstable during settings canary"

# Settings must install on keyboard-only tablets. The pointer daemon is
# optional; when present, leave it alone (do not require it active).

ARMED=0
rm -f "$QMD_BACKUP"
trap - EXIT HUP INT TERM
echo "OK: settings UI installed; open Settings > Help, then run settings-ui-check"
