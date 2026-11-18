#!/usr/bin/env bash
# Installs LooChat on this machine, or updates it when it is there already:
#
#   curl -fsSL https://raw.githubusercontent.com/loopx-project/loopx/main/apps/workbench/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --dry-run      only print what it would do
#
# It clones the code to ~/.local/share/loochat, builds it, writes ~/.config/loochat/.env (a random
# token; data under ~/.local/state/loochat; your home as the working directory), runs it as a
# systemd user service and prints the address and token. Run again to update: the .env, the
# token and the data stay. Agents (Claude Code, Codex, ...) are installed and signed in apart.
#
#   --dry-run      print the steps, change nothing
#   --no-service   do not install the service; print how to start it by hand
#   --uninstall    stop and remove the service and the code; the .env and the data stay
#
# LOOCHAT_REPO (git URL or path), LOOCHAT_REF (branch, tag or commit; default main) and
# LOOCHAT_HOME (checkout directory) override the defaults. Core is installed separately.
set -euo pipefail

REPO_URL="${LOOCHAT_REPO:-https://github.com/loopx-project/loopx.git}"
REF="${LOOCHAT_REF:-main}"
INSTALL_DIR="${LOOCHAT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/loochat}"
APP_DIR="$INSTALL_DIR/apps/workbench"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/loochat/.env"
DATA_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/loochat/data"
NAME=loochat

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
  step "Uninstalling LooChat"
  # Also recognize installations made before Workbench moved into the monorepo.
  REMOVE_APP="$APP_DIR"
  [ -d "$REMOVE_APP" ] || REMOVE_APP="$INSTALL_DIR"
  if service_installed && same_dir "$(service_dir)" "$REMOVE_APP"; then
    run "$REMOVE_APP/scripts/install-service.sh" --remove
  elif service_installed; then
    say "The $NAME service runs $(service_dir), not $APP_DIR; left as it is."
  fi
  if [ -d "$INSTALL_DIR" ]; then run rm -rf "$INSTALL_DIR"; fi
  say "Kept your settings and threads: $ENV_FILE and $DATA_DIR. Delete them yourself to remove everything."
  exit 0
fi

step "Checking what LooChat needs"
command -v git >/dev/null 2>&1 || die "git is not installed."
command -v node >/dev/null 2>&1 || die "Node.js 22 or newer is needed and none is installed. See https://nodejs.org/en/download (or use nvm), then run this again."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 22 ] || die "Node.js 22 or newer is needed; this is $(node -v). Update it (https://nodejs.org/en/download or nvm), then run this again."
command -v npm >/dev/null 2>&1 || die "npm is not installed (it comes with Node.js)."
say "git, Node.js $(node -v) and npm are here."

