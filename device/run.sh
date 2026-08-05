#!/bin/sh
# Prefer entware python; fall back to busybox-unfriendly paths.
export HOME=/home/root
DIR=/home/root/.paperpointer
LOG=$DIR/pointer.log

mkdir -p "$DIR"
modprobe uinput 2>/dev/null || true

if [ ! -x /opt/bin/python3 ] && [ -x /home/root/.entware/bin/python3 ]; then
    mkdir -p /opt
    mountpoint -q /opt || mount --bind /home/root/.entware /opt 2>/dev/null || true
fi

if [ -x /opt/bin/python3 ]; then
    exec /opt/bin/python3 "$DIR/paperpointerd.py"
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$DIR/paperpointerd.py"
fi

echo "[$(date)] no python3 on device" >> "$LOG"
exit 1
