#!/usr/bin/env bash
set -euo pipefail

repo="${LOOPX_REPO:-loopx-project/loopx}"
ref="${LOOPX_REF:-stable}"
archive_url_override="${LOOPX_ARCHIVE_URL:-}"
archive_url="$archive_url_override"
python_bin="${LOOPX_PYTHON:-python3}"
installer_timeout_seconds="${LOOPX_INSTALLER_TIMEOUT_SECONDS:-}"
export LOOPX_REPO="$repo"
export LOOPX_REF="$ref"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "loopx installer error: missing required command: $1" >&2
    exit 1
  fi
}

need curl
need tar
need "$python_bin"

if [[ -n "${LOOPX_RESOLVED_SOURCE_GIT_COMMIT:-}" \
  && ! "$LOOPX_RESOLVED_SOURCE_GIT_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "loopx installer error: LOOPX_RESOLVED_SOURCE_GIT_COMMIT must be a full Git commit SHA" >&2
  exit 2
fi
if [[ -n "$installer_timeout_seconds" \
  && ! "$installer_timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "loopx installer error: LOOPX_INSTALLER_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/loopx-install.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

if [[ -z "$archive_url" ]]; then
  if [[ ! "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    echo "loopx installer error: LOOPX_REPO must use GitHub owner/name syntax" >&2
    exit 2
  fi
  if [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    # Immutable inputs need no branch-resolution service (or GitHub API quota).
    resolved_commit="$(printf '%s' "$ref" | tr '[:upper:]' '[:lower:]')"
    if [[ -n "${LOOPX_RESOLVED_SOURCE_GIT_COMMIT:-}" \
      && "$(printf '%s' "$LOOPX_RESOLVED_SOURCE_GIT_COMMIT" | tr '[:upper:]' '[:lower:]')" != "$resolved_commit" ]]; then
      echo "loopx installer error: resolved commit disagrees with the requested full SHA" >&2
      exit 2
    fi
  else
    commit_api_url="$("$python_bin" - "$repo" "$ref" <<'PY'
from urllib.parse import quote
import sys

repo, ref = sys.argv[1:]
owner, name = repo.split("/", 1)
print(
    "https://api.github.com/repos/"
    f"{quote(owner, safe='')}/{quote(name, safe='')}/commits/{quote(ref, safe='')}"
)
PY
)"
    if ! curl -fsSL --connect-timeout 10 --max-time 30 --retry 2 --retry-max-time 45 \
    -H 'Accept: application/vnd.github+json' \
    -H 'User-Agent: LoopX-installer' \
      "$commit_api_url" -o "$tmp_dir/commit.json"; then
      # Reuse an existing login when available; never request credentials or
      # fall back to a different branch. Keep both transport and capture bounded.
      echo "loopx installer: public commit lookup failed; trying existing GitHub CLI authentication" >&2
      "$python_bin" - "$commit_api_url" "$tmp_dir/commit.json" <<'PY'
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

gh = shutil.which("gh")
if gh:
    try:
        with Path(sys.argv[2]).open("wb") as output:
            result = subprocess.run(
                [gh, "api", "--hostname", "github.com", urlsplit(sys.argv[1]).path],
                stdout=output, stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
        if result.returncode == 0:
            raise SystemExit(0)
    except (OSError, subprocess.TimeoutExpired):
        pass
raise SystemExit("Commit lookup failed. Retry with LOOPX_REF=<verified full commit SHA>; "
                 "no runtime was installed and no alternate ref was selected.")
PY
    fi
    resolved_commit="$("$python_bin" - "$tmp_dir/commit.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
sha = payload.get("sha")
if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
    raise SystemExit("GitHub commit response did not include a full SHA")
print(sha.lower())
PY
)"
  fi
  export LOOPX_RESOLVED_SOURCE_GIT_COMMIT="$resolved_commit"
  archive_url="https://codeload.github.com/$repo/tar.gz/$resolved_commit"
fi
export LOOPX_ARCHIVE_URL="$archive_url"

archive_path="$tmp_dir/loopx.tar.gz"
extract_dir="$tmp_dir/extract"
mkdir -p "$extract_dir"

echo "loopx installer: downloading $archive_url" >&2
archive_deadline=$((SECONDS + ${installer_timeout_seconds:-150}))
archive_attempt=1
archive_max_attempts=3
archive_attempts_completed=0
archive_downloaded=0
last_curl_code=28
last_http_status=0
while [[ "$archive_attempt" -le "$archive_max_attempts" ]]; do
  remaining=$((archive_deadline - SECONDS))
  if [[ "$remaining" -le 0 ]]; then
    break
  fi
  attempt_timeout="$remaining"
  if [[ "$archive_attempt" -lt "$archive_max_attempts" ]]; then
    reserved_attempts=$((archive_max_attempts - archive_attempt))
    attempt_timeout=$((remaining - reserved_attempts))
    if [[ "$attempt_timeout" -gt 120 ]]; then
      attempt_timeout=120
    elif [[ "$attempt_timeout" -lt 1 ]]; then
      attempt_timeout=1
    fi
  fi
  archive_attempts_completed="$archive_attempt"
  if http_status="$(curl --silent --show-error --fail --location \
    --connect-timeout 10 --max-time "$attempt_timeout" \
    --continue-at - --write-out '%{http_code}' \
    "$archive_url" -o "$archive_path")"; then
    archive_downloaded=1
    break
  else
    last_curl_code=$?
  fi
  if [[ "$http_status" =~ ^[0-9]{3}$ ]]; then
    last_http_status="$http_status"
  else
    last_http_status=0
  fi
  retryable=0
  case "$last_curl_code" in
    5|6|7|18|28|35|52|55|56)
      retryable=1
      ;;
    22)
      case "$last_http_status" in
        403|408|429|500|502|503|504)
          retryable=1
          ;;
      esac
      ;;
  esac
  if [[ "$retryable" -ne 1 ]]; then
    break
  fi
  archive_attempt=$((archive_attempt + 1))
done
if [[ "$archive_downloaded" -ne 1 ]]; then
  echo "loopx installer error: archive download failed after $archive_attempts_completed attempt(s) (curl $last_curl_code, HTTP $last_http_status)" >&2
  exit "$last_curl_code"
fi
archive_sha256="$("$python_bin" - "$archive_path" <<'PY'
from pathlib import Path
import hashlib
import sys

digest = hashlib.sha256()
with Path(sys.argv[1]).open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
print(digest.hexdigest())
PY
)"
export LOOPX_ARCHIVE_SHA256="$archive_sha256"
tar -xzf "$archive_path" -C "$extract_dir"

repo_root="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -z "$repo_root" || ! -x "$repo_root/scripts/install-local.sh" ]]; then
  echo "loopx installer error: downloaded archive does not contain scripts/install-local.sh" >&2
  exit 1
fi

# The downloaded checkout is temporary. Install a stable release snapshot and
# skip the live canary symlink unless the caller explicitly overrides it.
export LOOPX_INSTALL_CANARY="${LOOPX_INSTALL_CANARY:-0}"
export LOOPX_PROMOTE_DEFAULT="${LOOPX_PROMOTE_DEFAULT:-1}"
export LOOPX_PROMOTION_MODE="${LOOPX_PROMOTION_MODE:-trusted_github_archive}"

"$repo_root/scripts/install-local.sh"
