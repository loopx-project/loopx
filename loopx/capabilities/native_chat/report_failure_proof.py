"""Scoped persistence and verification for report terminal-failure evidence."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
import logging
from pathlib import Path
from typing import cast

from ...file_lock import try_exclusive_file_lock
from ...registry import atomic_write_json
from ..periodic_report.private_generation import attempt_directory
from .report_failure_ledger import (
    CONTRADICTED_FAILURES_SCHEMA as CONTRADICTED_FAILURES_SCHEMA,
    CONTRADICTED_REPORT_FAILURES as CONTRADICTED_REPORT_FAILURES,
    REPORT_FAILURE_PROOF as REPORT_FAILURE_PROOF,
    _binding_attempt_state,
    _binding_key as _binding_key,
    _contradiction_ledger_path as _contradiction_ledger_path,
    _durable_contradiction_status as _durable_contradiction_status,
    _is_contradicted_proof as _is_contradicted_proof,
    _is_exact_contradiction,
    _is_valid_retained_proof as _is_valid_retained_proof,
    _read_contradiction_ledger as _read_contradiction_ledger,
    _read_report_failure_record as _read_report_failure_record,
    is_report_failure_contradicted as is_report_failure_contradicted,
    read_retained_report_failure as read_retained_report_failure,
    report_failure_binding as report_failure_binding,
)
from .report_failure_probe import (
    ProofMapping as ProofMapping,
    ProofObject as ProofObject,
    ReportProofController as ReportProofController,
    ReportProofStore as ReportProofStore,
    TurnLoader as TurnLoader,
    _antigravity_has_final_response as _antigravity_has_final_response,
    _antigravity_has_running_step as _antigravity_has_running_step,
    _antigravity_trajectory_steps as _antigravity_trajectory_steps,
    _extract_antigravity_turn_cascade as _extract_antigravity_turn_cascade,
    _is_antigravity_turn_id as _is_antigravity_turn_id,
    _is_completed_host_failure_report,
    _mapping,
    _object_dict,
    _probe_antigravity_report_failure as _probe_antigravity_report_failure,
    observe_antigravity_report_failure as observe_antigravity_report_failure,
    probe_report_failure as probe_report_failure,
    proof_targets_original_turn as proof_targets_original_turn,
    valid_report_failure_proof as valid_report_failure_proof,
)

_LOG = logging.getLogger(__name__)


def _failure_proof_lock(
    folder: Path,
    *,
    operation: str,
    generation_locked: bool,
) -> AbstractContextManager[object]:
    if generation_locked:
        return nullcontext(folder / "generation.lock")
    return cast(
        AbstractContextManager[object],
        try_exclusive_file_lock(
            folder / "generation.lock",
            preserve_holder=True,
            operation=operation,
        ),
    )


def retain_report_failure(
    runtime_root: Path,
    store: TurnLoader,
    original: ProofMapping,
    window: ProofMapping,
    proof: ProofMapping,
    *,
    generation_locked: bool = False,
) -> None:
    """Idempotently record a verified pre-execution failure under the generation lock."""
    binding = report_failure_binding(original, window)
    thread_id = proof.get("thread_id")
    if (
        binding is None
        or proof.get("turn_id") != binding["provider_turn_id"]
        or not isinstance(thread_id, str)
        or not thread_id
    ):
        return
    if _is_antigravity_turn_id(binding["provider_turn_id"]):
        if (
            proof.get("adapter_kind") != "antigravity_ls"
            or proof.get("cascade_idle") is not True
            or proof.get("no_final_response") is not True
        ):
            return
        record: ProofObject = {
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
    folder = attempt_directory(runtime_root, _object_dict(window))
    if folder.is_symlink() or not folder.is_dir():
        return
    path = folder / REPORT_FAILURE_PROOF
    with _failure_proof_lock(
        folder, operation="retain_report_failure_observation", generation_locked=generation_locked
    ) as ownership:
        if ownership is None or path.exists() or path.is_symlink():
            return
        # Read the original again after metadata I/O. Never overwrite a
        # prior receipt, contradiction, or copy the provider's live quota observation.
        current = store.load_turn(cast(str, original["session_id"]), cast(str, original["turn_id"]))
        if current is not None and report_failure_binding(current, window) == binding:
            atomic_write_json(path, record)


def _invalidate_attempt_proof(
    runtime_root: Path,
    window: ProofMapping,
    binding: Mapping[str, str],
    *,
    thread_id: str | None,
    generation_locked: bool,
    write_contradiction: bool,
) -> None:
    folder = attempt_directory(runtime_root, _object_dict(window))
    if folder.is_symlink() or not folder.is_dir():
        return
    path = folder / REPORT_FAILURE_PROOF
    try:
        with _failure_proof_lock(
            folder,
            operation="contradict_report_failure_observation",
            generation_locked=generation_locked,
        ) as ownership:
            if ownership is None or path.is_symlink():
                return
            existing = _read_report_failure_record(runtime_root, window)
            if _is_exact_contradiction(existing, binding, thread_id):
                return
            if not _is_contradicted_proof(existing, binding) and path.exists():
                path.unlink(missing_ok=True)
            if not write_contradiction:
                return
            atomic_write_json(
                path,
                {
                    "binding": binding,
                    "provider_thread_id": thread_id,
                    "only_original_user_item": False,
                    "contradicted": True,
                },
            )
            persisted = _read_report_failure_record(runtime_root, window)
            if not _is_exact_contradiction(persisted, binding, thread_id):
                _LOG.warning(
                    "Attempt-level report failure proof readback did not match after durable contradiction"
                )
    except OSError:
        _LOG.warning("Could not invalidate attempt-level report failure proof")


def contradict_report_failure(
    runtime_root: Path,
    store: TurnLoader,
    original: ProofMapping,
    window: ProofMapping,
    proof: ProofMapping | None = None,
    *,
    generation_locked: bool = False,
) -> None:
    """Durably invalidate a report Turn's failure proof after live contradiction."""
    binding = report_failure_binding(original, window)
    if binding is None:
        raise ValueError("contradiction requires a valid report failure binding")
    if (
        isinstance(proof, Mapping)
        and proof.get("turn_id") is not None
        and proof.get("turn_id") != binding["provider_turn_id"]
    ):
        raise ValueError("contradiction proof turn does not match original report failure")
    ledger_path = _contradiction_ledger_path(runtime_root, window)
    if ledger_path is None:
        raise ValueError("contradiction requires a valid report goal identity")
    parent = ledger_path.parent
    if parent.parent.is_symlink() or parent.is_symlink():
        raise OSError("periodic report directory is unavailable for durable contradiction")
    parent.mkdir(parents=True, exist_ok=True)
    raw_thread = proof.get("thread_id") if isinstance(proof, Mapping) else None
    thread_id: str | None = (
        raw_thread if isinstance(raw_thread, str) and raw_thread else None
    )
    if thread_id is None:
        existing_attempt = _read_report_failure_record(runtime_root, window)
        existing_thread = existing_attempt.get("provider_thread_id") if existing_attempt else None
        if isinstance(existing_thread, str) and existing_thread:
            thread_id = existing_thread
    try:
        with try_exclusive_file_lock(
            ledger_path, preserve_holder=True, operation="persist_contradicted_report_failure"
        ) as ledger_ownership:
            if ledger_ownership is None or ledger_path.is_symlink():
                raise OSError("durable report contradiction lock or path is unavailable")
            # Read the original again after metadata I/O before recording the
            # contradiction for this exact Turn, window and native result digest.
            current = store.load_turn(
                cast(str, original["session_id"]), cast(str, original["turn_id"])
            )
            if current is None or report_failure_binding(current, window) != binding:
                raise RuntimeError(
                    "original report failure binding changed before contradiction persistence"
                )
            status, entries = _read_contradiction_ledger(ledger_path)
            if status == "untrusted":
                raise OSError("durable report contradiction ledger is corrupt or untrusted")
            key = _binding_key(binding)
            existing_entry = entries.get(key)
            if thread_id is None and isinstance(existing_entry, Mapping):
                ledger_thread = existing_entry.get("provider_thread_id")
                if isinstance(ledger_thread, str) and ledger_thread:
                    thread_id = ledger_thread
            if not _is_exact_contradiction(existing_entry, binding, thread_id):
                updated_entries = dict(entries)
                updated_entries[key] = {
                    "binding": binding,
                    "provider_thread_id": thread_id,
                    "only_original_user_item": False,
                    "contradicted": True,
                }
                atomic_write_json(
                    ledger_path,
                    {"schema_version": CONTRADICTED_FAILURES_SCHEMA, "entries": updated_entries},
                )
                readback_status, readback_entries = _read_contradiction_ledger(ledger_path)
                persisted = readback_entries.get(key) if readback_status == "ok" else None
                if not _is_exact_contradiction(persisted, binding, thread_id):
                    raise OSError("durable report contradiction failed readback verification")
    except OSError:
        # Best-effort cleanup of any removable positive attempt file before failing closed.
        _invalidate_attempt_proof(
            runtime_root,
            window,
            binding,
            thread_id=thread_id,
            generation_locked=generation_locked,
            write_contradiction=False,
        )
        raise
    _invalidate_attempt_proof(
        runtime_root,
        window,
        binding,
        thread_id=thread_id,
        generation_locked=generation_locked,
        write_contradiction=True,
    )


