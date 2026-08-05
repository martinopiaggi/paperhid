#!/bin/sh
# PaperHid Bluetooth keyboard setup & monitor
# /home/root/.paperwriter/bt-keyboard.sh — reMarkable Paper Pro (NXP btnxpuart)
#
# Critical Paper Pro rules:
#  - Do NOT modprobe -r btnxpuart (wedges UART firmware)
#  - Do NOT hciconfig hci0 down (cannot re-up without reboot)
#  - Do NOT systemctl restart bluetooth on every service start (leaves
#    bluetoothd "deactivating" and kills discovery). Restart at most once
#    after writing input.conf (stamp file).
#  - Gate WiFi (same IW61x combo) briefly only from the host scan path,
#    not in this long-running reconnect loop.
#  - After deep sleep, stock sleep-wifi.sh destroys hci0; re-init when the
#    adapter disappears or is powered off (see also bt-resume.sh).

HOME_DIR="/home/root/.paperwriter"
MAC_FILE="/home/root/.paperwriter-keyboard"
RECONNECT_INTERVAL=10
CONF_STAMP="$HOME_DIR/.bluez-inputconf-v1"
LOG_FILE="$HOME_DIR/bt.log"
UNIT_NAME="remarkable-bt-keyboard.service"
UNIT_HOME="$HOME_DIR/$UNIT_NAME"
UNIT_ETC="/etc/systemd/system/$UNIT_NAME"
UNIT_USR="/usr/lib/systemd/system/$UNIT_NAME"
BOOTSTRAP_NAME="paperwriter-bt-bootstrap.service"
BOOTSTRAP_HOME="$HOME_DIR/$BOOTSTRAP_NAME"
BOOTSTRAP_ETC="/etc/systemd/system/$BOOTSTRAP_NAME"
BOOTSTRAP_USR="/usr/lib/systemd/system/$BOOTSTRAP_NAME"
SLEEP_HOOK_NAME="zz-paperwriter-bt.sh"
SLEEP_HOOK_HOME="$HOME_DIR/$SLEEP_HOOK_NAME"
SLEEP_HOOK_USR="/usr/lib/systemd/system-sleep/$SLEEP_HOOK_NAME"

# ── Re-seed units / sleep hook from home if wiped ───────────
_seed_unit() {
    src="$1"
    etc="$2"
    usr="$3"
    [ -f "$src" ] || return 0
    if [ ! -f "$etc" ]; then
        cp "$src" "$etc" 2>/dev/null || true
    fi
    if [ ! -f "$usr" ]; then
        if mount -o remount,rw / 2>/dev/null; then
            mkdir -p "$(dirname "$usr")" 2>/dev/null || true
            cp "$src" "$usr" 2>/dev/null || true
            if echo "$usr" | grep -q '\.service$'; then
                mkdir -p /usr/lib/systemd/system/multi-user.target.wants 2>/dev/null || true
                ln -sf "$usr" "/usr/lib/systemd/system/multi-user.target.wants/$(basename "$usr")" 2>/dev/null || true
            fi
            sync
            mount -o remount,ro / 2>/dev/null || true
        fi
    fi
}

_seed_unit "$UNIT_HOME" "$UNIT_ETC" "$UNIT_USR"
_seed_unit "$BOOTSTRAP_HOME" "$BOOTSTRAP_ETC" "$BOOTSTRAP_USR"

# Sleep hook is a script, not a unit — seed /usr only.
if [ -f "$SLEEP_HOOK_HOME" ] && [ ! -f "$SLEEP_HOOK_USR" ]; then
    if mount -o remount,rw / 2>/dev/null; then
        mkdir -p /usr/lib/systemd/system-sleep 2>/dev/null || true
        cp "$SLEEP_HOOK_HOME" "$SLEEP_HOOK_USR" 2>/dev/null || true
        chmod 755 "$SLEEP_HOOK_USR" 2>/dev/null || true
        sync
        mount -o remount,ro / 2>/dev/null || true
    fi
fi

# ── Shared helpers ──────────────────────────────────────────
# shellcheck source=/dev/null
. "$HOME_DIR/bt-lib.sh"

pw_log "bt-keyboard: start"

# ── Initial bring-up ────────────────────────────────────────
if ! pw_init_adapter 45; then
    pw_log "bt-keyboard: initial adapter init failed — will retry in loop"
fi

# First-connect burst for saved keyboard
pw_connect_saved 8 3 || true

# ── Reconnect loop (also heals post-resume if sleep hook missed) ──
while true; do
    sleep "$RECONNECT_INTERVAL"

    if ! pw_hci_ready; then
        pw_log "loop: hci0 missing — re-init"
        pw_load_modules
        pw_init_adapter 30 || continue
    elif ! pw_adapter_powered; then
        pw_log "loop: adapter not powered — re-init"
        pw_wake_lock
        pw_runtime_pm_on
        pw_fix_bluez_conf
        pw_ensure_bluetoothd
        pw_power_on 6
        pw_ps_disable
    fi

    MAC=$(pw_read_mac)
    if [ -n "$MAC" ]; then
        pw_ensure_connected "$MAC" || true
    fi
done
