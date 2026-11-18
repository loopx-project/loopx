"""Scoped persistence for verified report terminal-failure evidence."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from ...file_lock import try_exclusive_file_lock
from ...registry import atomic_write_json
from ..periodic_report.private_generation import attempt_directory

REPORT_FAILURE_PROOF = "native-terminal-failure.json"


def report_failure_binding(original: Mapping[str, Any], window: dict[str, Any]) -> dict[str, str] | None:
    """Bind a terminal report host failure to one cadence window and native result."""
    request = original.get("loopx_request") or {}
    actor = request.get("actor") or {}
    result = (original.get("response") or {}).get("native_loopx") or {}
    failure = result.get("host_failure") or {}
    if (original.get("origin") != "native_loopx" or original.get("loopx_execution") is not True
            or original.get("status") != "completed" or original.get("session_id") != actor.get("session_id")
            or request.get("work_kind") != "report" or request.get("report_window_id") != window["window_id"]
            or request.get("report_subscription_revision") != window["subscription_revision"]
            or actor.get("goal_id") != window["goal_id"] or actor.get("agent_id") != window["agent_id"]
            or result.get("status") != "failed" or result.get("result_kind") != "host_failure"
            or failure.get("retryable") is not False):
        return None
    identity = {
        "session_id": original.get("session_id"),
        "chat_turn_id": original.get("turn_id"),
        "provider_turn_id": original.get("upstream_turn_id"),
        "turn_instance_id": request.get("turn_instance_id"),
        "window_id": window["window_id"],
        "subscription_revision": window["subscription_revision"],
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        return None
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**identity, "native_result_sha256": hashlib.sha256(encoded).hexdigest()}


def read_retained_report_failure(runtime_root: Path, original: Mapping[str, Any], window: dict[str, Any]) -> bool:
    """Scope retained provider evidence to the same failure and cadence window."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    path = attempt_directory(runtime_root, window) / REPORT_FAILURE_PROOF
    try:
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            return False
        proof = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(proof, dict)
        and set(proof) == {"binding", "provider_thread_id", "only_original_user_item"}
        and proof["binding"] == binding and proof["only_original_user_item"] is True
        and isinstance(proof["provider_thread_id"], str) and bool(proof["provider_thread_id"]))


def retain_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any], proof: Mapping[str, Any],
) -> None:
    """Idempotently record a verified pre-execution failure under the generation lock."""
    binding = report_failure_binding(original, window)
    thread_id = proof.get("thread_id")
    if (binding is None or proof.get("turn_id") != binding["provider_turn_id"]
            or proof.get("only_original_user_item") is not True
            or not isinstance(thread_id, str) or not thread_id):
        return
    folder = attempt_directory(runtime_root, window)
    if folder.is_symlink() or not folder.is_dir():
        return
    path = folder / REPORT_FAILURE_PROOF
    with try_exclusive_file_lock(folder / "generation.lock", preserve_holder=True,
            operation="retain_report_failure_observation") as ownership:
        if ownership is None or path.exists() or path.is_symlink():
            return
        # Read the original again after metadata I/O. Never overwrite a
        # prior receipt or copy the provider's live quota observation.
        current = store.load_turn(original["session_id"], original["turn_id"])
        if current is not None and report_failure_binding(current, window) == binding:
            atomic_write_json(path, {"binding": binding,
                "provider_thread_id": thread_id, "only_original_user_item": True})