case "$REF" in -*) die "LOOCHAT_REF must be a branch, tag or commit, not an option." ;; esac
UPDATE=0
step "Getting $REF from $REPO_URL into $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  UPDATE=1
  [ "$(git -C "$INSTALL_DIR" remote get-url origin)" = "$REPO_URL" ] \
    || die "$INSTALL_DIR has a different origin. Use its LOOCHAT_REPO, or choose a new LOOCHAT_HOME; settings and data can be reused."
  # Someone's own edits or commits there are theirs: stop instead of overwriting them.
  [ -z "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ] \
    || die "$INSTALL_DIR has local changes. Save them elsewhere or discard them, then run this again."
  [ ! -f "$INSTALL_DIR/.git/loochat-installed-revision" ] || \
    [ "$(git -C "$INSTALL_DIR" rev-parse HEAD)" = "$(cat "$INSTALL_DIR/.git/loochat-installed-revision")" ] \
    || die "$INSTALL_DIR has local commits. Keep them in another checkout before updating."
elif [ -e "$INSTALL_DIR" ]; then
  die "$INSTALL_DIR exists and is not a Workbench checkout. Move it away, or set LOOCHAT_HOME to another directory."
else
  run mkdir -p "$(dirname "$INSTALL_DIR")"
  run git clone --filter=blob:none --depth 1 --no-checkout "$REPO_URL" "$INSTALL_DIR"
fi
run git -C "$INSTALL_DIR" sparse-checkout set apps/workbench
run git -C "$INSTALL_DIR" fetch --depth 1 origin "$REF"
if [ "$DRY" != 1 ]; then
  git -C "$INSTALL_DIR" cat-file -e FETCH_HEAD:apps/workbench/package.json 2>/dev/null \
    || die "$REF does not contain apps/workbench/package.json. Choose a Workbench ref with LOOCHAT_REF."
fi
run git -C "$INSTALL_DIR" checkout --detach FETCH_HEAD
if [ "$DRY" != 1 ]; then
  REVISION="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  printf '%s\n' "$REVISION" > "$INSTALL_DIR/.git/loochat-installed-revision"
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
    --set "LOOCHAT_DATA_DIR=$DATA_DIR" --set "LOOCHAT_WORKDIRS=$HOME" \
    --set "LOOCHAT_LOOPX_CORE_URL=${LOOCHAT_LOOPX_CORE_URL:-http://127.0.0.1:8792}"
  [ "$DRY" = 1 ] || say "Created it: a random token, threads in $DATA_DIR, agents working in $HOME (LOOCHAT_WORKDIRS)."
fi

# Empty while there is no .env yet (a dry run).
env_get() { [ -f "$ENV_FILE" ] || return 0; sed -n "s/^LOOCHAT_$1=//p" "$ENV_FILE" | tail -1 | sed -E "s/^['\"]|['\"]$//g"; }
HOST="$(env_get HOST)"; HOST="${HOST:-127.0.0.1}"
PORT="$(env_get PORT)"; PORT="${PORT:-8787}"
case "$HOST" in 0.0.0.0 | ::) URL_HOST=127.0.0.1 ;; *) URL_HOST="$HOST" ;; esac
URL="http://$URL_HOST:$PORT/"

STARTED=0
if [ "$SERVICE" = 1 ] && ! has_systemd; then
  say ""
  say "No systemd user session here, so LooChat is not set up to run in the background."
  SERVICE=0
fi
if [ "$SERVICE" = 1 ]; then
  if service_installed && ! same_dir "$(service_dir)" "$APP_DIR"; then
    step "Service"
    say "A $NAME service already runs $(service_dir); it was not changed."
  elif [ "$UPDATE" = 1 ] && service_installed; then
    step "Restarting the service once no thread is running"
    run "$APP_DIR/scripts/restart.sh" --no-build
    STARTED=1
  else
    step "Running LooChat as a service"
    run "$APP_DIR/scripts/install-service.sh" "$ENV_FILE"
    STARTED=1
  fi
fi

[ "$DRY" = 1 ] && { say ""; say "Dry run: nothing was changed."; exit 0; }

say ""
say "Installed Workbench revision: $REVISION (requested ref: $REF)"
say "Workbench directory: $APP_DIR"
if [ "$STARTED" = 1 ]; then
  say "LooChat is running: $URL"
else
  say "To start LooChat:"
  printf '  cd %q && LOOCHAT_ENV_FILE=%q npm start -w @loopx/loochat\n' "$APP_DIR" "$ENV_FILE"
  say "Then open $URL"
fi
say "Token: $(env_get TOKEN)"
say "Open the address, choose Connect hub and paste the token. To reach it from a phone, see"
say "  https://github.com/loopx-project/loopx/tree/main/apps/workbench#远程访问"
say ""
CORE_URL="$(env_get LOOPX_CORE_URL)"; CORE_URL="${CORE_URL:-http://127.0.0.1:8792}"
if node --input-type=module -e '
  try { const r = await fetch(new URL("/healthz", process.argv[1]), { signal: AbortSignal.timeout(3000) }); process.exit(r.ok ? 0 : 1); }
  catch { process.exit(1); }
' "$CORE_URL"; then
  say "LoopX Core is reachable at $CORE_URL."
else
  say "LoopX Core is not reachable at $CORE_URL; Goal features remain unavailable until it is running."
fi
say "Core and its CLI are installed separately; this sparse checkout contains Workbench only."
say "Set LOOCHAT_LOOPX_CORE_URL and, for a source Core, LOOCHAT_LOOPX_CMD / LOOCHAT_LOOPX_CWD in $ENV_FILE."
say "See $APP_DIR/docs/install-agent.md for Core setup and connection checks."