def observe_report_failure(
    controller: ReportProofController,
    store: ReportProofStore,
    original: ProofMapping,
) -> ProofObject | None:
    """Read the original native failure without starting or resuming work."""
    upstream_turn_id = _is_completed_host_failure_report(original)
    if not upstream_turn_id:
        return None
    if _is_antigravity_turn_id(upstream_turn_id):
        return observe_antigravity_report_failure(store, original)
    probed = probe_report_failure(controller, store, original, swallow_probe_errors=True)
    if probed is None:
        return None
    threads, proof = probed
    return proof if valid_report_failure_proof(original, threads, proof) else None


def _try_retain_report_failure(
    runtime_root: Path,
    store: TurnLoader,
    original: ProofMapping,
    window: ProofMapping,
    proof: ProofMapping,
    *,
    generation_locked: bool = False,
) -> None:
    try:
        retain_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked
        )
    except (OSError, ValueError, TypeError, KeyError):
        _LOG.warning("An original report's terminal failure proof could not be retained")


def handle_contradicted_report_observation(
    runtime_root: Path,
    store: TurnLoader,
    original: ProofMapping,
    window: ProofMapping | None,
    threads: Sequence[str],
    proof: ProofMapping,
    *,
    generation_locked: bool = False,
) -> None:
    """Persist durable contradictions while holding transient in-progress threads safely."""
    if window is None or not proof_targets_original_turn(original, threads, proof):
        return
    if proof.get("in_progress") is True:
        if proof.get("target_in_progress") is True and read_retained_report_failure(
            runtime_root, original, window
        ):
            contradict_report_failure(
                runtime_root, store, original, window, proof, generation_locked=generation_locked
            )
        return
    if proof.get("explicit_contradiction") is True:
        contradict_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked
        )


