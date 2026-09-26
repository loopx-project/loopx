#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="${LOOPX_REPO_ROOT:-$(cd "$script_dir/.." && pwd)}"
bin_dir="${LOOPX_BIN_DIR:-$HOME/.local/bin}"
registry_override="${LOOPX_GLOBAL_REGISTRY:-}"
status_port="${LOOPX_STATUS_PORT:-8766}"
status_limit="${LOOPX_STATUS_LIMIT:-80}"
status_contract_min_version="${LOOPX_STATUS_CONTRACT_MIN_VERSION:-2}"
chat_port="${LOOPX_CHAT_PORT:-8767}"
host="${LOOPX_DASHBOARD_HOST:-127.0.0.1}"
chat_runtime_endpoint="$host:$chat_port"
label_prefix="${LOOPX_LAUNCH_LABEL_PREFIX:-com.loopx}"

uid="$(id -u)"
launch_agents_dir="$HOME/Library/LaunchAgents"
logs_dir="$HOME/Library/Logs/loopx"
status_label="$label_prefix.status"
chat_label="$label_prefix.chat"
status_plist="$launch_agents_dir/$status_label.plist"
chat_plist="$launch_agents_dir/$chat_label.plist"
control_plane_write_api_enabled=false

usage() {
  cat <<EOF
Usage: $0 [--enable-control-plane-write-api] install|uninstall|start|stop|restart|status

Installs user-level macOS LaunchAgents for:
  - LoopX global status feed: http://$host:$status_port/status.json
  - LoopX Chat and Lark:      http://$host:$chat_port/

Default mode is read-only for control-plane settings. Pass
--enable-control-plane-write-api with install or restart to write that explicit
opt-in flag into the status LaunchAgent plist.

Environment overrides:
  LOOPX_REPO_ROOT
  LOOPX_BIN_DIR
  LOOPX_GLOBAL_REGISTRY
  LOOPX_STATUS_PORT
  LOOPX_STATUS_LIMIT
  LOOPX_STATUS_CONTRACT_MIN_VERSION
  LOOPX_CHAT_PORT
  LOOPX_DASHBOARD_HOST
  LOOPX_LAUNCH_LABEL_PREFIX
  LOOPX_CHAT_CODEX_HOME  Explicit managed Codex home (upgrades preserve the existing binding)
EOF
}

xml_escape() {
  sed \
    -e 's/&/\&amp;/g' \
    -e 's/</\&lt;/g' \
    -e 's/>/\&gt;/g' \
    -e 's/"/\&quot;/g' \
    <<<"$1"
}

shell_quote() {
  printf '%q' "$1"
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS LaunchAgent installation requires Darwin/macOS." >&2
    exit 1
  fi
}

resolve_status_command() {
  if [[ -x "$bin_dir/loopx" ]]; then
    printf '%s\n' "$bin_dir/loopx"
  elif [[ -x "$bin_dir/loopx-canary" ]]; then
    printf '%s\n' "$bin_dir/loopx-canary"
  elif command -v loopx >/dev/null 2>&1; then
    command -v loopx
  elif command -v loopx-canary >/dev/null 2>&1; then
    command -v loopx-canary
  else
    echo "loopx is not installed; run scripts/install-local.sh first." >&2
    exit 1
  fi
}

resolve_python_command() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif [[ -x /usr/bin/python3 ]]; then
    printf '%s\n' /usr/bin/python3
  else
    echo "python3 is not on PATH; install Python 3 or add it to PATH." >&2
    exit 1
  fi
}

resolve_loopx_python() {
  local python_command
  if python_command="$(bash "$repo_root/scripts/loopx-python.sh" 2>/dev/null)"; then
    printf '%s\n' "$python_command"
    return 0
  fi
  resolve_python_command
}

resolve_global_registry() {
  local python_command="$1"
  if [[ -n "$registry_override" ]]; then
    printf '%s\n' "$registry_override"
    return 0
  fi
  "$python_command" -c 'from loopx.paths import global_registry_path, select_default_runtime_root; print(global_registry_path(select_default_runtime_root()))'
}

resolve_optional_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    command -v "$command_name"
  else
    printf '%s\n' "$command_name"
  fi
}

