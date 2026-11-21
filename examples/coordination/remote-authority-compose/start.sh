#!/bin/sh
set -eu
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$directory"
started=$(date +%s)
docker compose pull --policy missing db
pulled=$(date +%s)
docker compose build secrets
built=$(date +%s)
docker compose up --detach --no-build --wait --wait-timeout 120
ready=$(date +%s)
printf 'LoopX ready at https://127.0.0.1:%s (DB image %ss, build %ss, startup %ss, total %ss)\n' \
  "${LOOPX_PORT:-8443}" "$((pulled - started))" "$((built - pulled))" "$((ready - built))" "$((ready - started))"
printf 'Inspect the starter Todo with ./client.sh a read\n'
printf 'Scoped reads: ./client.sh reader read (https://127.0.0.1:%s)\n' "${LOOPX_READ_PORT:-8444}"
