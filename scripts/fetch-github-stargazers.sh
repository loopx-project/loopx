#!/usr/bin/env bash
set -euo pipefail

repository="${1:-}"
output_json="${2:-}"
count_output="${3:-}"

if [[ ! "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ || -z "$output_json" || -z "$count_output" ]]; then
  echo "usage: fetch-github-stargazers.sh OWNER/REPO OUTPUT_JSON COUNT_OUTPUT" >&2
  exit 2
fi
if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "::error title=Missing star-history credential::Set STAR_HISTORY_READ_TOKEN to a classic PAT from an admin/collaborator account with the public_repo scope." >&2
  exit 1
fi

owner="${repository%%/*}"
repo="${repository#*/}"
temp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
pages_file="$(mktemp "$temp_root/loopx-stargazer-pages.XXXXXX.json")"
snapshot_file="$(mktemp "$temp_root/loopx-stargazers.XXXXXX.json")"
read_file="$(mktemp "$temp_root/loopx-stargazer-read.XXXXXX.json")"
read_error_file="$(mktemp "$temp_root/loopx-stargazer-read.XXXXXX.err")"
cleanup() {
  rm -f "$pages_file" "$snapshot_file" "$read_file" "$read_error_file"
}
trap cleanup EXIT

retry_delay="${LOOPX_STARGAZER_RETRY_DELAY_SECONDS:-5}"
api_attempts="${LOOPX_STARGAZER_API_ATTEMPTS:-3}"

# GitHub occasionally answers a read with a 5xx status or with an empty or
# truncated body. `gh` reports those as "unexpected end of JSON input", and
# `set -e` used to abort the whole Pages deployment on that single transient
# answer, before the snapshot loop below could try again. Only transport-level
# answers are retried: authorization, scope and missing-resource failures must
# still fail the build immediately with their own diagnostic.
retryable_read_failure() {
  case "$1" in
    *"unexpected end of JSON input"* | *"unexpected EOF"* | *"connection reset"* | *"stream error"*)
      return 0
      ;;
    *"HTTP 429"* | *"HTTP 500"* | *"HTTP 502"* | *"HTTP 503"* | *"HTTP 504"*)
      return 0
      ;;
  esac
  return 1
}

read_api_json() {
  # read_api_json DESCRIPTION TARGET_FILE [gh api arguments...]
  local description="$1"
  local target="$2"
  shift 2
  local attempt=1
  local message
  local retryable
  while ((attempt <= api_attempts)); do
    : > "$target"
    : > "$read_error_file"
    if gh api "$@" > "$target" 2> "$read_error_file"; then
      if [[ -s "$target" ]] && jq -e 'true' "$target" > /dev/null 2>&1; then
        return 0
      fi
      # A 2xx answer whose body is empty or truncated is a transport fault,
      # not a statement about the repository.
      message="response body was empty or not valid JSON"
      retryable=1
    else
      message="$(<"$read_error_file")"
      if [[ -z "$message" ]]; then
        message="gh api exited without a diagnostic"
      fi
      if retryable_read_failure "$message"; then
        retryable=1
      else
        retryable=0
      fi
    fi
    message="${message//$'\n'/ }"
    if ((attempt == api_attempts)) || ((retryable == 0)); then
      echo "::error title=Star history read failed::$description failed: $message" >&2
      return 1
    fi
    echo "::warning title=Retrying star history read::$description attempt $attempt/$api_attempts failed: $message" >&2
    sleep "$retry_delay"
    attempt=$((attempt + 1))
  done
}

for attempt in 1 2 3; do
  read_api_json \
    "repository star count before the snapshot" \
    "$read_file" \
    "repos/$owner/$repo" --jq .stargazers_count
  count_before="$(tr -d '[:space:]' < "$read_file")"
  read_api_json \
    "stargazer page fetch" \
    "$pages_file" \
    --paginate --slurp \
    -H "Accept: application/vnd.github.star+json" \
    "repos/$owner/$repo/stargazers?per_page=100"
  jq '[.[][] | if (.starred_at | type) == "string" then {starred_at: .starred_at} else error("stargazer row is missing starred_at") end]' \
    "$pages_file" > "$snapshot_file"
  read_api_json \
    "repository star count after the snapshot" \
    "$read_file" \
    "repos/$owner/$repo" --jq .stargazers_count
  count_after="$(tr -d '[:space:]' < "$read_file")"
  fetched_count="$(jq 'length' "$snapshot_file")"
  if [[ ! "$count_before" =~ ^[0-9]+$ || ! "$count_after" =~ ^[0-9]+$ || ! "$fetched_count" =~ ^[0-9]+$ ]]; then
    echo "GitHub returned a non-numeric stargazer count" >&2
    exit 1
  fi

  if [[ "$count_before" == "$count_after" && "$fetched_count" -le "$count_after" ]]; then
    install -m 0644 "$snapshot_file" "$output_json"
    printf '%s\n' "$fetched_count" > "$count_output"
    if [[ "$fetched_count" != "$count_after" ]]; then
      echo "::warning title=Aggregate-only stargazers omitted::Fetched all $fetched_count enumerable stargazer events; GitHub reports $count_after aggregate stars." >&2
    fi
    exit 0
  fi

  if [[ "$attempt" == 3 ]]; then
    echo "stargazer snapshot changed or pagination was incomplete: before=$count_before fetched=$fetched_count after=$count_after" >&2
    exit 1
  fi
  sleep "$retry_delay"
done
