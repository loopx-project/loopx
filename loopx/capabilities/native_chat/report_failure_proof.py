"""Scoped persistence and verification for report terminal-failure evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import time
from typing import Any

from ...file_lock import try_exclusive_file_lock
from ...registry import atomic_write_json
from ..periodic_report.cadence_journal import cadence_journal_path
from ..periodic_report.field_shapes import REPORT_IDENTITY_PATTERN
from ..periodic_report.private_generation import attempt_directory

REPORT_FAILURE_PROOF = "native-terminal-failure.json"
CONTRADICTED_REPORT_FAILURES = "contradicted-report-failures.json"
CONTRADICTED_FAILURES_SCHEMA = "periodic_report_contradicted_failures_v0"
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


def _is_antigravity_turn_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("agy_turn_")


def _is_valid_retained_proof(proof: dict[str, Any] | None, binding: dict[str, str]) -> bool:
    if _is_antigravity_turn_id(binding.get("provider_turn_id")):
        return (
            isinstance(proof, dict)
            and set(proof) == {"binding", "provider_thread_id", "adapter_kind", "cascade_idle", "no_final_response"}
            and proof["binding"] == binding
            and proof["adapter_kind"] == "antigravity_ls"
            and proof["cascade_idle"] is True
            and proof["no_final_response"] is True
            and isinstance(proof["provider_thread_id"], str)
            and bool(proof["provider_thread_id"])
        )
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


def _contradiction_ledger_path(runtime_root: Path, window: Mapping[str, Any]) -> Path | None:
    goal_id = window.get("goal_id")
    if not isinstance(goal_id, str) or not REPORT_IDENTITY_PATTERN.fullmatch(goal_id):
        return None
    return cadence_journal_path(runtime_root, goal_id).parent / CONTRADICTED_REPORT_FAILURES


def _binding_key(binding: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(binding), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_contradiction_ledger(ledger_path: Path) -> tuple[str, dict[str, Any]]:
    try:
        if ledger_path.parent.parent.is_symlink() or ledger_path.parent.is_symlink() or ledger_path.is_symlink():
            return "untrusted", {}
        if not ledger_path.exists():
            return "absent", {}
        if not ledger_path.is_file():
            return "untrusted", {}
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "untrusted", {}
    if (not isinstance(data, dict) or set(data) != {"schema_version", "entries"}
            or data.get("schema_version") != CONTRADICTED_FAILURES_SCHEMA
            or not isinstance(data.get("entries"), dict)):
        return "untrusted", {}
    return "ok", data["entries"]


def _durable_contradiction_status(
    runtime_root: Path, binding: dict[str, str], window: Mapping[str, Any],
) -> str:
    ledger_path = _contradiction_ledger_path(runtime_root, window)
    if ledger_path is None:
        return "untrusted"
    status, entries = _read_contradiction_ledger(ledger_path)
    if status != "ok":
        return status
    entry = entries.get(_binding_key(binding))
    if entry is None:
        return "absent"
    return "contradicted" if _is_contradicted_proof(entry, binding) else "untrusted"


def read_retained_report_failure(runtime_root: Path, original: Mapping[str, Any], window: dict[str, Any]) -> bool:
    """Scope retained provider evidence to the same failure and cadence window."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    if _durable_contradiction_status(runtime_root, binding, window) != "absent":
        return False
    return _is_valid_retained_proof(_read_report_failure_record(runtime_root, window), binding)


