#!/bin/sh
# Shared Paper Pro Bluetooth helpers for PaperWriter.
# Sourced by bt-keyboard.sh and bt-resume.sh — not run directly.
#
# Rules (NXP IW61x / btnxpuart):
#  - Never modprobe -r btnxpuart
#  - Never hciconfig hci0 down
#  - Restart bluetoothd only after input.conf changes (stamp), not every start
#  - Re-send PS_DISABLE after every fresh hci0 (boot and post-resume)

HOME_DIR="${HOME_DIR:-/home/root/.paperwriter}"
MAC_FILE="${MAC_FILE:-/home/root/.paperwriter-keyboard}"
CONF_STAMP="${CONF_STAMP:-$HOME_DIR/.bluez-inputconf-v1}"
LOG_FILE="${LOG_FILE:-$HOME_DIR/bt.log}"

pw_log() {
    # Keep log small; best-effort only.
    mkdir -p "$HOME_DIR" 2>/dev/null || true
    if [ -f "$LOG_FILE" ]; then
        sz=$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)
        if [ "${sz:-0}" -gt 65536 ] 2>/dev/null; then
            tail -c 16384 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && \
                mv "$LOG_FILE.tmp" "$LOG_FILE" 2>/dev/null || true
        fi
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?')] $*" >> "$LOG_FILE" 2>/dev/null || true
}

pw_hci_ready() {
    [ -d /sys/class/bluetooth/hci0 ]
}

pw_adapter_powered() {
    bluetoothctl show 2>/dev/null | grep -q "Powered: yes"
}

pw_is_connected() {
    [ -n "$1" ] || return 1
    bluetoothctl info "$1" 2>/dev/null | grep -q "Connected: yes"
}

pw_is_paired() {
    [ -n "$1" ] || return 1
    bluetoothctl info "$1" 2>/dev/null | grep -q "Paired: yes"
}

pw_load_modules() {
    modprobe bluetooth 2>/dev/null || true
    if ! lsmod | grep -q '^btnxpuart'; then
        modprobe btnxpuart 2>/dev/null || true
    fi
    modprobe uhid 2>/dev/null || true
}