def verify_report_window_failure(
    controller: ReportProofController,
    store: ReportProofStore,
    original: ProofMapping,
    window: ProofMapping,
    *,
    probe_fn: Callable[..., tuple[list[str], ProofObject | None] | None] | None = None,
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
            controller, store, original, include_contradiction=True, contradiction_detail=True
        )
    if probed is None:
        return False
    threads, proof = probed
    if proof is not None:
        if proof.get("contradicted") is True or not valid_report_failure_proof(
            original, threads, proof
        ):
            handle_contradicted_report_observation(
                runtime_root, store, original, window, threads, proof, generation_locked=generation_locked
            )
            return False
        contradicted, _ = _binding_attempt_state(runtime_root, binding, window)
        if contradicted:
            return False
        _try_retain_report_failure(
            runtime_root, store, original, window, proof, generation_locked=generation_locked
        )
        return True
    contradicted, retained_record = _binding_attempt_state(runtime_root, binding, window)
    return (not contradicted) and _is_valid_retained_proof(retained_record, binding)


def resolve_report_quota_observation(
    controller: ReportProofController,
    store: ReportProofStore,
    original: ProofMapping,
    *,
    report_window: ProofMapping | None = None,
    probe_fn: Callable[..., tuple[list[str], ProofObject | None] | None] | None = None,
    fallback_observe_fn: Callable[[ProofMapping], ProofObject | None] | None = None,
) -> ProofObject | None:
    """Inspect live quota recovery for the original Turn without rewriting prior contradictions."""
    runtime_root = controller.coordination_runtime_root
    if report_window is not None and is_report_failure_contradicted(
        runtime_root, original, report_window
    ):
        return None
    if fallback_observe_fn is not None:
        proof = fallback_observe_fn(original)
    else:
        probed = (
            probe_fn(
                original, include_contradiction=True, contradiction_detail=True, swallow_probe_errors=True
            )
            if probe_fn is not None
            else probe_report_failure(
                controller,
                store,
                original,
                include_contradiction=True,
                contradiction_detail=True,
                swallow_probe_errors=True,
            )
        )
        if probed is None:
            return None
        threads, proof = probed
        if proof is None:
            return None
        if proof.get("contradicted") is True or not valid_report_failure_proof(
            original, threads, proof
        ):
            handle_contradicted_report_observation(
                runtime_root, store, original, report_window, threads, proof
            )
            return None
    if proof is not None and report_window is not None:
        _try_retain_report_failure(runtime_root, store, original, report_window, proof)
    return proof