resolve_lark_cli_command() {
  local python_command="$1"
  if command -v lark-cli >/dev/null 2>&1; then
    command -v lark-cli
    return 0
  fi
  "$python_command" - "$HOME" <<'PY'
import os
import re
import sys
from pathlib import Path

home = Path(sys.argv[1]).expanduser()
nvm_root = home / ".nvm" / "versions" / "node"
versions = []
if nvm_root.is_dir():
    for directory in nvm_root.iterdir():
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", directory.name)
        if match:
            versions.append((tuple(map(int, match.groups())), directory / "bin" / "lark-cli"))
candidates = []
nvm_bin = os.environ.get("NVM_BIN", "").strip()
if nvm_bin:
    candidates.append(Path(nvm_bin).expanduser() / "lark-cli")
candidates.extend(path for _version, path in sorted(versions, key=lambda item: item[0], reverse=True))
candidates.extend(
    [
        home / ".local" / "bin" / "lark-cli",
        home / ".npm-global" / "bin" / "lark-cli",
        Path("/opt/homebrew/bin/lark-cli"),
        Path("/usr/local/bin/lark-cli"),
        Path("/usr/bin/lark-cli"),
        Path("/bin/lark-cli"),
    ]
)
for candidate in candidates:
    if candidate.is_file() and os.access(candidate, os.X_OK):
        print(candidate)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

resolve_chat_codex_home() {
  "$1" - "$chat_plist" <<'PY'
import os
from pathlib import Path
import plistlib
import shlex
import sys

target = Path(sys.argv[1])
selected = os.environ.get("LOOPX_CHAT_CODEX_HOME")
if not selected and target.exists():
    # Decode, never execute, an old generated shell command. A malformed plist
    # must fail closed rather than silently adopt the upgrader's account home.
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    env = plist.get("EnvironmentVariables", {})
    selected = env.get("LOOPX_CHAT_CODEX_HOME") or env.get("CODEX_HOME")
    if not selected:
        args = plist.get("ProgramArguments", [])
        if len(args) == 3 and args[1] == "-c":
            lexer = shlex.shlex(args[2], posix=True, punctuation_chars=";")
            lexer.whitespace_split = True
            words = list(lexer)
            for index, word in enumerate(words[:-1]):
                if word == "export" and words[index + 1].startswith("CODEX_HOME="):
                    selected = words[index + 1].split("=", 1)[1]
                    break
    selected = selected or str(Path.home() / ".codex")
selected = selected or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
path = Path(selected).expanduser()
if not path.is_absolute():
    raise SystemExit("LoopX Chat Codex home must be absolute")
print(path.resolve())
PY
}

write_plists() {
  local status_command python_command codex_command claude_command lark_cli_command registry
  local path_prefix command_path command_dir status_shell chat_shell control_plane_write_arg lark_cli_arg codex_home_export chat_codex_home
  status_command="$(resolve_status_command)"
  python_command="$(resolve_loopx_python)"
  registry="$(resolve_global_registry "$python_command")"
  codex_command="$(resolve_optional_command codex)"
  claude_command="$(resolve_optional_command claude)"
  lark_cli_command="$(resolve_lark_cli_command "$python_command" 2>/dev/null || true)"
  path_prefix="$bin_dir"
  for command_path in "$codex_command" "$claude_command" "$lark_cli_command"; do
    if [[ "$command_path" == /* ]]; then
      command_dir="$(dirname "$command_path")"
      case ":$path_prefix:" in
        *":$command_dir:"*) ;;
        *) path_prefix="$path_prefix:$command_dir" ;;
      esac
    fi
  done
  path_prefix="$path_prefix:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  control_plane_write_arg=""
  lark_cli_arg=""
  codex_home_export=""
  if [[ "$control_plane_write_api_enabled" == "true" ]]; then
    control_plane_write_arg=" --enable-control-plane-write-api"
  fi
  if [[ -n "$lark_cli_command" ]]; then
    lark_cli_arg=" --lark-cli-bin $(shell_quote "$lark_cli_command")"
  fi
  chat_codex_home="$(resolve_chat_codex_home "$python_command")"
  codex_home_export=" export CODEX_HOME=$(shell_quote "$chat_codex_home"); export LOOPX_CHAT_CODEX_HOME=$(shell_quote "$chat_codex_home");"
  status_shell="export LOOPX_PYTHON=$(shell_quote "$python_command"); export PATH=$(shell_quote "$path_prefix"):\$PATH; exec $(shell_quote "$status_command") --registry $(shell_quote "$registry") serve-status --global-registry --host $(shell_quote "$host") --port $(shell_quote "$status_port") --limit $(shell_quote "$status_limit")$control_plane_write_arg"
  chat_shell="export LOOPX_PYTHON=$(shell_quote "$python_command");$codex_home_export export PATH=$(shell_quote "$path_prefix"):\$PATH; exec $(shell_quote "$status_command") --registry $(shell_quote "$registry") chat --global-registry --host $(shell_quote "$host") --port $(shell_quote "$chat_port") --codex-bin $(shell_quote "$codex_command") --claude-bin $(shell_quote "$claude_command")$lark_cli_arg --replace-existing-loopx-chat --no-open"

  mkdir -p "$launch_agents_dir" "$logs_dir"

  cat >"$status_plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$status_label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-c</string>
    <string>$(xml_escape "$status_shell")</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$logs_dir/status.out.log</string>
  <key>StandardErrorPath</key>
  <string>$logs_dir/status.err.log</string>
</dict>
</plist>
EOF

  cat >"$chat_plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$chat_label</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LOOPX_CHAT_CODEX_HOME</key>
    <string>$(xml_escape "$chat_codex_home")</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-c</string>
    <string>$(xml_escape "$chat_shell")</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$logs_dir/chat.out.log</string>
  <key>StandardErrorPath</key>
  <string>$logs_dir/chat.err.log</string>
</dict>
</plist>
EOF

}

bootout_one() {
  local label="$1" plist="$2"
  launchctl bootout "gui/$uid" "$plist" >/dev/null 2>&1 || true
  launchctl bootout "gui/$uid/$label" >/dev/null 2>&1 || true
}

bootstrap_one() {
  local label="$1" plist="$2"
  bootout_one "$label" "$plist"
  launchctl bootstrap "gui/$uid" "$plist"
  launchctl kickstart -k "gui/$uid/$label"
}

start_agents() {
  bootstrap_one "$status_label" "$status_plist"
  bootstrap_one "$chat_label" "$chat_plist"
  verify_current_chat_runtime
}

stop_agents() {
  bootout_one "$chat_label" "$chat_plist"
  bootout_one "$status_label" "$status_plist"
}

expected_chat_runtime_identity() {
  local status_command python_command
  status_command="$(resolve_status_command)"
  python_command="$(resolve_python_command)"
  "$status_command" --format json doctor | "$python_command" -c '
import json
import sys

payload = json.load(sys.stdin)
manifest = ((payload.get("release_manifest") or {}).get("manifest") or {})
package = manifest.get("package") or {}
source = manifest.get("source") or {}
identity = {
    "schema_version": "loopx_runtime_identity_v1",
    "package_version": package.get("version"),
    "release_id": manifest.get("release_id"),
    "source_revision": source.get("git_commit"),
}
if not identity["package_version"] or not identity["release_id"]:
    raise SystemExit(2)
print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
'
}

chat_runtime_identity() {
  local python_command payload
  python_command="$(resolve_python_command)"
  # A scheme-less curl endpoint defaults to local HTTP; managed replacement rejects non-loopback hosts.
  payload="$(curl -fsS "$chat_runtime_endpoint/api/chat/capabilities" 2>/dev/null)"
  "$python_command" -c '
import json
import sys

payload = json.load(sys.stdin)
if payload.get("ok") is not True or payload.get("schema_version") != "loopx_chat_capabilities_v1":
    raise SystemExit(2)
identity = payload.get("runtime_identity")
if not isinstance(identity, dict):
    raise SystemExit(2)
print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
' <<<"$payload"
}

verify_current_chat_runtime() {
  local expected actual attempt
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to verify the restarted LoopX Chat runtime." >&2
    return 1
  fi
  expected="$(expected_chat_runtime_identity)" || {
    echo "Could not resolve the installed LoopX runtime identity." >&2
    return 1
  }
  for attempt in {1..50}; do
    actual="$(chat_runtime_identity 2>/dev/null || true)"
    if [[ -n "$actual" && "$actual" == "$expected" ]]; then
      echo "- chat_runtime: current release identity verified"
      return 0
    fi
    sleep 0.2
  done
  echo "LoopX Chat did not start with the current release identity at local endpoint $chat_runtime_endpoint." >&2
  [[ -n "${actual:-}" ]] && echo "Observed runtime identity: $actual" >&2
  return 1
}

print_status_contract_health() {
  local status_url python_command status_json version producer control_plane_write
  status_url="http://$host:$status_port/status.json"
  python_command="$(resolve_python_command 2>/dev/null || true)"
  if ! command -v curl >/dev/null 2>&1 || [[ -z "$python_command" ]]; then
    echo "- status_contract: unknown (curl or python3 unavailable)"
    echo "- control_plane_write_api: unknown"
    return
  fi
  status_json="$(curl -fsS "$status_url" 2>/dev/null || true)"
  if [[ -z "$status_json" ]]; then
    echo "- status_contract: unavailable (status feed not reachable)"
    echo "- control_plane_write_api: unknown"
    return
  fi
  version="$("$python_command" -c 'import json,sys; data=json.load(sys.stdin); contract=data.get("status_contract") or {}; print(contract.get("schema_version", 0))' <<<"$status_json" 2>/dev/null || true)"
  producer="$("$python_command" -c 'import json,sys; data=json.load(sys.stdin); contract=data.get("status_contract") or {}; print(contract.get("producer") or "unknown")' <<<"$status_json" 2>/dev/null || true)"
  control_plane_write="$("$python_command" -c 'import json,sys; data=json.load(sys.stdin); api=data.get("local_dashboard_api") or {}; print("enabled" if api.get("control_plane_write_enabled") else "disabled")' <<<"$status_json" 2>/dev/null || true)"
  version="${version:-0}"
  producer="${producer:-unknown}"
  control_plane_write="${control_plane_write:-unknown}"
  echo "- status_contract: schema_version=$version producer=$producer expected>=$status_contract_min_version"
  echo "- control_plane_write_api: $control_plane_write"
  if [[ "$control_plane_write" == "enabled" ]]; then
    echo "  warning: control-plane registry writes are enabled for this local status feed"
  fi
  if [[ "$version" =~ ^[0-9]+$ ]] && (( version < status_contract_min_version )); then
    echo "  warning: status feed is using an old contract; run: $0 restart"
  fi
}

print_status() {
  echo "LaunchAgents:"
  launchctl print "gui/$uid/$status_label" >/dev/null 2>&1 \
    && echo "- $status_label: loaded" \
    || echo "- $status_label: not loaded"
  launchctl print "gui/$uid/$chat_label" >/dev/null 2>&1 \
    && echo "- $chat_label: loaded" \
    || echo "- $chat_label: not loaded"
  echo
  echo "URLs:"
  echo "- Chat:      http://$host:$chat_port/"
  echo "- status:    http://$host:$status_port/status.json"
  print_status_contract_health
  echo
  echo "Logs:"
  echo "- $logs_dir/status.out.log"
  echo "- $logs_dir/status.err.log"
  echo "- $logs_dir/chat.out.log"
  echo "- $logs_dir/chat.err.log"
}

main() {
  require_macos
  case "${1:-}" in
    install)
      write_plists
      start_agents
      print_status
      ;;
    uninstall)
      stop_agents
      rm -f "$status_plist" "$chat_plist"
      print_status
      ;;
    start)
      [[ -f "$status_plist" && -f "$chat_plist" ]] || {
        echo "LaunchAgents are not installed; run: $0 install" >&2
        exit 1
      }
      start_agents
      print_status
      ;;
    stop)
      stop_agents
      print_status
      ;;
    restart)
      write_plists
      start_agents
      print_status
      ;;
    status)
      print_status
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

parsed_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-control-plane-write-api)
      control_plane_write_api_enabled=true
      shift
      ;;
    --)
      shift
      parsed_args+=("$@")
      break
      ;;
    *)
      parsed_args+=("$1")
      shift
      ;;
  esac
done

main "${parsed_args[@]}"
