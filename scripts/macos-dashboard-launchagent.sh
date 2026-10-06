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
log_max_bytes_override="${LOOPX_LOG_MAX_BYTES:-}"
log_max_bytes="${log_max_bytes_override:-10485760}"

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
  LOOPX_LOG_MAX_BYTES    Rotate an agent log once it exceeds this size (default 10 MiB)
  CODEX_HOME            Explicit service execution home, independent of the Chat override
  LOOPX_CHAT_CODEX_HOME  Explicit managed Codex home (upgrades preserve the existing binding)
  LOOPX_CHAT_SCAN_PATHS_JSON  JSON array of absolute workspace directories (preserved on upgrade)
  LOOPX_CHAT_RUNTIME_ROOT    Explicit Chat data directory (preserved on upgrade)
  LOOPX_CHAT_IDLE_TIMEOUT_SECONDS  Explicit idle timeout (preserved on upgrade)
  LOOPX_CHAT_HARD_TIMEOUT_SECONDS  Explicit turn timeout (preserved on upgrade)
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

# Keep the agent logs bounded. KeepAlive means these files outlive every
# release: without a retention step they only ever grow, and a service that
# becomes noisy for a while leaves that output on disk forever.
#
# Rotation has to run inside the agent's own wrapper, because launchd restarts
# the service on its own and those restarts never re-enter this installer.
# It also must not rename the live file: launchd opens StandardOutPath before
# the wrapper runs and keeps appending to that descriptor, so renaming would
# send the service's output to the rotated copy and leave the live path empty.
# Copy the previous generation aside and truncate in place instead, which the
# append-mode descriptor follows back to offset zero.
#
# The truncate is conditional on the copy succeeding. A failed copy (read-only
# target, full disk) must leave the live log intact: dropping it would destroy
# the only record of the failure the operator is trying to diagnose. Retention
# is retried at the next agent start, and the warning goes to the agent's own
# error log.
log_rotation_prelude() {
  local basename="$1"
  printf 'for loopx_log in %s %s; do [ -f "$loopx_log" ] || continue; loopx_size="$(stat -f%%z "$loopx_log" 2>/dev/null || echo 0)"; case "$loopx_size" in [0-9]*) ;; *) continue; esac; [ "$loopx_size" -gt %s ] || continue; if [ ! -d "$loopx_log.1" ] && cp -f "$loopx_log" "$loopx_log.1" 2>/dev/null; then : >"$loopx_log"; else printf "loopx-launchagent: kept %%s and skipped retention: could not write %%s.1; the next start retries\\n" "$loopx_log" "$loopx_log" >&2; fi; done; unset loopx_log loopx_size;' \
    "$(shell_quote "$logs_dir/$basename.out.log")" \
    "$(shell_quote "$logs_dir/$basename.err.log")" \
    "$log_max_bytes"
}

# The installed retention policy is whatever the installed wrapper runs; the
# caller's environment describes a future install. Read the value back out of
# the plist so status cannot report a setting that is not in effect.
installed_log_max_bytes() {
  local plist="$1" value
  [[ -f "$plist" ]] || return 1
  value="$(grep -o -- '-gt [0-9][0-9]*' "$plist" 2>/dev/null | head -n 1 | awk '{print $2}')"
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$value"
}

# Fail fast instead of writing a wrapper whose retention step can never match.
validate_log_max_bytes() {
  [[ "$log_max_bytes" =~ ^[0-9]+$ ]] || return 1
  (( log_max_bytes > 0 ))
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
  # Ask the selected console script, rather than letting a checkout venv own
  # the interpreter of an unrelated uv/pipx install. Legacy snapshots retain
  # the existing resolver until their doctor exposes this projection.
  python_command="$("$1" --format json doctor --installation-only | "$(resolve_python_command)" -c 'import json,sys; print((json.load(sys.stdin).get("python") or {}).get("executable") or "")')" || return 1
  if [[ -n "$python_command" && -x "$python_command" ]]; then
    printf '%s\n' "$python_command"
    return 0
  fi
  if python_command="$(bash "$repo_root/scripts/loopx-python.sh" 2>/dev/null)"; then
    printf '%s\n' "$python_command"
    return 0
  fi
  resolve_python_command
}

