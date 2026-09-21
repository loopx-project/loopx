"""Apply the built-in Goal lifecycle contract through a configured SSH host."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess

from ..status.ssh_host_catalog import configured_ssh_host_aliases


REMOTE_GOAL_LIFECYCLE_SCHEMA_VERSION = "loopx_remote_goal_lifecycle_v1"
_REMOTE_REGISTRY = "$HOME/.codex/loopx/registry.global.json"
_REMOTE_LOOPX_PREFIX = (
    'bin="$HOME/.local/bin/loopx"; '
    '[ -x "$bin" ] || bin="$(command -v loopx || true)"; '
    '[ -n "$bin" ] || { echo "loopx not found on remote" >&2; exit 127; }; '
)


def _required_text(value: object, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\0" in text:
        raise ValueError(f"{field} must be bounded non-empty text")
    return text


def apply_ssh_goal_lifecycle(
    *,
    host_alias: str,
    goal_id: str,
    operation: str,
    reason: str | None = None,
    ssh_config_path: Path | None = None,
    timeout_seconds: float = 45.0,
) -> dict[str, object]:
    """Run one stop/resume transition on the selected remote authority.

    The SSH alias is accepted only from the owner's configured host catalog.
    Dynamic lifecycle values are shell-quoted and the remote command never
    falls back to a local registry.
    """

    alias = _required_text(host_alias, field="host alias", limit=255)
    if alias not in configured_ssh_host_aliases(ssh_config_path):
        raise ValueError(f"unknown SSH host alias: {alias}")
    normalized_goal_id = _required_text(goal_id, field="goal id", limit=255)
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in {"stop", "resume"}:
        raise ValueError("remote Goal lifecycle operation must be stop or resume")
    normalized_reason = _required_text(
        reason or "Updated from the owner workspace through SSH",
        field="reason",
        limit=600,
    )

    command = _REMOTE_LOOPX_PREFIX + "exec " + " ".join(
        [
            '"$bin"',
            "--format",
            "json",
            "--registry",
            f'"{_REMOTE_REGISTRY}"',
            "goal-lifecycle",
            "--goal-id",
            shlex.quote(normalized_goal_id),
            "--operation",
            normalized_operation,
            "--actor-kind",
            "owner",
            "--reason",
            shlex.quote(normalized_reason),
            "--execute",
        ]
    )
    try:
        completed = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", alias, command],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("remote Goal lifecycle transport is unavailable") from exc

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        if completed.returncode == 127:
            raise ValueError("LoopX is unavailable on the selected SSH host") from exc
        raise ValueError("remote Goal lifecycle returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise ValueError("remote Goal lifecycle returned an invalid response")
    if completed.returncode != 0 or payload.get("ok") is not True:
        error_kind = str(payload.get("error_kind") or "remote_goal_lifecycle_failed")
        raise ValueError(f"remote Goal lifecycle failed ({error_kind})")
    readback = payload.get("readback")
    expected_state = "stopped" if normalized_operation == "stop" else "active"
    if (
        payload.get("schema_version") != "loopx_goal_activation_transition_v1"
        or payload.get("goal_id") != normalized_goal_id
        or payload.get("execute") is not True
        or payload.get("after_state") != expected_state
        or not isinstance(readback, dict)
        or readback.get("verified") is not True
    ):
        raise ValueError("remote Goal lifecycle readback did not verify")

    return {
        "ok": True,
        "schema_version": REMOTE_GOAL_LIFECYCLE_SCHEMA_VERSION,
        "host_alias": alias,
        "goal_id": normalized_goal_id,
        "operation": normalized_operation,
        "activation_state": expected_state,
        "changed": bool(payload.get("changed")),
        "projection_verified": True,
    }
