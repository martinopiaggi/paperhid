#!/bin/sh
# Thin wrapper → paperhid-ui (allow-listed accel only).
set -eu
exec /home/root/.paperhid/paperhid-ui set-accel "${1:-}"
