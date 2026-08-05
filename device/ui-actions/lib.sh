#!/bin/sh
# Shared helpers for fixed PaperHid pointer UI actions. Invoked only via absolute
# script paths from the QML CommandExecutor — never with free-form shell text.
set -eu

HOME_PP="/home/root/.paperpointer"
CONF="$HOME_PP/pointer.conf"
LOG="$HOME_PP/ui-actions.log"

ui_log() {
    ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo unknown)
    echo "[$ts] $*" >>"$LOG" 2>/dev/null || true
}

ensure_conf() {
    if [ ! -f "$CONF" ]; then
        echo "ERROR: missing $CONF" >&2
        exit 10
    fi
}

set_key() {
    key="$1"
    value="$2"
    ensure_conf
    if grep -q "^${key}=" "$CONF"; then
        sed -i "s/^${key}=.*/${key}=${value}/" "$CONF"
    else
        echo "${key}=${value}" >>"$CONF"
    fi
}

restart_daemon() {
    systemctl restart paperpointer.service
    # Give systemd a moment; surface failure to the caller.
    i=0
    while [ "$i" -lt 8 ]; do
        if systemctl is-active --quiet paperpointer.service; then
            return 0
        fi
        i=$((i + 1))
        sleep 0.25
    done
    systemctl is-active --quiet paperpointer.service
}

get_key() {
    key="$1"
    default="$2"
    if [ -f "$CONF" ] && grep -q "^${key}=" "$CONF"; then
        sed -n "s/^${key}=//p" "$CONF" | head -n 1
    else
        echo "$default"
    fi
}
