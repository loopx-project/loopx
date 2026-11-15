#!/usr/bin/env bash
# Stands in for scripts/restart.sh in the repository install.sh tests clone: logs and succeeds.
echo "restart.sh $*" >> "$CALLI_FAKE_STATE/restart.log"
