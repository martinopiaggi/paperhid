#!/bin/sh
# Set cursor_style to an allow-listed skin and publish the QML status file.
# Usage: set-cursor-style.sh cross|win95
set -eu
# shellcheck source=lib.sh
. /home/root/.paperpointer/ui-actions/lib.sh

val="${1:-}"
case "$val" in
    cross|win95) ;;
    *)
        echo "ERROR: style must be cross or win95" >&2
        exit 2
        ;;
esac

if [ "$val" = "win95" ] && [ ! -f "$HOME_PP/cursors/win95.png" ]; then
    echo "ERROR: missing $HOME_PP/cursors/win95.png" >&2
    exit 3
fi

set_key cursor_style "$val"
printf '%s\n' "$val" >"$HOME_PP/cursor_style"
ui_log "set cursor_style=$val"
restart_daemon
printf 'ok style=%s\n' "$val"
