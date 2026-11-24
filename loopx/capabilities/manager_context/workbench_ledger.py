"""Authenticated read-only client for the local Workbench Steward ledger socket.

Reuses Workbench's host-minted ``<data_dir>/steward/credential.json`` and
``<data_dir>/steward/calli.sock`` without creating a second task store or
mutating Core ``turn_context_cache`` / ``steward.background`` ownership.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any, Mapping


WORKBENCH_STEWARD_CREDENTIAL_ENV = "LOOPX_WORKBENCH_STEWARD_CREDENTIAL"
DEFAULT_SOCKET_TIMEOUT_SECONDS = 4.0


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = DEFAULT_SOCKET_TIMEOUT_SECONDS) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def _is_ephemeral_runtime_root(runtime_root: Path | None) -> bool:
    if runtime_root is None:
        return False
    try:
        resolved = runtime_root.resolve()
        tmp_root = Path(tempfile.gettempdir()).resolve()
        return resolved == tmp_root or tmp_root in resolved.parents
    except OSError:
        return False


def resolve_workbench_steward_credential_path(
    environ: Mapping[str, str] | None = None,
    *,
    runtime_root: Path | None = None,
) -> Path | None:
    """Locate the local Workbench supervisor credential file if configured or running."""
    env = os.environ if environ is None else environ
    explicit = env.get(WORKBENCH_STEWARD_CREDENTIAL_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()

    candidates: list[Path] = []
    data_dir = env.get("LOOCHAT_DATA_DIR", "").strip()
    if data_dir:
        candidates.append(Path(data_dir).expanduser() / "steward" / "credential.json")
    state_dir = env.get("WORKBENCH_STATE_DIR", "").strip()
    if state_dir:
        candidates.append(Path(state_dir).expanduser() / "data" / "steward" / "credential.json")

    if not _is_ephemeral_runtime_root(runtime_root):
        xdg_state = env.get("XDG_STATE_HOME", "").strip()
        base_state = Path(xdg_state).expanduser() if xdg_state else (Path.home() / ".local" / "state")
        candidates.append(base_state / "loochat-workbench" / "data" / "steward" / "credential.json")

    for path in candidates:
        if path.is_file():
            return path
    return None


def _unavailable_response(view: str, reason: str, detail: str) -> dict[str, Any]:
    empty_collection: dict[str, Any]
    if view == "sessions_find":
        empty_collection = {"hits": [], "index_status": "rebuilding"}
    elif view == "session_read":
        empty_collection = {"chunks": [], "next_cursor": None}
    else:
        empty_collection = {"items": [], "next_cursor": None}
    return {
        "ok": True,
        "view": view,
        "available": False,
        "source_id": "local",
        "source_health": {
            "workbench": {
                "status": "unavailable",
                "reason": reason,
                "detail": detail,
            }
        },
        "coverage": {
            "workbench_threads": "unavailable",
            "local_sessions": "unavailable",
        },
        **empty_collection,
        "note": (
            "Local Workbench session store was not read on this call; do not report "
            "unavailable non-Goal sessions as absent or completed."
        ),
    }


def call_workbench_steward_rpc(
    method: str,
    params: Mapping[str, Any],
    *,
    view_name: str,
    environ: Mapping[str, str] | None = None,
    runtime_root: Path | None = None,
    timeout: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Invoke a read-only Steward S1 RPC over Workbench's authenticated Unix socket."""
    cred_path = resolve_workbench_steward_credential_path(environ, runtime_root=runtime_root)
    if cred_path is None or not cred_path.is_file():
        return _unavailable_response(
            view_name,
            "workbench_steward_socket_unconfigured",
            "Workbench daemon is not running or has not published steward/credential.json.",
        )

    try:
        cred = json.loads(cred_path.read_text(encoding="utf-8"))
        socket_path = str(cred.get("socketPath") or "")
        token = str(cred.get("token") or "")
        if not socket_path or not token:
            raise ValueError("missing socketPath or token")
    except (OSError, ValueError, TypeError):
        return _unavailable_response(
            view_name,
            "workbench_steward_credential_unreadable",
            "Could not parse Workbench steward credential metadata.",
        )

    if not Path(socket_path).exists():
        return _unavailable_response(
            view_name,
            "workbench_steward_socket_missing",
            "Workbench steward socket does not exist; daemon may be stopped.",
        )

    body = json.dumps({"method": method, "params": dict(params)}, ensure_ascii=False).encode("utf-8")
    conn = _UnixHTTPConnection(socket_path, timeout=timeout)
    try:
        conn.request(
            "POST",
            "/rpc",
            body=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
                "Authorization": f"Bearer {token}",
            },
        )
        resp = conn.getresponse()
        raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return _unavailable_response(
            view_name,
            "workbench_steward_transport_error",
            f"Workbench steward socket call failed: {type(exc).__name__}",
        )
    finally:
        conn.close()

    if resp.status == 403:
        return {
            "ok": False,
            "view": view_name,
            "error": str(parsed.get("error") or "unauthorized_caller_role"),
        }
    if resp.status == 404:
        return {
            "ok": False,
            "view": view_name,
            "error": str(parsed.get("error") or "session_not_found"),
        }
    if resp.status != 200 or not isinstance(parsed, dict) or parsed.get("ok") is not True:
        return {
            "ok": False,
            "view": view_name,
            "error": str(parsed.get("error") or f"workbench_steward_http_{resp.status}"),
        }

    result = parsed.get("result")
    return {
        "ok": True,
        "view": view_name,
        "available": True,
        "source_id": "local",
        **(result if isinstance(result, dict) else {"data": result}),
    }