def is_report_failure_contradicted(runtime_root: Path, original: Mapping[str, Any], window: dict[str, Any]) -> bool:
    """Return True when live provider history has already contradicted this Turn's proof."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    if _durable_contradiction_status(runtime_root, binding, window) != "absent":
        return True
    return _is_contradicted_proof(_read_report_failure_record(runtime_root, window), binding)


def retain_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any], proof: Mapping[str, Any],
    *, generation_locked: bool = False,
) -> None:
    """Idempotently record a verified pre-execution failure under the generation lock."""
    binding = report_failure_binding(original, window)
    thread_id = proof.get("thread_id")
    if binding is None or proof.get("turn_id") != binding["provider_turn_id"] or not isinstance(thread_id, str) or not thread_id:
        return
    is_ag = _is_antigravity_turn_id(binding["provider_turn_id"])
    if is_ag:
        if (proof.get("adapter_kind") != "antigravity_ls"
                or proof.get("cascade_idle") is not True
                or proof.get("no_final_response") is not True):
            return
        record: dict[str, Any] = {
            "binding": binding,
            "provider_thread_id": thread_id,
            "adapter_kind": "antigravity_ls",
            "cascade_idle": True,
            "no_final_response": True,
        }
    else:
        if proof.get("only_original_user_item") is not True:
            return
        record = {
            "binding": binding,
            "provider_thread_id": thread_id,
            "only_original_user_item": True,
        }
    if _durable_contradiction_status(runtime_root, binding, window) != "absent":
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
            atomic_write_json(path, record)


def _invalidate_attempt_proof(
    runtime_root: Path, window: dict[str, Any], binding: dict[str, str],
    *, thread_id: str | None, generation_locked: bool, write_contradiction: bool,
) -> None:
    folder = attempt_directory(runtime_root, window)
    if folder.is_symlink() or not folder.is_dir():
        return
    path = folder / REPORT_FAILURE_PROOF
    try:
        with _failure_proof_lock(
            folder, operation="contradict_report_failure_observation", generation_locked=generation_locked,
        ) as ownership:
            if ownership is None or path.is_symlink():
                return
            existing = _read_report_failure_record(runtime_root, window)
            if _is_contradicted_proof(existing, binding) and existing.get("provider_thread_id") == thread_id:
                return
            if not _is_contradicted_proof(existing, binding) and path.exists():
                path.unlink(missing_ok=True)
            if not write_contradiction:
                return
            atomic_write_json(path, {
                "binding": binding,
                "provider_thread_id": thread_id,
                "only_original_user_item": False,
                "contradicted": True,
            })
            persisted = _read_report_failure_record(runtime_root, window)
            if not _is_contradicted_proof(persisted, binding) or persisted.get("provider_thread_id") != thread_id:
                _LOG.warning("Attempt-level report failure proof readback did not match after durable contradiction")
    except OSError:
        _LOG.warning("Could not invalidate attempt-level report failure proof")


def contradict_report_failure(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any],
    proof: Mapping[str, Any] | None = None, *, generation_locked: bool = False,
) -> None:
    """Durably invalidate a report Turn's failure proof after live contradiction."""
    binding = report_failure_binding(original, window)
    if binding is None:
        raise ValueError("contradiction requires a valid report failure binding")
    if (isinstance(proof, Mapping) and proof.get("turn_id") is not None
            and proof.get("turn_id") != binding["provider_turn_id"]):
        raise ValueError("contradiction proof turn does not match original report failure")
    ledger_path = _contradiction_ledger_path(runtime_root, window)
    if ledger_path is None:
        raise ValueError("contradiction requires a valid report goal identity")
    parent = ledger_path.parent
    if parent.parent.is_symlink() or parent.is_symlink():
        raise OSError("periodic report directory is unavailable for durable contradiction")
    parent.mkdir(parents=True, exist_ok=True)
    existing_attempt = _read_report_failure_record(runtime_root, window)
    thread_id = proof.get("thread_id") if isinstance(proof, Mapping) else None
    if not isinstance(thread_id, str) or not thread_id:
        existing_thread = existing_attempt.get("provider_thread_id") if existing_attempt else None
        thread_id = existing_thread if isinstance(existing_thread, str) and existing_thread else None
    try:
        with try_exclusive_file_lock(
            ledger_path, preserve_holder=True, operation="persist_contradicted_report_failure",
        ) as ledger_ownership:
            if ledger_ownership is None or ledger_path.is_symlink():
                raise OSError("durable report contradiction lock or path is unavailable")
            # Read the original again after metadata I/O before recording the
            # contradiction for this exact Turn, window and native result digest.
            current = store.load_turn(original["session_id"], original["turn_id"])
            if current is None or report_failure_binding(current, window) != binding:
                raise RuntimeError("original report failure binding changed before contradiction persistence")
            status, entries = _read_contradiction_ledger(ledger_path)
            if status == "untrusted":
                raise OSError("durable report contradiction ledger is corrupt or untrusted")
            key = _binding_key(binding)
            existing_entry = entries.get(key)
            if not isinstance(thread_id, str) or not thread_id:
                ledger_thread = existing_entry.get("provider_thread_id") if isinstance(existing_entry, dict) else None
                thread_id = ledger_thread if isinstance(ledger_thread, str) and ledger_thread else None
            if not (_is_contradicted_proof(existing_entry, binding) and existing_entry.get("provider_thread_id") == thread_id):
                updated_entries = dict(entries)
                updated_entries[key] = {
                    "binding": binding,
                    "provider_thread_id": thread_id,
                    "only_original_user_item": False,
                    "contradicted": True,
                }
                atomic_write_json(ledger_path, {
                    "schema_version": CONTRADICTED_FAILURES_SCHEMA,
                    "entries": updated_entries,
                })
                readback_status, readback_entries = _read_contradiction_ledger(ledger_path)
                persisted = readback_entries.get(key) if readback_status == "ok" else None
                if not _is_contradicted_proof(persisted, binding) or persisted.get("provider_thread_id") != thread_id:
                    raise OSError("durable report contradiction failed readback verification")
    except OSError:
        # Best-effort cleanup of any removable positive attempt file before failing closed.
        _invalidate_attempt_proof(
            runtime_root, window, binding, thread_id=thread_id,
            generation_locked=generation_locked, write_contradiction=False,
        )
        raise
    _invalidate_attempt_proof(
        runtime_root, window, binding, thread_id=thread_id,
        generation_locked=generation_locked, write_contradiction=True,
    )