def build_report_quota_request(
    controller: ReportProofController,
    store: ReportProofStore,
    original: ProofMapping,
    *,
    report_window: ProofMapping | None = None,
    fallback_observe_fn: Callable[[ProofMapping], ProofObject | None] | None = None,
) -> ProofObject | None:
    """Observe quota recovery, never resume or rewrite the failed native Turn."""
    request = _mapping(original.get("loopx_request"))
    if (
        request.get("report_quota_recovery") is not None
        or request.get("report_timeout_recovery") is not None
    ):
        return None
    proof = resolve_report_quota_observation(
        controller, store, original, report_window=report_window, fallback_observe_fn=fallback_observe_fn
    )
    if (
        not proof
        or proof.get("only_original_user_item") is not True
        or proof.get("codex_error_info") != "usageLimitExceeded"
        or proof.get("quota_available") is not True
    ):
        return None
    return {
        **request,
        "turn_instance_id": cast(str, request["turn_instance_id"]) + ".quota-recovery",
        "report_quota_recovery": {
            "original_turn_id": original["turn_id"],
            "native_observation": proof,
        },
    }


def build_report_timeout_request(
    original: ProofMapping,
    *,
    observe_fn: Callable[[ProofMapping], ProofObject | None],
) -> ProofObject | None:
    """Observe idle Antigravity timeout for one bounded isolated-cascade retry."""
    request = _mapping(original.get("loopx_request"))
    upstream_turn_id = original.get("upstream_turn_id")
    if (
        request.get("report_quota_recovery") is not None
        or request.get("report_timeout_recovery") is not None
        or not _is_antigravity_turn_id(upstream_turn_id)
    ):
        return None
    proof = observe_fn(original)
    if (
        not proof
        or proof.get("adapter_kind") != "antigravity_ls"
        or proof.get("cascade_idle") is not True
        or proof.get("no_final_response") is not True
        or proof.get("native_timeout") is not True
        or proof.get("quota_available") is not True
    ):
        return None
    return {
        **request,
        "turn_instance_id": cast(str, request["turn_instance_id"]) + ".timeout-recovery",
        "report_timeout_recovery": {
            "original_turn_id": original["turn_id"],
            "native_observation": proof,
        },
    }
