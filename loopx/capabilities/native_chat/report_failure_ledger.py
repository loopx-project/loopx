from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import cast

from ..periodic_report.cadence_journal import cadence_journal_path
from ..periodic_report.field_shapes import REPORT_IDENTITY_PATTERN
from ..periodic_report.private_generation import attempt_directory
from .report_failure_probe import (
    ProofMapping,
    ProofObject,
    _is_antigravity_turn_id,
    _mapping,
    _object_dict,
)

REPORT_FAILURE_PROOF = "native-terminal-failure.json"
CONTRADICTED_REPORT_FAILURES = "contradicted-report-failures.json"
CONTRADICTED_FAILURES_SCHEMA = "periodic_report_contradicted_failures_v0"


def report_failure_binding(
    original: ProofMapping,
    window: ProofMapping,
) -> dict[str, str] | None:
    """Bind a terminal report host failure to one cadence window and native result."""
    request = _mapping(original.get("loopx_request"))
    actor = _mapping(request.get("actor"))
    result = _mapping(_mapping(original.get("response")).get("native_loopx"))
    failure = _mapping(result.get("host_failure"))
    if (
        original.get("origin") != "native_loopx"
        or original.get("loopx_execution") is not True
        or original.get("status") != "completed"
        or original.get("session_id") != actor.get("session_id")
        or request.get("work_kind") != "report"
        or request.get("report_window_id") != window.get("window_id")
        or request.get("report_subscription_revision") != window.get("subscription_revision")
        or actor.get("goal_id") != window.get("goal_id")
        or actor.get("agent_id") != window.get("agent_id")
        or result.get("status") != "failed"
        or result.get("result_kind") != "host_failure"
        or failure.get("retryable") is not False
    ):
        return None
    identity: dict[str, str] = {}
    for key, value in (
        ("session_id", original.get("session_id")),
        ("chat_turn_id", original.get("turn_id")),
        ("provider_turn_id", original.get("upstream_turn_id")),
        ("turn_instance_id", request.get("turn_instance_id")),
        ("window_id", window.get("window_id")),
        ("subscription_revision", window.get("subscription_revision")),
    ):
        if not isinstance(value, str) or not value:
            return None
        identity[key] = value
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    identity["native_result_sha256"] = hashlib.sha256(encoded).hexdigest()
    return identity


def _read_report_failure_record(
    runtime_root: Path,
    window: ProofMapping,
) -> ProofObject | None:
    path = attempt_directory(runtime_root, _object_dict(window)) / REPORT_FAILURE_PROOF
    try:
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        proof = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(ProofObject, proof) if isinstance(proof, dict) else None


def _is_valid_retained_proof(
    proof: ProofMapping | None,
    binding: Mapping[str, str],
) -> bool:
    if _is_antigravity_turn_id(binding.get("provider_turn_id")):
        return (
            isinstance(proof, dict)
            and set(proof)
            == {
                "binding",
                "provider_thread_id",
                "adapter_kind",
                "cascade_idle",
                "no_final_response",
            }
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


def _is_contradicted_proof(
    proof: object,
    binding: Mapping[str, str],
) -> bool:
    if not isinstance(proof, dict):
        return False
    thread_id = proof.get("provider_thread_id")
    return (
        set(proof)
        == {"binding", "provider_thread_id", "only_original_user_item", "contradicted"}
        and proof["binding"] == binding
        and proof["only_original_user_item"] is False
        and proof["contradicted"] is True
        and (thread_id is None or (isinstance(thread_id, str) and bool(thread_id)))
    )


def _is_exact_contradiction(
    proof: object,
    binding: Mapping[str, str],
    thread_id: str | None,
) -> bool:
    return (
        isinstance(proof, Mapping)
        and _is_contradicted_proof(proof, binding)
        and proof.get("provider_thread_id") == thread_id
    )


def _contradiction_ledger_path(runtime_root: Path, window: ProofMapping) -> Path | None:
    goal_id = window.get("goal_id")
    if not isinstance(goal_id, str) or not REPORT_IDENTITY_PATTERN.fullmatch(goal_id):
        return None
    return (
        cadence_journal_path(runtime_root, goal_id).parent
        / CONTRADICTED_REPORT_FAILURES
    )


def _binding_key(binding: Mapping[str, str]) -> str:
    encoded = json.dumps(
        dict(binding), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_contradiction_ledger(ledger_path: Path) -> tuple[str, ProofObject]:
    try:
        if (
            ledger_path.parent.parent.is_symlink()
            or ledger_path.parent.is_symlink()
            or ledger_path.is_symlink()
        ):
            return "untrusted", {}
        if not ledger_path.exists():
            return "absent", {}
        if not ledger_path.is_file():
            return "untrusted", {}
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "untrusted", {}
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "entries"}
        or data.get("schema_version") != CONTRADICTED_FAILURES_SCHEMA
        or not isinstance(data.get("entries"), dict)
    ):
        return "untrusted", {}
    return "ok", cast(ProofObject, data["entries"])


def _durable_contradiction_status(
    runtime_root: Path,
    binding: Mapping[str, str],
    window: ProofMapping,
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


def _binding_attempt_state(
    runtime_root: Path,
    binding: Mapping[str, str],
    window: ProofMapping,
) -> tuple[bool, ProofObject | None]:
    """Read attempt record before durable contradiction ledger so concurrent commits cannot lose."""
    record = _read_report_failure_record(runtime_root, window)
    if _is_contradicted_proof(record, binding):
        return True, record
    if _durable_contradiction_status(runtime_root, binding, window) != "absent":
        return True, None
    return False, record


def read_retained_report_failure(
    runtime_root: Path,
    original: ProofMapping,
    window: ProofMapping,
) -> bool:
    """Scope retained provider evidence to the same failure and cadence window."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    contradicted, record = _binding_attempt_state(runtime_root, binding, window)
    return (not contradicted) and _is_valid_retained_proof(record, binding)


def is_report_failure_contradicted(
    runtime_root: Path,
    original: ProofMapping,
    window: ProofMapping,
) -> bool:
    """Return True when live provider history has already contradicted this Turn's proof."""
    binding = report_failure_binding(original, window)
    if binding is None:
        return False
    contradicted, _ = _binding_attempt_state(runtime_root, binding, window)
    return contradicted
