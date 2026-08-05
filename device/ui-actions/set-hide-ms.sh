#!/bin/sh
# Set cursor_hide_ms to an allow-listed idle timeout and restart the daemon.
# Usage: set-hide-ms.sh 0|3000|10000|60000
set -eu
# shellcheck source=lib.sh
. /home/root/.paperpointer/ui-actions/lib.sh

val="${1:-}"
case "$val" in
    0|3000|10000|60000) ;;
    *)
        echo "ERROR: hide_ms must be 0, 3000, 10000, or 60000" >&2
        exit 2
        ;;
esac

set_key cursor_hide_ms "$val"
ui_log "set cursor_hide_ms=$val"
restart_daemon
printf 'ok hide_ms=%s\n' "$val"
