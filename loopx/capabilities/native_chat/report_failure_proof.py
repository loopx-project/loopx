"""Scoped persistence and verification for report terminal-failure evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
import hashlib
import json
import logging
from pathlib import Path
import subprocess
from typing import Any

from ...file_lock import try_exclusive_file_lock
from ...registry import atomic_write_json
from ..periodic_report.private_generation import attempt_directory

REPORT_FAILURE_PROOF = "native-terminal-failure.json"
_LOG = logging.getLogger(__name__)


def report_failure_binding(original: Mapping[str, Any], window: dict[str, Any]) -> dict[str, str] | None:
    """Bind a terminal report host failure to one cadence window and native result."""
    request = original.get("loopx_request") or {}
    actor = request.get("actor") or {}
    result = (original.get("response") or {}).get("native_loopx") or {}
    failure = result.get("host_failure") or {}
    if (original.get("origin") != "native_loopx" or original.get("loopx_execution") is not True
            or original.get("status") != "completed" or original.get("session_id") != actor.get("session_id")
            or request.get("work_kind") != "report" or request.get("report_window_id") != window.get("window_id")
            or request.get("report_subscription_revision") != window.get("subscription_revision")
            or actor.get("goal_id") != window.get("goal_id") or actor.get("agent_id") != window.get("agent_id")
            or result.get("status") != "failed" or result.get("result_kind") != "host_failure"
            or failure.get("retryable") is not False):
        return None
    identity = {
        "session_id": original.get("session_id"),
        "chat_turn_id": original.get("turn_id"),
        "provider_turn_id": original.get("upstream_turn_id"),
        "turn_instance_id": request.get("turn_instance_id"),
        "window_id": window.get("window_id"),
        "subscription_revision": window.get("subscription_revision"),
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        return None
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {**identity, "native_result_sha256": hashlib.sha256(encoded).hexdigest()}


def _read_report_failure_record(runtime_root: Path, window: dict[str, Any]) -> dict[str, Any] | None:
    path = attempt_directory(runtime_root, window) / REPORT_FAILURE_PROOF
    try:
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        proof = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return proof if isinstance(proof, dict) else None


def _is_valid_retained_proof(proof: dict[str, Any] | None, binding: dict[str, str]) -> bool:
    return (
        isinstance(proof, dict)
        and set(proof) == {"binding", "provider_thread_id", "only_original_user_item"}
        and proof["binding"] == binding
        and proof["only_original_user_item"] is True
        and isinstance(proof["provider_thread_id"], str)
        and bool(proof["provider_thread_id"])
    )


def _is_contradicted_proof(proof: dict[str, Any] | None, binding: dict[str, str]) -> bool:
    if not isinstance(proof, dict):
        return False
    thread_id = proof.get("provider_thread_id")
    return (
        set(proof) == {"binding", "provider_thread_id", "only_original_user_item", "contradicted"}
        and proof["binding"] == binding
        and proof["only_original_user_item"] is False
        and proof["contradicted"] is True
        and (thread_id is None or isinstance(thread_id, str) and bool(thread_id))
    )


def _failure_proof_lock(folder: Path, *, operation: str, generation_locked: bool):
    if generation_locked:
        return nullcontext(folder / "generation.lock")
    return try_exclusive_file_lock(
        folder / "generation.lock",
        preserve_holder=True,
        operation=operation,
    )


def read_retained_report_failure(runtime_root: Path, original: Mapping[str, Any], window: dict[str, Any]) -> bool:
    """Scope retained provider evidence to the same failure and cadence window."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    return _is_valid_retained_proof(_read_report_failure_record(runtime_root, window), binding)