resolve_global_registry() {
  # A Python stdin entry otherwise imports the caller's checkout before the
  # selected distribution. Resolve defaults outside that checkout.
  (
  cd /
  "$1" - "$chat_plist" "$registry_override" <<'PY'
import plistlib
import shlex
import sys
from pathlib import Path

selected = sys.argv[2]
target = Path(sys.argv[1])
if not selected and target.exists():
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    selected = plist.get("EnvironmentVariables", {}).get("LOOPX_GLOBAL_REGISTRY")
    if not selected:
        args = plist.get("ProgramArguments", [])
        words = shlex.split(args[2]) if len(args) == 3 and args[1] == "-c" else []
        if "--registry" in words:
            selected = words[words.index("--registry") + 1]
if not selected:
    from loopx.paths import global_registry_path, select_default_runtime_root
    selected = str(global_registry_path(select_default_runtime_root()))
path = Path(selected).expanduser()
if not path.is_absolute():
    raise SystemExit("LoopX managed registry must be absolute")
print(path.resolve())
PY
  )
}

resolve_chat_scan_paths() {
  "$1" - "$chat_plist" <<'PY'
import json
import os
import plistlib
import sys
from pathlib import Path

selected = os.environ.get("LOOPX_CHAT_SCAN_PATHS_JSON")
target = Path(sys.argv[1])
if selected is None and target.exists():
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    selected = plist.get("EnvironmentVariables", {}).get("LOOPX_CHAT_SCAN_PATHS_JSON")
if selected is not None and (not isinstance(selected, str) or len(selected) > 16000):
    raise SystemExit("LoopX Chat workspace selection is too large")
paths = json.loads(selected) if selected is not None else []
if not isinstance(paths, list) or len(paths) > 32:
    raise SystemExit("LoopX Chat workspace selection must be an array of at most 32 directories")
resolved = []
for item in paths:
    if not isinstance(item, str) or not item or any(ord(c) < 32 for c in item):
        raise SystemExit("LoopX Chat workspace paths must be nonempty strings without control characters")
    path = Path(item).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise SystemExit("LoopX Chat workspace paths must be existing absolute directories")
    value = str(path.resolve())
    if value not in resolved:
        resolved.append(value)
print(json.dumps(resolved, ensure_ascii=False))
PY
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

resolve_codex_home() {
  "$1" - "$chat_plist" "$2" "${3:-}" <<'PY'
import os
from pathlib import Path
import plistlib
import shlex
import sys

target = Path(sys.argv[1])
variable, fallback = sys.argv[2:4]
selected = os.environ.get(variable)
if not selected and target.exists():
    # Decode, never execute, an old generated shell command. A malformed plist
    # must fail closed rather than silently adopt the upgrader's account home.
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    env = plist.get("EnvironmentVariables", {})
    selected = env.get(variable) or env.get("CODEX_HOME")
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
selected = selected or fallback or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
path = Path(selected).expanduser()
if not path.is_absolute():
    raise SystemExit(f"{variable} must be absolute")
print(path.resolve())
PY
}

resolve_chat_options() {
  "$1" - "$chat_plist" <<'PY'
import json
import math
import os
import plistlib
import shlex
import sys
from pathlib import Path

target = Path(sys.argv[1])
installed, words = {}, []
if target.exists():
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    installed = plist.get("EnvironmentVariables", {})
    args = plist.get("ProgramArguments", [])
    # Decode the legacy command; never run it to recover a setting.
    words = shlex.split(args[2]) if len(args) == 3 and args[1] == "-c" else args
options = {}
for variable, flag in (
    ("LOOPX_CHAT_RUNTIME_ROOT", "--runtime-root"),
    ("LOOPX_CHAT_IDLE_TIMEOUT_SECONDS", "--idle-timeout-seconds"),
    ("LOOPX_CHAT_HARD_TIMEOUT_SECONDS", "--hard-timeout-seconds"),
):
    value = os.environ.get(variable) or installed.get(variable)
    if value is not None and not isinstance(value, str):
        raise SystemExit(f"{variable} must be a string")
    if not value:
        for index, word in enumerate(words):
            if word == flag:
                if index + 1 == len(words):
                    raise SystemExit(f"{variable} has a missing installed argument")
                value = words[index + 1]
            elif word.startswith(flag + "="):
                value = word[len(flag) + 1:]
                if not value:
                    raise SystemExit(f"{variable} has an empty installed argument")
    if value is None or value == "":
        options[variable] = ""
        continue  # Retain the CLI's existing default, without pinning a new root.
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise SystemExit(f"{variable} must be a bounded string without control characters")
    if flag == "--runtime-root":
        path = Path(value).expanduser()
        if not path.is_absolute() or (path.exists() and not path.is_dir()):
            raise SystemExit(f"{variable} must be an absolute directory")
        value = str(path.resolve())
    else:
        try:
            seconds = float(value)
        except ValueError:
            raise SystemExit(f"{variable} must be a positive finite number")
        if not math.isfinite(seconds) or seconds <= 0:
            raise SystemExit(f"{variable} must be a positive finite number")
    options[variable] = value
print(json.dumps(options))
PY
}

write_plists() {
  local status_command python_command codex_command claude_command lark_cli_command registry
  local path_prefix command_path command_dir status_shell chat_shell control_plane_write_arg lark_cli_arg codex_home_export chat_codex_home execution_codex_home chat_scan_paths chat_scan_args
  local chat_options chat_runtime_root chat_idle_timeout chat_hard_timeout chat_runtime_arg chat_timeout_args
  status_command="$(resolve_status_command)"
  python_command="$(resolve_loopx_python "$status_command")"
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
  chat_codex_home="$(resolve_codex_home "$python_command" LOOPX_CHAT_CODEX_HOME)"
  execution_codex_home="$(resolve_codex_home "$python_command" CODEX_HOME "$chat_codex_home")"
  chat_scan_paths="$(resolve_chat_scan_paths "$python_command")"
  chat_scan_args="$("$python_command" -c 'import json,shlex,sys; print("".join(" --scan-path " + shlex.quote(path) for path in json.load(sys.stdin)))' <<<"$chat_scan_paths")"
  chat_options="$(resolve_chat_options "$python_command")"
  chat_runtime_root="$("$python_command" -c 'import json,sys; print(json.load(sys.stdin)["LOOPX_CHAT_RUNTIME_ROOT"])' <<<"$chat_options")"
  chat_idle_timeout="$("$python_command" -c 'import json,sys; print(json.load(sys.stdin)["LOOPX_CHAT_IDLE_TIMEOUT_SECONDS"])' <<<"$chat_options")"
  chat_hard_timeout="$("$python_command" -c 'import json,sys; print(json.load(sys.stdin)["LOOPX_CHAT_HARD_TIMEOUT_SECONDS"])' <<<"$chat_options")"
  chat_runtime_arg=""
  chat_timeout_args=""
  [[ -z "$chat_runtime_root" ]] || chat_runtime_arg=" --runtime-root $(shell_quote "$chat_runtime_root")"
  [[ -z "$chat_idle_timeout" ]] || chat_timeout_args+=" --idle-timeout-seconds $(shell_quote "$chat_idle_timeout")"
  [[ -z "$chat_hard_timeout" ]] || chat_timeout_args+=" --hard-timeout-seconds $(shell_quote "$chat_hard_timeout")"
  expected_chat_runtime_identity >/dev/null || {
    echo "Could not resolve the installed LoopX runtime identity; existing plists were kept." >&2
    return 1
  }
  codex_home_export=" export CODEX_HOME=$(shell_quote "$execution_codex_home"); export LOOPX_CHAT_CODEX_HOME=$(shell_quote "$chat_codex_home");"
  # Registry has already been resolved explicitly. --global-registry would
  # replace it with <common_runtime_root>/registry.json and lose custom routes.
  status_shell="$(log_rotation_prelude status) export LOOPX_PYTHON=$(shell_quote "$python_command"); export PATH=$(shell_quote "$path_prefix"):\$PATH; exec $(shell_quote "$status_command") --registry $(shell_quote "$registry") serve-status --host $(shell_quote "$host") --port $(shell_quote "$status_port") --limit $(shell_quote "$status_limit")$chat_scan_args$control_plane_write_arg"
  chat_shell="$(log_rotation_prelude chat) export LOOPX_PYTHON=$(shell_quote "$python_command");$codex_home_export export PATH=$(shell_quote "$path_prefix"):\$PATH; exec $(shell_quote "$status_command") --registry $(shell_quote "$registry")$chat_runtime_arg chat --host $(shell_quote "$host") --port $(shell_quote "$chat_port") --codex-bin $(shell_quote "$codex_command") --claude-bin $(shell_quote "$claude_command")$lark_cli_arg$chat_scan_args$chat_timeout_args --replace-existing-loopx-chat --no-open"

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
    <key>CODEX_HOME</key>
    <string>$(xml_escape "$execution_codex_home")</string>
    <key>LOOPX_CHAT_CODEX_HOME</key>
    <string>$(xml_escape "$chat_codex_home")</string>
    <key>LOOPX_GLOBAL_REGISTRY</key>
    <string>$(xml_escape "$registry")</string>
    <key>LOOPX_CHAT_SCAN_PATHS_JSON</key>
    <string>$(xml_escape "$chat_scan_paths")</string>
    <key>LOOPX_CHAT_RUNTIME_ROOT</key>
    <string>$(xml_escape "$chat_runtime_root")</string>
    <key>LOOPX_CHAT_IDLE_TIMEOUT_SECONDS</key>
    <string>$(xml_escape "$chat_idle_timeout")</string>
    <key>LOOPX_CHAT_HARD_TIMEOUT_SECONDS</key>
    <string>$(xml_escape "$chat_hard_timeout")</string>
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
  local expected
  expected="$(expected_chat_runtime_identity)" || {
    echo "Could not resolve the installed LoopX runtime identity; existing agents were kept." >&2
    return 1
  }
  bootstrap_one "$status_label" "$status_plist"
  bootstrap_one "$chat_label" "$chat_plist"
  verify_current_chat_runtime "$expected"
}

stop_agents() {
  bootout_one "$chat_label" "$chat_plist"
  bootout_one "$status_label" "$status_plist"
}

expected_chat_runtime_identity() {
  local status_command python_command
  status_command="$(resolve_status_command)"
  python_command="$(resolve_python_command)"
  "$status_command" --format json doctor --installation-only | "$python_command" -c '
import json
import sys

payload = json.load(sys.stdin)
manifest = ((payload.get("release_manifest") or {}).get("manifest") or {})
package = manifest.get("package") or {}
source = manifest.get("source") or {}
identity = payload.get("service_runtime_identity") or {
    "schema_version": "loopx_runtime_identity_v1",
    "package_version": package.get("version"),
    "release_id": manifest.get("release_id"),
    "source_revision": source.get("git_commit"),
}
if (not isinstance(identity, dict)
    or identity.get("schema_version") != "loopx_runtime_identity_v1"
    or not identity.get("package_version")
    or not (identity.get("release_id") or identity.get("package_fingerprint"))):
    raise SystemExit(2)
print(json.dumps(identity, sort_keys=True, separators=(",", ":")))
'
}

chat_runtime_identity() {
  local python_command payload
  python_command="$(resolve_python_command)"
  # A scheme-less curl endpoint defaults to local HTTP; managed replacement rejects non-loopback hosts.
  payload="$(curl -fsS --connect-timeout 1 --max-time 5 "$chat_runtime_endpoint/api/chat/capabilities" 2>/dev/null)"
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
  expected="${1:-}"; [[ -n "$expected" ]] || expected="$(expected_chat_runtime_identity)" || {
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
  # The full feed collects registered Goals; it is not a cheap liveness probe.
  # Allow a bounded read beyond five seconds while retaining connection failure
  # and contract-version checks. This does not make a slow feed healthy.
  status_json="$(curl -fsS --connect-timeout 1 --max-time 15 "$status_url" 2>/dev/null || true)"
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
  local installed_log_max
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
  if installed_log_max="$(installed_log_max_bytes "$status_plist")"; then
    echo "- retention: rotated to .1 at each agent start once a log exceeds $installed_log_max bytes"
    if [[ -n "$log_max_bytes_override" ]] && (( installed_log_max != log_max_bytes )); then
      echo "  note: LOOPX_LOG_MAX_BYTES=$log_max_bytes is not in effect; the installed value holds until the next install or restart"
    fi
  else
    echo "- retention: unknown (the installed agents carry no retention step; run: $0 install)"
  fi
}

main() {
  require_macos
  case "${1:-}" in
    install|restart)
      if ! validate_log_max_bytes; then
        echo "LOOPX_LOG_MAX_BYTES must be a positive byte count, got: $log_max_bytes" >&2
        exit 2
      fi
      ;;
  esac
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
