#!/bin/sh
# Thin wrapper → paperhid-ui (allow-listed hide_ms only).
set -eu
exec /home/root/.paperhid/paperhid-ui set-hide-ms "${1:-}"