def is_report_failure_contradicted(runtime_root: Path, original: Mapping[str, Any], window: dict[str, Any]) -> bool:
    """Return True when live provider history has already contradicted this Turn's proof."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    return _is_contradicted_proof(_read_report_failure_record(runtime_root, window), binding)


def retain_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any], proof: Mapping[str, Any],
    *, generation_locked: bool = False,
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
    with _failure_proof_lock(
        folder, operation="retain_report_failure_observation", generation_locked=generation_locked,
    ) as ownership:
        if ownership is None or path.exists() or path.is_symlink():
            return
        # Read the original again after metadata I/O. Never overwrite a
        # prior receipt, contradiction, or copy the provider's live quota observation.
        current = store.load_turn(original["session_id"], original["turn_id"])
        if current is not None and report_failure_binding(current, window) == binding:
            atomic_write_json(path, {"binding": binding,
                "provider_thread_id": thread_id, "only_original_user_item": True})


def contradict_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any],
    proof: Mapping[str, Any] | None = None, *, generation_locked: bool = False,
) -> None:
    """Durably invalidate a report Turn's failure proof after live contradiction."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return
    if (isinstance(proof, Mapping) and proof.get("turn_id") is not None
            and proof.get("turn_id") != binding["provider_turn_id"]):
        return
    folder = attempt_directory(runtime_root, window)
    if folder.is_symlink() or not folder.is_dir():
        return
    path = folder / REPORT_FAILURE_PROOF
    with _failure_proof_lock(
        folder, operation="contradict_report_failure_observation", generation_locked=generation_locked,
    ) as ownership:
        if ownership is None or path.is_symlink():
            return
        existing = _read_report_failure_record(runtime_root, window)
        thread_id = proof.get("thread_id") if isinstance(proof, Mapping) else None
        if not isinstance(thread_id, str) or not thread_id:
            existing_thread = existing.get("provider_thread_id") if existing else None
            thread_id = existing_thread if isinstance(existing_thread, str) and existing_thread else None
        if _is_contradicted_proof(existing, binding) and existing.get("provider_thread_id") == thread_id:
            return
        # Read the original again after metadata I/O before recording the
        # contradiction for this exact Turn, window and native result digest.
        current = store.load_turn(original["session_id"], original["turn_id"])
        if current is not None and report_failure_binding(current, window) == binding:
            atomic_write_json(path, {
                "binding": binding,
                "provider_thread_id": thread_id,
                "only_original_user_item": False,
                "contradicted": True,
            })


def valid_report_failure_proof(
    original: Mapping[str, Any], threads: Sequence[str], proof: Mapping[str, Any] | None,
) -> bool:
    """Return True when a live provider observation proves a user-only host failure."""
    return (isinstance(proof, Mapping) and proof.get("contradicted") is not True
        and proof.get("turn_id") == original["upstream_turn_id"] and proof.get("thread_id") in threads
        and proof.get("only_original_user_item") is True and bool(proof.get("codex_error_info")))


def proof_targets_original_turn(
    original: Mapping[str, Any], threads: Sequence[str], proof: Mapping[str, Any] | None,
) -> bool:
    """Ensure a contradicted observation refers to this exact original thread/turn."""
    if not isinstance(proof, Mapping):
        return False
    thread_id = proof.get("thread_id")
    turn_id = proof.get("turn_id")
    if proof.get("contradicted") is True:
        return ((thread_id is None or thread_id in threads)
            and (turn_id is None or turn_id == original.get("upstream_turn_id")))
    return thread_id in threads and turn_id == original.get("upstream_turn_id")


def probe_report_failure(
    controller: Any, store: Any, original: Mapping[str, Any], *,
    include_contradiction: bool = False, contradiction_detail: bool = False,
    swallow_probe_errors: bool | None = None,
) -> tuple[list[str], dict[str, Any] | None] | None:
    """Query the original Session's native threads without starting or resuming work."""
    result = (original.get("response") or {}).get("native_loopx") or {}
    request = original.get("loopx_request") or {}
    if (original.get("status") != "completed" or request.get("work_kind") != "report"
            or result.get("result_kind") != "host_failure" or not original.get("upstream_turn_id")):
        return None
    from ...chat_agent import CodexChatAgentError
    from .codex_adapter import CodexAppServerAdapter
    from .codex_context import checked_session_home

    session = store.load_session(original["session_id"])
    if not session or not session.get("codex_home") or not session.get("upstream_thread_id"):
        return None
    history = session.get("upstream_context_history") or []
    if not isinstance(history, list) or any(not isinstance(item, dict)
            or not isinstance(item.get("thread_id"), str) or not item["thread_id"]
            for item in history):
        return None
    # Context replacement retains native provenance on this same Session.
    # Read the original refusal there; never resume a historical thread.
    threads = [session["upstream_thread_id"], *(item["thread_id"] for item in reversed(history))]
    swallow = include_contradiction if swallow_probe_errors is None else swallow_probe_errors
    try:
        proof = CodexAppServerAdapter.failed_request_observation(codex_bin=controller.codex_bin,
            codex_home=Path(session["codex_home"]), thread_ids=list(dict.fromkeys(threads)),
            model_auth_home=checked_session_home(controller.codex_home, session),
            turn_id=original["upstream_turn_id"], include_contradiction=include_contradiction,
            contradiction_detail=contradiction_detail)
    except (CodexChatAgentError, OSError, subprocess.SubprocessError):
        if not swallow:
            raise
        proof = None
    return threads, proof


