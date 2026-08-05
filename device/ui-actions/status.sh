#!/bin/sh
# Print a single-line status for the on-device settings panel.
# Format: hide_ms=N accel=N cursor=N service=active|inactive|failed
set -eu
# shellcheck source=lib.sh
. /home/root/.paperpointer/ui-actions/lib.sh

hide=$(get_key cursor_hide_ms 0)
accel=$(get_key accel 2.0)
cursor=$(get_key cursor 0)
style=$(get_key cursor_style cross)
if systemctl is-active --quiet paperpointer.service 2>/dev/null; then
    svc=active
elif systemctl is-failed --quiet paperpointer.service 2>/dev/null; then
    svc=failed
else
    svc=inactive
fi
printf 'hide_ms=%s accel=%s cursor=%s style=%s service=%s\n' \
    "$hide" "$accel" "$cursor" "$style" "$svc"
