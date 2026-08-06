#!/bin/sh
# Thin wrapper → paperhid-ui.
set -eu
exec /home/root/.paperhid/paperhid-ui pointer-restart
