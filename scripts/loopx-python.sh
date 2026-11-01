#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

python_version_ok() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null
}

resolve_candidate() {
  local candidate="$1"
  if [[ "${candidate}" == */* ]]; then
    [ -x "${candidate}" ] && printf '%s\n' "${candidate}"
    return
  fi
  command -v "${candidate}" 2>/dev/null || true
}

# Discover installed minor-version names, then validate the executable itself.
# Numeric ordering avoids selecting python3.9 before python3.14 lexically.
versioned_python_names() {
  local directory candidate name minor
  for directory in "$@"; do
    for candidate in "${directory:-.}"/python3.*; do
      [ -x "${candidate}" ] && [ ! -d "${candidate}" ] || continue
      name="${candidate##*/}"
      minor="${name#python3.}"
      case "${minor}" in
        ''|*[!0-9]*) continue ;;
      esac
      printf '%s\n' "${name}"
    done
  done | sort -t. -k2,2nr | uniq
}

select_loopx_python() {
  local candidate=""
  local resolved=""
  local configured_python=""
  local authoritative=0
  local directory=""
  local remaining_path="${PATH:-}:"
  local -a path_directories=()
  while [[ "${remaining_path}" == *:* ]]; do
    path_directories+=("${remaining_path%%:*}")
    remaining_path="${remaining_path#*:}"
  done

  if [ -n "${LOOPX_PYTHON:-}" ]; then
    configured_python="${LOOPX_PYTHON}"
    authoritative=1
  elif [ -f "${REPO_ROOT}/.loopx-python" ]; then
    IFS= read -r configured_python <"${REPO_ROOT}/.loopx-python"
    # Installer records can use Windows CRLF; the CR is not part of the path.
    configured_python="${configured_python%$'\r'}"
  fi

  if [ -n "${configured_python}" ]; then
    resolved="$(resolve_candidate "${configured_python}")"
    if [ -n "${resolved}" ] && python_version_ok "${resolved}"; then
      printf '%s\n' "${resolved}"
      return 0
    fi
    if [ "${authoritative}" -eq 1 ]; then
      echo "LOOPX_PYTHON is set but does not resolve to a Python 3.11+ interpreter: ${configured_python}" >&2
      return 1
    fi
    echo "Ignoring non-functional Python recorded in .loopx-python: ${configured_python}" >&2
  fi

  resolved="$(resolve_candidate "${REPO_ROOT}/.venv/bin/python")"
  if [ -n "${resolved}" ] && python_version_ok "${resolved}"; then
    printf '%s\n' "${resolved}"
    return 0
  fi

  while IFS= read -r candidate; do
    resolved="$(resolve_candidate "${candidate}")"
    if [ -n "${resolved}" ] && python_version_ok "${resolved}"; then
      printf '%s\n' "${resolved}"
      return 0
    fi
  done < <(versioned_python_names "${path_directories[@]}")

  resolved="$(resolve_candidate python3)"
  if [ -n "${resolved}" ] && python_version_ok "${resolved}"; then
    printf '%s\n' "${resolved}"
    return 0
  fi

  for directory in "${HOME:-}/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    while IFS= read -r candidate; do
      resolved="$(resolve_candidate "${directory}/${candidate}")"
      if [ -n "${resolved}" ] && python_version_ok "${resolved}"; then
        printf '%s\n' "${resolved}"
        return 0
      fi
    done < <(versioned_python_names "${directory}")
  done

  return 1
}

if [ "${1:-}" = "--exec" ]; then
  shift
  if [ "$#" -eq 0 ]; then
    echo "usage: loopx-python.sh --exec <python-arguments...>" >&2
    exit 2
  fi
  if ! PYTHON_BIN="$(select_loopx_python)"; then
    echo "LoopX requires Python 3.11 or newer." >&2
    exit 2
  fi
  exec "${PYTHON_BIN}" "$@"
fi

select_loopx_python