def valid_report_failure_proof(
    original: Mapping[str, Any], threads: Sequence[str], proof: Mapping[str, Any] | None,
) -> bool:
    """Return True when a live provider observation proves a terminal host failure with no generated answer."""
    upstream_turn_id = original.get("upstream_turn_id")
    if _is_antigravity_turn_id(upstream_turn_id):
        return (
            isinstance(proof, Mapping)
            and proof.get("contradicted") is not True
            and proof.get("adapter_kind") == "antigravity_ls"
            and proof.get("turn_id") == upstream_turn_id
            and proof.get("thread_id") in threads
            and proof.get("cascade_idle") is True
            and proof.get("no_final_response") is True
        )
    return (isinstance(proof, Mapping) and proof.get("contradicted") is not True
        and proof.get("turn_id") == upstream_turn_id and proof.get("thread_id") in threads
        and proof.get("only_original_user_item") is True and bool(proof.get("codex_error_info")))


def proof_targets_original_turn(
    original: Mapping[str, Any], threads: Sequence[str], proof: Mapping[str, Any] | None,
) -> bool:
    """Ensure an observation refers to this exact original thread/turn."""
    if not isinstance(proof, Mapping):
        return False
    thread_id = proof.get("thread_id")
    turn_id = proof.get("turn_id")
    return isinstance(thread_id, str) and thread_id in threads and turn_id == original.get("upstream_turn_id")


