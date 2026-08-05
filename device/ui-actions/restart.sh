#!/bin/sh
# Restart paperpointer.service only.
set -eu
# shellcheck source=lib.sh
. /home/root/.paperpointer/ui-actions/lib.sh

ui_log "restart paperpointer.service"
restart_daemon
printf 'ok service=active\n'