def _try_retain_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any],
    proof: Mapping[str, Any], *, generation_locked: bool = False,
) -> None:
    try:
        retain_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked,
        )
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning("An original report's terminal failure proof could not be retained")


def _try_contradict_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any],
    proof: Mapping[str, Any] | None = None, *, generation_locked: bool = False,
) -> None:
    try:
        contradict_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked,
        )
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning("An original report's terminal failure contradiction could not be retained")


def handle_contradicted_report_observation(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any] | None,
    threads: Sequence[str], proof: Mapping[str, Any], *, generation_locked: bool = False,
) -> None:
    """Persist durable contradictions while holding transient in-progress threads safely."""
    if window is None or not proof_targets_original_turn(original, threads, proof):
        return
    if proof.get("in_progress") is True:
        if proof.get("target_in_progress") is True and read_retained_report_failure(runtime_root, original, window):
            _try_contradict_report_failure(
                runtime_root, store, original, window, proof, generation_locked=generation_locked,
            )
        return
    _try_contradict_report_failure(
        runtime_root, store, original, window, proof, generation_locked=generation_locked,
    )


def verify_report_window_failure(
    controller: Any, store: Any, original: Mapping[str, Any], window: dict[str, Any], *,
    probe_fn: Callable[..., tuple[list[str], dict[str, Any] | None] | None] | None = None,
    generation_locked: bool = False,
) -> bool:
    """Query live provider history first; fall back to un-contradicted proof only if unavailable."""
    runtime_root = controller.coordination_runtime_root
    probed = (
        probe_fn(original, include_contradiction=True, contradiction_detail=True)
        if probe_fn is not None
        else probe_report_failure(
            controller, store, original, include_contradiction=True, contradiction_detail=True,
        )
    )
    if probed is None:
        return False
    threads, proof = probed
    if proof is not None:
        if proof.get("contradicted") is True or not valid_report_failure_proof(original, threads, proof):
            handle_contradicted_report_observation(
                runtime_root, store, original, window, threads, proof, generation_locked=generation_locked,
            )
            return False
        if is_report_failure_contradicted(runtime_root, original, window):
            return False
        _try_retain_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked,
        )
        return True
    return read_retained_report_failure(runtime_root, original, window)


def resolve_report_quota_observation(
    controller: Any, store: Any, original: Mapping[str, Any], *,
    report_window: dict[str, Any] | None = None,
    probe_fn: Callable[..., tuple[list[str], dict[str, Any] | None] | None] | None = None,
    fallback_observe_fn: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Inspect live quota recovery for the original Turn without rewriting prior contradictions."""
    runtime_root = controller.coordination_runtime_root
    if report_window is not None and is_report_failure_contradicted(runtime_root, original, report_window):
        return None
    if fallback_observe_fn is not None:
        proof = fallback_observe_fn(original)
    else:
        probed = (
            probe_fn(
                original, include_contradiction=True, contradiction_detail=True, swallow_probe_errors=False,
            )
            if probe_fn is not None
            else probe_report_failure(
                controller, store, original,
                include_contradiction=True, contradiction_detail=True, swallow_probe_errors=False,
            )
        )
        if probed is None:
            return None
        threads, proof = probed
        if proof is None:
            return None
        if proof.get("contradicted") is True or not valid_report_failure_proof(original, threads, proof):
            handle_contradicted_report_observation(runtime_root, store, original, report_window, threads, proof)
            return None
    if proof is not None and report_window is not None:
        _try_retain_report_failure(runtime_root, store, original, report_window, proof)
    return proof
