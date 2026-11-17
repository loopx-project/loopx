#!/usr/bin/env bash
# Logs restart attempts; restart-fails simulates a service restart error.
echo "restart.sh $*" >> "$LOOCHAT_FAKE_STATE/restart.log"
[ ! -e "$LOOCHAT_FAKE_STATE/restart-fails" ]