pw_wait_hci() {
    # $1 = max seconds (default 45)
    max="${1:-45}"
    i=0
    while [ "$i" -lt "$max" ]; do
        if pw_hci_ready; then
            return 0
        fi
        # Gentle re-probe a few times; never -r
        if [ "$i" -eq 3 ] || [ "$i" -eq 10 ] || [ "$i" -eq 20 ]; then
            modprobe btnxpuart 2>/dev/null || true
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

pw_wake_lock() {
    echo paperwriter.bt >> /sys/power/wake_lock 2>/dev/null || true
    echo user.lock >> /sys/power/wake_lock 2>/dev/null || true
}

pw_runtime_pm_on() {
    DEV=$(readlink -f /sys/class/bluetooth/hci0 2>/dev/null)
    i=0
    while [ -n "$DEV" ] && [ "$DEV" != "/" ] && [ "$i" -lt 14 ]; do
        [ -f "$DEV/power/control" ] && echo on > "$DEV/power/control" 2>/dev/null
        [ -f "$DEV/power/autosuspend_delay_ms" ] && echo -1 > "$DEV/power/autosuspend_delay_ms" 2>/dev/null
        DEV=$(dirname "$DEV")
        i=$((i + 1))
    done
    for p in /sys/class/bluetooth/hci0/device /sys/class/bluetooth/hci0/device/device; do
        [ -f "$p/power/control" ] && echo on > "$p/power/control" 2>/dev/null
        [ -f "$p/power/autosuspend_delay_ms" ] && echo -1 > "$p/power/autosuspend_delay_ms" 2>/dev/null
    done
}

pw_fix_bluez_conf() {
    # bluetoothd wants ConfigurationDirectoryMode=555.
    chmod 555 /etc/bluetooth 2>/dev/null || true

    need_bt_restart=0
    if [ -f /etc/bluetooth/input.conf ]; then
        if ! grep -q '^UserspaceHID=true' /etc/bluetooth/input.conf 2>/dev/null \
            || ! grep -q '^ClassicBondedOnly=false' /etc/bluetooth/input.conf 2>/dev/null \
            || [ ! -f "$CONF_STAMP" ]; then
            sed -i '/^[# ]*UserspaceHID/d' /etc/bluetooth/input.conf
            echo 'UserspaceHID=true' >> /etc/bluetooth/input.conf
            sed -i '/^[# ]*ClassicBondedOnly/d' /etc/bluetooth/input.conf
            echo 'ClassicBondedOnly=false' >> /etc/bluetooth/input.conf
            need_bt_restart=1
        fi
    fi
    if [ -f /etc/bluetooth/main.conf ]; then
        # Privacy causes BLE connect abort on NXP; strip it if present
        sed -i '/^[# ]*Privacy/d' /etc/bluetooth/main.conf 2>/dev/null || true
    fi

    if [ "$need_bt_restart" -eq 1 ]; then
        systemctl restart bluetooth 2>/dev/null || systemctl start bluetooth 2>/dev/null || true
        touch "$CONF_STAMP" 2>/dev/null || true
        sleep 2
        return 0
    fi

    if ! systemctl is-active bluetooth >/dev/null 2>&1; then
        systemctl start bluetooth 2>/dev/null || true
        sleep 2
    fi
}

pw_ps_disable() {
    # NXP IW61x: disable controller auto-sleep (HCI 0xFC23 / BT_PS_DISABLE).
    # Without this, LE scan/connect HCI cmds time out (-110) after ps_state.
    # Safe no-op on controllers that ignore the vendor opcode.
    (
        hcitool cmd 0x3f 0x23 0x03 0x00 0x00 >/dev/null 2>&1
    ) &
    CPID=$!
    sleep 2
    kill $CPID 2>/dev/null
    wait $CPID 2>/dev/null
    true
}

pw_power_on() {
    # $1 = max attempts (default 12)
    max="${1:-12}"
    i=0
    while [ "$i" -lt "$max" ]; do
        if pw_adapter_powered; then
            bluetoothctl pairable on 2>/dev/null || true
            return 0
        fi
        bluetoothctl power on 2>/dev/null || true
        sleep 2
        i=$((i + 1))
    done
    bluetoothctl pairable on 2>/dev/null || true
    pw_adapter_powered
}

pw_ensure_bluetoothd() {
    # If bluez started before hci existed, start again (not restart).
    if ! bluetoothctl list 2>/dev/null | grep -q Controller; then
        systemctl start bluetooth 2>/dev/null || true
        sleep 2
    fi
}

pw_init_adapter() {
    # Full bring-up for a (possibly fresh) hci0. Idempotent.
    # $1 = hci wait seconds (default 45)
    wait_s="${1:-45}"

    pw_load_modules
    if ! pw_wait_hci "$wait_s"; then
        pw_log "init: hci0 not ready after ${wait_s}s"
        return 1
    fi

    pw_wake_lock
    pw_runtime_pm_on
    pw_fix_bluez_conf
    pw_ensure_bluetoothd
    pw_power_on 12
    pw_ps_disable

    if pw_adapter_powered; then
        pw_log "init: adapter powered"
        return 0
    fi
    pw_log "init: adapter still not powered"
    return 1
}

pw_pair_unbonded() {
    MAC="$1"
    [ -n "$MAC" ] || return 1
    (echo "scan on"; sleep 6; echo "scan off") | bluetoothctl >/dev/null 2>&1
    sleep 1
    bluetoothctl <<EOF
agent NoInputNoOutput
default-agent
pair $MAC
EOF
    sleep 2
}

pw_ensure_connected() {
    MAC="$1"
    [ -n "$MAC" ] || return 1
    pw_is_connected "$MAC" && return 0
    bluetoothctl trust "$MAC" >/dev/null 2>&1
    if ! pw_is_paired "$MAC"; then
        pw_pair_unbonded "$MAC"
    else
        # BLE keyboards often need a short discovery window before connect
        # succeeds after deep sleep (device must be advertising).
        (echo "scan on"; sleep 4; echo "scan off") | bluetoothctl >/dev/null 2>&1 &
        SPID=$!
        sleep 1
    fi
    bluetoothctl connect "$MAC" >/dev/null 2>&1 &
    CPID=$!
    sleep 6
    kill $CPID 2>/dev/null
    wait $CPID 2>/dev/null
    if [ -n "${SPID:-}" ]; then
        kill $SPID 2>/dev/null
        wait $SPID 2>/dev/null
    fi
    pw_is_connected "$MAC"
}

pw_read_mac() {
    if [ -f "$MAC_FILE" ]; then
        cat "$MAC_FILE" 2>/dev/null | tr -d ' \t\r\n'
    fi
}

pw_connect_saved() {
    # $1 = attempts (default 5), $2 = sleep between (default 3)
    attempts="${1:-5}"
    gap="${2:-3}"
    MAC=$(pw_read_mac)
    [ -n "$MAC" ] || return 1
    i=0
    while [ "$i" -lt "$attempts" ]; do
        if pw_is_connected "$MAC"; then
            pw_log "connect: $MAC already connected"
            return 0
        fi
        pw_log "connect: attempt $((i + 1))/$attempts -> $MAC"
        pw_ensure_connected "$MAC" && {
            pw_log "connect: $MAC ok"
            return 0
        }
        sleep "$gap"
        i=$((i + 1))
    done
    pw_log "connect: $MAC failed after $attempts attempts"
    return 1
}
