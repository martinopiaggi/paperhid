#!/bin/sh
# PaperHid post-resume Bluetooth re-init.
# Invoked by zz-paperwriter-bt.sh after deep sleep / hibernate.
#
# Stock sleep-wifi.sh rmmod's btnxpuart on suspend and reloads it async on
# resume. Fresh hci0 comes up powered-off with NXP auto-sleep re-enabled.
# This script waits for the new hci0, re-inits, and reconnects the saved MAC.

HOME_DIR="/home/root/.paperwriter"
MAC_FILE="/home/root/.paperwriter-keyboard"
LOG_FILE="$HOME_DIR/resume.log"
CONF_STAMP="$HOME_DIR/.bluez-inputconf-v1"
LOCK_FILE="/run/paperwriter-bt-resume.lock"

# Serialize overlapping resume hooks (suspend-then-hibernate can fire twice).
if [ -f "$LOCK_FILE" ]; then
    oldpid=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE" 2>/dev/null || true
trap 'rm -f "$LOCK_FILE" 2>/dev/null || true' EXIT INT TERM

# shellcheck source=/dev/null
. "$HOME_DIR/bt-lib.sh"

# Override log target for resume-specific trail.
LOG_FILE="$HOME_DIR/resume.log"

pw_log "resume: start"

# Stock async-after may still be modprobing; give it a head start then wait.
sleep 2
pw_load_modules

if ! pw_init_adapter 40; then
    pw_log "resume: adapter init failed"
    # Still try connect later if hci appears
    if ! pw_wait_hci 20; then
        pw_log "resume: no hci0 — giving up"
        exit 0
    fi
    pw_wake_lock
    pw_runtime_pm_on
    pw_fix_bluez_conf
    pw_ensure_bluetoothd
    pw_power_on 8
    pw_ps_disable
fi

pw_connect_saved 6 3
rc=$?
pw_log "resume: done rc=$rc"
exit 0
