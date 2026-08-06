#!/bin/sh
# Thin wrapper → paperhid-ui (host CLI / legacy paths).
set -eu
exec /home/root/.paperhid/paperhid-ui status
