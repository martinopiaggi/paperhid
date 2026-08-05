#!/bin/sh
# PaperWriter system-sleep hook — re-init BT keyboard after resume.
# Installed to /usr/lib/systemd/system-sleep/zz-paperwriter-bt.sh
# Name sorts after stock sleep-wifi.sh so btnxpuart reload is already queued.
#
# Do not block resume: hand off to a detached oneshot.

HOME_DIR="/home/root/.paperwriter"
RESUME="$HOME_DIR/bt-resume.sh"

case "$1" in
after)
    if [ -x "$RESUME" ]; then
        # systemd-run detaches cleanly; fall back to background sh if missing.
        if command -v systemd-run >/dev/null 2>&1; then
            systemd-run --unit=paperwriter-bt-resume --collect \
                /bin/sh "$RESUME" >/dev/null 2>&1 || \
                /bin/sh "$RESUME" >/dev/null 2>&1 &
        else
            /bin/sh "$RESUME" >/dev/null 2>&1 &
        fi
    fi
    ;;
esac
exit 0
