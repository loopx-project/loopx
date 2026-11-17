#!/bin/sh
set -eu
case "${1:-}" in
  a|b|reader) actor=$1; shift ;;
  *) printf '%s\n' "Usage: ./client.sh a|b|reader [read|request|recover|descriptor|ca]" >&2; exit 2 ;;
esac
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$directory"
[ "$#" -gt 0 ] || set -- read
exec docker compose run --rm --no-deps -T "client-${actor}" "$@"
