#!/bin/sh
# Thin wrapper → paperhid-ui (allow-listed style only).
set -eu
exec /home/root/.paperhid/paperhid-ui set-cursor-style "${1:-}"
