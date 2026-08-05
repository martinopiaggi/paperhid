#!/bin/bash
# PaperHid XOVI autostart — runs on boot from paperwriter-xovi.service.
#
# Safe by design: no [Unit] deps. If /home never mounts, script exits silently
# and stock xochitl keeps running.

LOGFILE=/home/root/.paperwriter/xovi-autostart.log
ATTEMPTS_FILE=/home/root/.paperwriter/xovi-activation-attempts
MAX_ATTEMPTS=3

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOGFILE" 2>/dev/null || true
}

i=0
while [ $i -lt 20 ]; do
    if [ -d /home/root/xovi ]; then
        break
    fi
    sleep 1
    i=$((i + 1))
done

if [ ! -d /home/root/xovi ]; then
    exit 0
fi

mkdir -p /home/root/.paperwriter 2>/dev/null || true
log "autostart: found /home/root/xovi"

ATTEMPTS=$(cat "$ATTEMPTS_FILE" 2>/dev/null)
case "$ATTEMPTS" in ''|*[!0-9]*) ATTEMPTS=0 ;; esac
if [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
    log "autostart: XOVI activation failed ${ATTEMPTS}x — staying on stock (reinstall PaperHid native app to retry)"
    exit 0
fi
echo $((ATTEMPTS + 1)) > "$ATTEMPTS_FILE" 2>/dev/null || true

# Entware's /opt can shadow system glibc; unmount around xovi/start.
OPT_WAS_MOUNTED=0
if mountpoint -q /opt 2>/dev/null; then
    OPT_WAS_MOUNTED=1
    umount /opt 2>/dev/null && log "autostart: unmounted /opt"
fi

export XOVI_ROOT=/home/root/xovi
bash /home/root/xovi/start >> "$LOGFILE" 2>&1 || log "autostart: xovi/start exited non-zero"

if [ $OPT_WAS_MOUNTED -eq 1 ]; then
    mount --bind /home/root/.entware /opt 2>/dev/null && log "autostart: remounted /opt"
elif [ -d /home/root/.entware ] && [ -d /opt ]; then
    mount --bind /home/root/.entware /opt 2>/dev/null && log "autostart: mounted /opt"
fi

systemd-run --on-active=60 --collect /bin/sh -c 'NR=$(systemctl show xochitl.service -p NRestarts --value 2>/dev/null); if pidof xochitl >/dev/null 2>&1 && [ "${NR:-0}" -lt 5 ]; then echo 0 > /home/root/.paperwriter/xovi-activation-attempts; else bash /home/root/xovi/stock >/dev/null 2>&1; fi' >/dev/null 2>&1 || echo 0 > "$ATTEMPTS_FILE"

log "autostart: done"
exit 0
