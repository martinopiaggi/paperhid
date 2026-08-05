#!/bin/sh
# Set pointer accel to an allow-listed value and restart the daemon.
# Usage: set-accel.sh 1.0|2.0|3.0|4.0
set -eu
# shellcheck source=lib.sh
. /home/root/.paperpointer/ui-actions/lib.sh

val="${1:-}"
case "$val" in
    1.0|2.0|3.0|4.0) ;;
    *)
        echo "ERROR: accel must be 1.0, 2.0, 3.0, or 4.0" >&2
        exit 2
        ;;
esac

set_key accel "$val"
ui_log "set accel=$val"
restart_daemon
printf 'ok accel=%s\n' "$val"
