#!/usr/bin/env bash
# Installs Calli on this machine, or updates it when it is there already:
#
#   curl -fsSL https://raw.githubusercontent.com/maxliux5/calli/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --dry-run      only print what it would do
#
# It clones the code to ~/.local/share/calli, builds it, writes ~/.config/calli/.env (a random
# token; data under ~/.local/state/calli; your home as the working directory), runs it as a
# systemd user service and prints the address and token. Run again to update: the .env, the
# token and the data stay. Agents (Claude Code, Codex, ...) are installed and signed in apart.
#
#   --dry-run      print the steps, change nothing
#   --no-service   do not install the service; print how to start it by hand
#   --uninstall    stop and remove the service and the code; the .env and the data stay
#
# CALLI_REPO (git URL or path) and CALLI_HOME (where the code goes) override the defaults.
set -euo pipefail

REPO_URL="${CALLI_REPO:-https://github.com/maxliux5/calli.git}"
APP_DIR="${CALLI_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/calli}"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/calli/.env"
DATA_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/calli/data"
NAME=calli

DRY=0 SERVICE=1 UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --no-service) SERVICE=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h | --help) sed -n '2,17p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
# Runs a command, or only shows it on a dry run.
run() { if [ "$DRY" = 1 ]; then printf '  $ %s\n' "$*"; else "$@"; fi; }

has_systemd() { command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; }
# The directory the installed service runs, empty when there is none.
service_dir() { systemctl --user show -p WorkingDirectory --value "$NAME" 2>/dev/null || true; }
service_installed() { has_systemd && systemctl --user cat "$NAME" >/dev/null 2>&1; }
same_dir() { [ -n "$1" ] && [ -d "$1" ] && [ -d "$2" ] && [ "$(cd "$1" && pwd -P)" = "$(cd "$2" && pwd -P)" ]; }

if [ "$UNINSTALL" = 1 ]; then
  step "Uninstalling Calli"
  if service_installed && same_dir "$(service_dir)" "$APP_DIR"; then
    run "$APP_DIR/scripts/install-service.sh" --remove
  elif service_installed; then
    say "The $NAME service runs $(service_dir), not $APP_DIR; left as it is."
  fi
  if [ -d "$APP_DIR" ]; then run rm -rf "$APP_DIR"; fi
  say "Kept your settings and threads: $ENV_FILE and $DATA_DIR. Delete them yourself to remove everything."
  exit 0
fi

step "Checking what Calli needs"
command -v git >/dev/null 2>&1 || die "git is not installed."
command -v node >/dev/null 2>&1 || die "Node.js 22 or newer is needed and none is installed. See https://nodejs.org/en/download (or use nvm), then run this again."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 22 ] || die "Node.js 22 or newer is needed; this is $(node -v). Update it (https://nodejs.org/en/download or nvm), then run this again."
command -v npm >/dev/null 2>&1 || die "npm is not installed (it comes with Node.js)."
say "git, Node.js $(node -v) and npm are here."

UPDATE=0
step "Getting the code into $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  UPDATE=1
  # Someone's own edits there are theirs: stop instead of overwriting them.
  [ -z "$(git -C "$APP_DIR" status --porcelain --untracked-files=no)" ] \
    || die "$APP_DIR has local changes. Commit or discard them, then run this again."
  run git -C "$APP_DIR" pull --ff-only
elif [ -e "$APP_DIR" ]; then
  die "$APP_DIR exists and is not a Calli checkout. Move it away, or set CALLI_HOME to another directory."
else
  run mkdir -p "$(dirname "$APP_DIR")"
  run git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

step "Installing dependencies and building"
if [ "$DRY" = 1 ]; then
  say "  $ (cd $APP_DIR && npm install && npm run build)"
else
  cd "$APP_DIR"
  # npm install, not ci: on an update the running hub keeps its modules where they are.
  if ! npm install --no-audit --no-fund; then
    say ""
    say "npm install failed. The terminal needs a native module (node-pty); without a C++ toolchain it cannot build."
    say "  Debian/Ubuntu: sudo apt install -y build-essential python3"
    say "  Fedora/RHEL:   sudo dnf install -y gcc-c++ make python3"
    say "  macOS:         xcode-select --install"
    die "Install those, then run this again."
  fi
  npm run build
fi

step "Settings in $ENV_FILE"
if [ -f "$ENV_FILE" ]; then
  say "Already there; kept as it is."
else
  run node "$APP_DIR/scripts/setup.mjs" --quiet --env "$ENV_FILE" \
    --set "CALLI_DATA_DIR=$DATA_DIR" --set "CALLI_WORKDIRS=$HOME"
  [ "$DRY" = 1 ] || say "Created it: a random token, threads in $DATA_DIR, agents working in $HOME (CALLI_WORKDIRS)."
fi

# Empty while there is no .env yet (a dry run).
env_get() { [ -f "$ENV_FILE" ] || return 0; sed -n "s/^CALLI_$1=//p" "$ENV_FILE" | tail -1 | sed -E "s/^['\"]|['\"]$//g"; }
HOST="$(env_get HOST)"; HOST="${HOST:-127.0.0.1}"
PORT="$(env_get PORT)"; PORT="${PORT:-8787}"
case "$HOST" in 0.0.0.0 | ::) URL_HOST=127.0.0.1 ;; *) URL_HOST="$HOST" ;; esac
URL="http://$URL_HOST:$PORT/"

STARTED=0
if [ "$SERVICE" = 1 ] && ! has_systemd; then
  say ""
  say "No systemd user session here, so Calli is not set up to run in the background."
  SERVICE=0
fi
if [ "$SERVICE" = 1 ]; then
  if service_installed && ! same_dir "$(service_dir)" "$APP_DIR"; then
    step "Service"
    say "A $NAME service already runs $(service_dir); it was not changed."
  elif [ "$UPDATE" = 1 ] && service_installed; then
    step "Restarting the service once no thread is running"
    run "$APP_DIR/scripts/restart.sh" --no-build && STARTED=1
  else
    step "Running Calli as a service"
    run "$APP_DIR/scripts/install-service.sh" "$ENV_FILE" && STARTED=1
  fi
fi

[ "$DRY" = 1 ] && { say ""; say "Dry run: nothing was changed."; exit 0; }

say ""
if [ "$STARTED" = 1 ]; then
  say "Calli is running: $URL"
else
  say "To start Calli:"
  say "  cd $APP_DIR && CALLI_ENV_FILE=$ENV_FILE npm start -w @calli/daemon"
  say "Then open $URL"
fi
say "Token: $(env_get TOKEN)"
say "Open the address, choose Connect hub and paste the token. To reach it from a phone, see"
say "  https://github.com/maxliux5/calli#远程访问"
