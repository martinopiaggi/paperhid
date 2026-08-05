#!/bin/sh
# PaperWriter: re-seed systemd units + sleep hook from home after wipe.
# Runs early (oneshot) so remarkable-bt-keyboard.service has a unit file again.
#
# Canonical copies live under /home/root/.paperwriter (survives reboot + OTA).
# /etc is a volatile overlay (wiped every reboot). /usr/lib survives reboot but
# not OTA — so this script also re-seeds itself into /usr when missing.

HOME_DIR="/home/root/.paperwriter"
WANTS_DIR="/usr/lib/systemd/system/multi-user.target.wants"
SLEEP_DIR="/usr/lib/systemd/system-sleep"

UNIT_NAME="remarkable-bt-keyboard.service"
BOOTSTRAP_NAME="paperwriter-bt-bootstrap.service"
SLEEP_HOOK_NAME="zz-paperwriter-bt.sh"

SCRIPT="$HOME_DIR/bt-keyboard.sh"
LIB="$HOME_DIR/bt-lib.sh"
RESUME="$HOME_DIR/bt-resume.sh"
SLEEP_HOOK_HOME="$HOME_DIR/$SLEEP_HOOK_NAME"
BOOTSTRAP_SCRIPT="$HOME_DIR/paperwriter-bt-bootstrap.sh"

seed_service() {
    name="$1"
    src="$HOME_DIR/$name"
    etc="/etc/systemd/system/$name"
    usr="/usr/lib/systemd/system/$name"
    wants="$WANTS_DIR/$name"

    [ -f "$src" ] || return 0

    if [ ! -f "$etc" ]; then
        cp "$src" "$etc" 2>/dev/null || true
    fi

    if [ ! -f "$usr" ]; then
        if mount -o remount,rw / 2>/dev/null; then
            cp "$src" "$usr" 2>/dev/null || true
            mkdir -p "$WANTS_DIR" 2>/dev/null || true
            ln -sf "$usr" "$wants" 2>/dev/null || true
            sync
            mount -o remount,ro / 2>/dev/null || true
        fi
    elif [ ! -L "$wants" ] && [ ! -f "$wants" ]; then
        if mount -o remount,rw / 2>/dev/null; then
            mkdir -p "$WANTS_DIR" 2>/dev/null || true
            ln -sf "$usr" "$wants" 2>/dev/null || true
            sync
            mount -o remount,ro / 2>/dev/null || true
        fi
    fi
}

seed_sleep_hook() {
    [ -f "$SLEEP_HOOK_HOME" ] || return 0
    dest="$SLEEP_DIR/$SLEEP_HOOK_NAME"
    if [ -f "$dest" ]; then
        # Refresh if home copy differs (best-effort)
        if cmp -s "$SLEEP_HOOK_HOME" "$dest" 2>/dev/null; then
            return 0
        fi
    fi
    if mount -o remount,rw / 2>/dev/null; then
        mkdir -p "$SLEEP_DIR" 2>/dev/null || true
        cp "$SLEEP_HOOK_HOME" "$dest" 2>/dev/null || true
        chmod 755 "$dest" 2>/dev/null || true
        sync
        mount -o remount,ro / 2>/dev/null || true
    fi
}

# Nothing to do without the long-running script.
[ -f "$SCRIPT" ] || exit 0

chmod +x "$SCRIPT" 2>/dev/null || true
[ -f "$LIB" ] && chmod +x "$LIB" 2>/dev/null || true
[ -f "$RESUME" ] && chmod +x "$RESUME" 2>/dev/null || true
[ -f "$SLEEP_HOOK_HOME" ] && chmod +x "$SLEEP_HOOK_HOME" 2>/dev/null || true
[ -f "$BOOTSTRAP_SCRIPT" ] && chmod +x "$BOOTSTRAP_SCRIPT" 2>/dev/null || true

# Keyboard unit + bootstrap unit (self-heal: bootstrap seeds itself too)
seed_service "$UNIT_NAME"
seed_service "$BOOTSTRAP_NAME"
seed_sleep_hook

systemctl daemon-reload 2>/dev/null || true
systemctl enable "$BOOTSTRAP_NAME" 2>/dev/null || true
systemctl enable "$UNIT_NAME" 2>/dev/null || true
systemctl start "$UNIT_NAME" 2>/dev/null || true

exit 0
