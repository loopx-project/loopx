#!/bin/sh
set -eu

usage() {
  printf '%s\n' 'Usage: ./state.sh backup /absolute/new-directory' \
    '       COMPOSE_PROJECT_NAME=new-name ./state.sh restore /absolute/backup-directory' >&2
  exit 2
}

[ "$#" -eq 2 ] || usage
operation=$1
archive=$2
case "$archive" in /*) ;; *) usage ;; esac
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$directory"
project=${COMPOSE_PROJECT_NAME:-loopx-authority}
case "$project" in ''|*[!a-z0-9_-]*) usage ;; esac
image=$(docker compose config --images | awk '/^loopx\/remote-authority:/ { print; exit }')
[ -n "$image" ] || { printf '%s\n' 'Cannot resolve the packaged gateway image' >&2; exit 1; }
volumes='pg-admin pg-auth pg-data gateway reader-gateway client-a client-b reader health'
umask 077

digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum < "$1"
  else
    shasum -a 256 < "$1"
  fi
}

case "$operation" in
  backup)
    for volume in $volumes; do
      label=$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}/{{index .Labels "com.docker.compose.volume"}}' "${project}_${volume}")
      [ "$label" = "$project/$volume" ] || { printf '%s\n' 'Unexpected Compose volume ownership' >&2; exit 1; }
    done
    docker image inspect "$image" >/dev/null
    mkdir -m 700 "$archive"
    docker compose --project-name "$project" down
    for volume in $volumes; do
      docker run --rm --pull never --read-only --network none --user 0:0 \
        --cpus 0.25 --memory 256m --pids-limit 64 --log-driver none \
        --mount "type=volume,src=${project}_${volume},dst=/state,readonly" \
        --entrypoint tar "$image" --numeric-owner -C /state -cpf - . > "$archive/$volume.tar"
      digest "$archive/$volume.tar" > "$archive/$volume.sha256"
    done
    printf '%s\n' "$image" > "$archive/COMPLETE"
    printf '%s\n' 'Backup complete. Run ./start.sh to bring this project back online.'
    ;;
  restore)
    [ -n "${COMPOSE_PROJECT_NAME:-}" ] || usage
    [ "$(cat "$archive/COMPLETE")" = "$image" ] ||
      { printf '%s\n' 'Incomplete backup or bundle version mismatch' >&2; exit 1; }
    for volume in $volumes; do
      [ "$(digest "$archive/$volume.tar")" = "$(cat "$archive/$volume.sha256")" ] ||
        { printf 'Checksum mismatch for %s\n' "$volume" >&2; exit 1; }
      if docker volume inspect "${project}_${volume}" >/dev/null 2>&1; then
        printf 'Remove or choose a different restore project for %s\n' "${project}_${volume}" >&2
        exit 1
      fi
    done
    docker compose --project-name "$project" build secrets
    for volume in $volumes; do
      docker volume create --label "com.docker.compose.project=$project" \
        --label "com.docker.compose.volume=$volume" "${project}_${volume}" >/dev/null
      docker run --rm --pull never --interactive --read-only --network none --user 0:0 \
        --cpus 0.25 --memory 256m --pids-limit 64 --log-driver none \
        --mount "type=volume,src=${project}_${volume},dst=/state" \
        --entrypoint tar "$image" --numeric-owner --same-owner --same-permissions -C /state -xpf - < "$archive/$volume.tar"
    done
    printf '%s\n' 'Restore complete. Run ./start.sh with the same COMPOSE_PROJECT_NAME.'
    ;;
  *) usage ;;
esac
