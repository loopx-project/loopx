#!/usr/bin/env bash
# Update registered nodes through their relay connection. See update-hosts.mjs.
set -euo pipefail
exec node "$(dirname "$0")/update-hosts.mjs" "$@"