def probe_report_failure(
    controller: Any, store: Any, original: Mapping[str, Any], *,
    include_contradiction: bool = False, contradiction_detail: bool = False,
    swallow_probe_errors: bool | None = None,
) -> tuple[list[str], dict[str, Any] | None] | None:
    """Query the original Session's native threads without starting or resuming work."""
    result = (original.get("response") or {}).get("native_loopx") or {}
    request = original.get("loopx_request") or {}
    upstream_turn_id = original.get("upstream_turn_id")
    if (original.get("status") != "completed" or request.get("work_kind") != "report"
            or result.get("result_kind") != "host_failure" or not upstream_turn_id):
        return None
    if _is_antigravity_turn_id(upstream_turn_id):
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
    # Read the original refusal there; exclude non-Codex cascades.
    raw_threads = [
        *([session["upstream_thread_id"]] if session.get("agent_id") != "antigravity" else []),
        *(item["thread_id"] for item in reversed(history) if item.get("agent_id") != "antigravity"),
    ]
    threads = list(dict.fromkeys(raw_threads))
    if not threads:
        return None
    swallow = include_contradiction if swallow_probe_errors is None else swallow_probe_errors
    try:
        proof = CodexAppServerAdapter.failed_request_observation(codex_bin=controller.codex_bin,
            codex_home=Path(session["codex_home"]), thread_ids=threads,
            model_auth_home=checked_session_home(controller.codex_home, session),
            turn_id=upstream_turn_id, include_contradiction=include_contradiction,
            contradiction_detail=contradiction_detail)
    except (CodexChatAgentError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        if not swallow:
            raise
        proof = None
    return threads, proof


def _extract_antigravity_turn_cascade(
    store: Any, original: Mapping[str, Any],
) -> tuple[dict[str, Any], str, list[dict[str, Any]]] | None:
    """Resolve one Antigravity cascade and stored events when no final answer exists."""
    upstream_turn_id = original.get("upstream_turn_id")
    session = store.load_session(original["session_id"])
    if not session or original.get("delta_count") != 0 or not _is_antigravity_turn_id(upstream_turn_id):
        return None
    try:
        events = store.events_after(original["session_id"], original["turn_id"], None)
    except (OSError, ValueError):
        return None
    if any(row.get("kind") in {"answer.final", "turn.native_result"} for row in events):
        return None
    started = [
        (row.get("payload") or {}).get("threadId")
        for row in events
        if row.get("kind") == "turn.started" and isinstance(row.get("payload"), Mapping)
        and row["payload"].get("upstream_turn_id") == upstream_turn_id
    ]
    if len(started) != 1 or not isinstance(started[0], str) or not started[0]:
        return None
    return session, started[0], events


def _antigravity_trajectory_steps(
    url: str, csrf_token: str, cascade_id: str, traj: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    """Load complete trajectory steps from an idle Antigravity cascade."""
    from ...chat_agent import CodexChatAgentError
    from .antigravity_adapter import _read_trajectory_steps, _rpc_post

    if (traj.get("trajectory") or {}).get("cascadeId") != cascade_id:
        return None
    total = traj.get("numTotalSteps")
    inline_steps = (traj.get("trajectory") or {}).get("steps")
    if type(total) is int and total > 0:
        if isinstance(inline_steps, list) and len(inline_steps) == total and all(isinstance(s, dict) for s in inline_steps):
            return inline_steps
        try:
            loaded = _read_trajectory_steps(
                lambda offset, end: _rpc_post(
                    url,
                    csrf_token,
                    "GetCascadeTrajectorySteps",
                    {"cascadeId": cascade_id, "stepOffset": offset, "startIndex": offset, "endIndex": end},
                    timeout=5.0,
                ).get("steps"),
                start=0,
                total=total,
            )
            if loaded is None:
                return None
            after = _rpc_post(url, csrf_token, "GetCascadeTrajectory", {"cascadeId": cascade_id}, timeout=2.5)
        except (CodexChatAgentError, OSError, ValueError):
            return None
        if (after.get("status") != "CASCADE_RUN_STATUS_IDLE"
                or after.get("numTotalSteps") != total
                or (after.get("trajectory") or {}).get("cascadeId") != cascade_id):
            return None
        return [step for _, step in loaded[0]]
    if total == 0 or (total is None and isinstance(inline_steps, list) and len(inline_steps) == 0):
        return []
    return None


def _antigravity_has_running_step(steps: Sequence[Mapping[str, Any]]) -> bool:
    return any(step.get("status") == "CORTEX_STEP_STATUS_RUNNING" for step in steps)


def _antigravity_has_final_response(steps: Sequence[Mapping[str, Any]]) -> bool:
    for step in steps:
        if step.get("type") != "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
            continue
        planner = step.get("plannerResponse")
        if not isinstance(planner, Mapping) or planner.get("toolCalls"):
            continue
        answer = planner.get("modifiedResponse") or planner.get("response")
        if isinstance(answer, str) and answer.strip():
            return True
    return False


def _probe_antigravity_report_failure(
    store: Any, original: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any] | None] | None:
    """Query the Antigravity LS trajectory without starting or resuming work."""
    result = (original.get("response") or {}).get("native_loopx") or {}
    request = original.get("loopx_request") or {}
    upstream_turn_id = original.get("upstream_turn_id")
    if (original.get("status") != "completed" or request.get("work_kind") != "report"
            or result.get("result_kind") != "host_failure" or not _is_antigravity_turn_id(upstream_turn_id)):
        return None
    extracted = _extract_antigravity_turn_cascade(store, original)
    if extracted is None:
        return None
    _, cascade_id, _ = extracted
    from ...chat_agent import CodexChatAgentError
    from .antigravity_adapter import _rpc_post, discover_antigravity_language_server

    found = discover_antigravity_language_server(
        include_model_config=False,
        deadline=time.monotonic() + 3.0,
    )
    if found is None:
        return [cascade_id], None
    url = str(found["url"])
    csrf_token = str(found["csrf_token"])
    try:
        traj = _rpc_post(url, csrf_token, "GetCascadeTrajectory", {"cascadeId": cascade_id}, timeout=2.5)
    except (CodexChatAgentError, OSError, ValueError):
        return [cascade_id], None
    if (traj.get("trajectory") or {}).get("cascadeId") != cascade_id:
        return [cascade_id], None
    base_proof = {
        "adapter_kind": "antigravity_ls",
        "turn_id": upstream_turn_id,
        "thread_id": cascade_id,
    }
    if traj.get("status") != "CASCADE_RUN_STATUS_IDLE":
        return [cascade_id], {**base_proof, "in_progress": True, "target_in_progress": True, "contradicted": True}
    steps = _antigravity_trajectory_steps(url, csrf_token, cascade_id, traj)
    if steps is None:
        return [cascade_id], None
    if _antigravity_has_running_step(steps):
        return [cascade_id], {**base_proof, "in_progress": True, "target_in_progress": True, "contradicted": True}
    if _antigravity_has_final_response(steps):
        return [cascade_id], {
            **base_proof,
            "cascade_idle": True,
            "no_final_response": False,
            "contradicted": True,
            "explicit_contradiction": True,
        }
    return [cascade_id], {**base_proof, "cascade_idle": True, "no_final_response": True}


def observe_antigravity_report_failure(store: Any, original: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read an Antigravity report timeout/failure observation without starting work."""
    extracted = _extract_antigravity_turn_cascade(store, original)
    if extracted is None:
        return None
    session, cascade_id, events = extracted
    upstream_turn_id = str(original["upstream_turn_id"])
    native_timeout = any(
        row.get("kind") == "agent.phase" and isinstance(row.get("payload"), Mapping)
        and row["payload"].get("method") == "native/timeout"
        for row in events
    )
    from .antigravity_adapter import AntigravityAdapter
    return AntigravityAdapter.failed_report_observation(
        cascade_id=cascade_id,
        turn_id=upstream_turn_id,
        native_timeout=native_timeout,
        model_id=session.get("agent_model_id"),
        reasoning_effort=session.get("agent_reasoning_effort"),
    )


def observe_report_failure(controller: Any, store: Any, original: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the original native failure without starting or resuming work."""
    result = (original.get("response") or {}).get("native_loopx") or {}
    request = original.get("loopx_request") or {}
    upstream_turn_id = original.get("upstream_turn_id")
    if (original.get("status") != "completed" or request.get("work_kind") != "report"
            or result.get("result_kind") != "host_failure" or not upstream_turn_id):
        return None
    if _is_antigravity_turn_id(upstream_turn_id):
        return observe_antigravity_report_failure(store, original)
    probed = probe_report_failure(controller, store, original, swallow_probe_errors=True)
    if probed is None:
        return None
    threads, proof = probed
    return proof if valid_report_failure_proof(original, threads, proof) else None


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


def handle_contradicted_report_observation(
    runtime_root: Path, store: Any, original: Mapping[str, Any], window: dict[str, Any] | None,
    threads: Sequence[str], proof: Mapping[str, Any], *, generation_locked: bool = False,
) -> None:
    """Persist durable contradictions while holding transient in-progress threads safely."""
    if window is None or not proof_targets_original_turn(original, threads, proof):
        return
    if proof.get("in_progress") is True:
        if proof.get("target_in_progress") is True and read_retained_report_failure(runtime_root, original, window):
            contradict_report_failure(
                runtime_root, store, original, window, proof, generation_locked=generation_locked,
            )
        return
    if proof.get("explicit_contradiction") is True:
        contradict_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked,
        )


def verify_report_window_failure(
    controller: Any, store: Any, original: Mapping[str, Any], window: dict[str, Any], *,
    probe_fn: Callable[..., tuple[list[str], dict[str, Any] | None] | None] | None = None,
    generation_locked: bool = False,
) -> bool:
    """Query live provider history first; fall back to un-contradicted proof only if unavailable."""
    runtime_root = controller.coordination_runtime_root
    binding = report_failure_binding(original, window)
    if binding is None or _durable_contradiction_status(runtime_root, binding, window) == "untrusted":
        return False
    if probe_fn is not None:
        probed = probe_fn(original, include_contradiction=True, contradiction_detail=True)
    elif _is_antigravity_turn_id(original.get("upstream_turn_id")):
        probed = _probe_antigravity_report_failure(store, original)
    else:
        probed = probe_report_failure(
            controller, store, original, include_contradiction=True, contradiction_detail=True,
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
    if is_report_failure_contradicted(runtime_root, original, window):
        return False
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
                original, include_contradiction=True, contradiction_detail=True, swallow_probe_errors=True,
            )
            if probe_fn is not None
            else probe_report_failure(
                controller, store, original,
                include_contradiction=True, contradiction_detail=True, swallow_probe_errors=True,
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


def build_report_quota_request(
    controller: Any, store: Any, original: Mapping[str, Any], *,
    report_window: dict[str, Any] | None = None,
    fallback_observe_fn: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Observe quota recovery, never resume or rewrite the failed native Turn."""
    request = original.get("loopx_request") or {}
    if request.get("report_quota_recovery") is not None or request.get("report_timeout_recovery") is not None:
        return None
    proof = resolve_report_quota_observation(
        controller, store, original, report_window=report_window, fallback_observe_fn=fallback_observe_fn,
    )
    if (not proof or proof.get("only_original_user_item") is not True
            or proof.get("codex_error_info") != "usageLimitExceeded" or proof.get("quota_available") is not True):
        return None
    return {**request, "turn_instance_id": request["turn_instance_id"] + ".quota-recovery",
        "report_quota_recovery": {"original_turn_id": original["turn_id"], "native_observation": proof}}


def build_report_timeout_request(
    original: Mapping[str, Any], *,
    observe_fn: Callable[[Mapping[str, Any]], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Observe idle Antigravity timeout for one bounded isolated-cascade retry."""
    request = original.get("loopx_request") or {}
    upstream_turn_id = original.get("upstream_turn_id")
    if (request.get("report_quota_recovery") is not None or request.get("report_timeout_recovery") is not None
            or not isinstance(upstream_turn_id, str) or not upstream_turn_id.startswith("agy_turn_")):
        return None
    proof = observe_fn(original)
    if (not proof or proof.get("adapter_kind") != "antigravity_ls"
            or proof.get("cascade_idle") is not True or proof.get("no_final_response") is not True
            or proof.get("native_timeout") is not True or proof.get("quota_available") is not True):
        return None
    return {**request, "turn_instance_id": request["turn_instance_id"] + ".timeout-recovery",
        "report_timeout_recovery": {"original_turn_id": original["turn_id"], "native_observation": proof}}
