from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ...history import validate_goal_id_path_segment
from ...file_lock import exclusive_file_lock
from ...registry import read_json, registry_goals
from ...control_plane.projects.registry_codec import load_registry
from .context import build_change_quality_repository_context
from .policy import change_quality_goal_policy
from .result import (
    CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
    REVIEW_LENSES,
    SIMPLIFY_GUARDRAIL_LENS_IDS,
    derive_change_quality_guardrails,
    normalize_change_quality_result,
)
from .scope import build_change_quality_scope, resolve_git_root

CHANGE_QUALITY_PREPARE_SCHEMA_VERSION = "change_quality_prepare_packet_v2"
CHANGE_QUALITY_RECEIPT_SCHEMA_VERSION = "change_quality_receipt_v2"
CHANGE_QUALITY_VERIFY_SCHEMA_VERSION = "change_quality_receipt_verification_v2"
CHANGE_QUALITY_PR_EVIDENCE_SCHEMA_VERSION = "change_quality_pr_evidence_v0"

_PR_EVIDENCE_BY_STATUS = {
    "disabled": (
        "disabled",
        "Change-quality qualification is disabled for this goal.",
        None,
    ),
    "no_changes": (
        "not_required",
        "No changed files require a change-quality receipt.",
        None,
    ),
    "valid": (
        "valid",
        "The receipt matches the exact current base and diff.",
        None,
    ),
    "stale_receipt": (
        "stale",
        (
            "A prior receipt exists, but the exact base or diff changed; "
            "requalification is required."
        ),
        (
            "rerun change-quality prepare against the current base and diff, "
            "review that exact scope, and record a new passing receipt"
        ),
    ),
    "receipt_missing": (
        "missing",
        "No receipt is recorded for the exact current base and diff.",
        (
            "run change-quality prepare, review the exact current scope, "
            "and record a passing receipt"
        ),
    ),
    "invalid_receipt": (
        "invalid",
        "The receipt for the exact current base and diff is invalid.",
        (
            "repair the recorded result, review the exact current scope, "
            "and record a new passing receipt"
        ),
    ),
}


def _goal_from_registry(
    registry_path: Path,
    goal_id: str,
    *,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "") == goal_id
        ),
        None,
    )
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    if runtime_root is not None:
        from ..machine_configuration.builtins import (
            build_builtin_machine_configuration_registry,
            project_goal_with_builtin_machine_configuration,
        )
        from ..machine_configuration.store import read_machine_configuration

        machine_configuration = read_machine_configuration(
            runtime_root,
            registry=build_builtin_machine_configuration_registry(),
        )
        return project_goal_with_builtin_machine_configuration(
            goal, machine_configuration
        )
    return goal


def _receipt_root(runtime_root: Path, goal_id: str) -> Path:
    validated_goal_id = validate_goal_id_path_segment(goal_id)
    return (
        runtime_root.expanduser()
        / "goals"
        / validated_goal_id
        / "change-quality"
        / "receipts"
    )


def _repo_key(repo_path: Path) -> str:
    repo_root = resolve_git_root(repo_path)
    return hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:16]


def _receipt_path(
    *,
    runtime_root: Path,
    goal_id: str,
    repo_path: Path,
    scope_fingerprint: str,
) -> Path:
    return _receipt_root(runtime_root, goal_id) / (
        f"{_repo_key(repo_path)}-{scope_fingerprint}.json"
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_receipt(path: Path, receipt: dict[str, Any]) -> Path | None:
    # Keep original failures (including malformed bytes) before replacing the
    # current-scope read pointer. Concurrent writers archive under one lock.
    with exclusive_file_lock(path):
        previous = path.read_bytes() if path.exists() else None
        history = None
        if previous is not None:
            history = path.parent / "history" / (
                f"{path.stem}-{hashlib.sha256(previous).hexdigest()}.json"
            )
            if not history.exists():
                _atomic_write_bytes(history, previous)
        content = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _atomic_write_bytes(path, content)
    return history


def build_change_quality_prepare_packet(
    *,
    registry_path: Path,
    goal_id: str,
    repo_path: Path,
    base_ref: str = "origin/main",
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    goal = _goal_from_registry(
        registry_path, goal_id, runtime_root=runtime_root
    )
    policy = change_quality_goal_policy(goal)
    scope = build_change_quality_scope(repo_path=repo_path, base_ref=base_ref)
    repository_context = build_change_quality_repository_context(
        repo_path=repo_path,
        changed_files=list(scope["changed_files"]),
    )
    if not policy["enabled"]:
        status = "disabled"
    elif not scope["changed_files"]:
        status = "no_changes"
    else:
        status = "review_required"
    return {
        "ok": True,
        "schema_version": CHANGE_QUALITY_PREPARE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "status": status,
        "review_required": status == "review_required",
        "policy": policy,
        "scope": scope,
        "repository_context": repository_context,
        "agent_contract": {
            "provider_neutral": True,
            "max_safe_fix_passes": 1,
            "primary_result_fields": ["reuse", "simplification"],
            "sparse_result_fields": ["risks", "validation"],
            "guardrail_catalog": [
                dict(item)
                for item in REVIEW_LENSES
                if item["lens_id"] in SIMPLIFY_GUARDRAIL_LENS_IDS
            ],
            "guardrail_status_owner": "loopx_derived",
            "instructions": [
                "Review only the exact changed scope and resolve the projected repository instruction and ownership references.",
                "Write one grounded reuse conclusion and one grounded simplification conclusion.",
                "Prefer deletion, reuse, direct control flow, and established language idioms over redundant state, parameters, branches, wrappers, or speculative abstraction.",
                "Simplification must preserve decision meaning, including agent-consumed prose. Follow the repository's budget decision guide: unchanged fields or green size tests do not prove equivalence; prefer justified regression-limit increases over semantic loss.",
                "Emit a risks[] item only when a guardrail has a concrete triggered risk; do not write all-clear rows for inactive guardrails.",
                "LoopX derives guardrail status from sparse risks[] and validation[]; the Agent does not author guardrail states.",
                "Use blocker only for concrete correctness, security, privacy, contract, or required-validation failures.",
                "Use repository-native tests, linters, type checkers, and build tools as language-specific oracles.",
                *([
                    "Failed validators remain failed. Only optional validation may be nonblocking through exact-scope change_quality_baseline_attribution_v0: independent immutable base/head runs of the same command, identical full failure-signature/fixture/environment digests, causal analysis and passed required validators covering all changed paths. Required checks, frozen criteria, missing evidence and introduced or worsened failures stay blocking. Execution and causal evidence must be independently reviewed; shape checks do not prove a run occurred."
                ] if policy["enabled"] else []),
                (
                    "One bounded safe-fix pass is allowed; rerun prepare after edits and review the final scope."
                    if policy["safe_fix"]
                    else "Do not modify files; report findings only."
                ),
                "Record a result only after reviewing the final scope fingerprint.",
            ],
            "result_schema_version": CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
            "result_template": {
                "schema_version": CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
                "scope_fingerprint": scope["scope_fingerprint"],
                "reviewed_final_scope": True,
                "reuse": {
                    "outcome": "retained",
                    "summary": "",
                    "evidence_refs": [],
                },
                "simplification": {
                    "outcome": "retained",
                    "summary": "",
                    "evidence_refs": [],
                    "safe_fix_applied": False,
                },
                "risks": [],
                "validation": [
                    {
                        "validator": "",
                        "status": "passed",
                        "scope": "",
                        "required": True,
                    }
                ],
            },
        },
        "turn_boundary": {
            "role": "transport_only",
            "rule": (
                "Turn may carry this packet or a receipt reference, but canary "
                "premerge remains the enforcement authority."
            ),
        },
    }


def record_change_quality_receipt(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    repo_path: Path,
    result_path: Path,
    base_ref: str = "origin/main",
    execute: bool = False,
) -> dict[str, Any]:
    goal = _goal_from_registry(
        registry_path, goal_id, runtime_root=runtime_root
    )
    policy = change_quality_goal_policy(goal)
    if not policy["enabled"]:
        raise ValueError("change-quality qualification is disabled for this goal")
    scope = build_change_quality_scope(repo_path=repo_path, base_ref=base_ref)
    if not scope["changed_files"]:
        raise ValueError("current scope has no changes and does not need a receipt")
    repository_context = build_change_quality_repository_context(
        repo_path=repo_path,
        changed_files=list(scope["changed_files"]),
    )
    result = normalize_change_quality_result(
        json.loads(result_path.expanduser().read_text(encoding="utf-8")),
        expected_fingerprint=str(scope["scope_fingerprint"]),
        safe_fix_allowed=bool(policy["safe_fix"]),
        expected_changed_files=list(scope["changed_files"]),
        expected_instruction_refs=list(repository_context["instruction_refs"]),
        expected_scope=scope,
    )
    guardrails = derive_change_quality_guardrails(result, expected_scope=scope)
    unresolved_blockers = list(guardrails["blocking_codes"])
    decision = "fail" if unresolved_blockers else "pass"
    receipt_id = f"cqr_{str(scope['scope_fingerprint'])[:20]}"
    receipt = {
        "schema_version": CHANGE_QUALITY_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "goal_id": goal_id,
        "recorded_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "policy": policy,
        "scope": scope,
        "result": result,
        "guardrails": guardrails,
        "decision": decision,
        "unresolved_blockers": unresolved_blockers,
    }
    path = _receipt_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
        repo_path=repo_path,
        scope_fingerprint=str(scope["scope_fingerprint"]),
    )
    history = _write_receipt(path, receipt) if execute else None
    return {
        "ok": decision == "pass",
        "schema_version": CHANGE_QUALITY_RECEIPT_SCHEMA_VERSION,
        "dry_run": not execute,
        "written": execute,
        "receipt_id": receipt_id,
        "decision": decision,
        "unresolved_blockers": unresolved_blockers,
        "receipt_path": str(path),
        "scope_fingerprint": scope["scope_fingerprint"],
        "receipt": receipt,
        **({"previous_receipt_path": str(history)} if history is not None else {}),
    }


def _stored_receipt_is_valid(
    receipt: dict[str, Any] | None,
    *,
    scope_fingerprint: str,
    safe_fix_allowed: bool,
    changed_files: list[str],
    instruction_refs: list[str],
    scope: dict[str, Any],
) -> bool:
    if not (
        receipt
        and receipt.get("schema_version") == CHANGE_QUALITY_RECEIPT_SCHEMA_VERSION
        and receipt.get("decision") == "pass"
        and isinstance(receipt.get("result"), dict)
        and isinstance(receipt.get("scope"), dict)
        and receipt["scope"].get("scope_fingerprint") == scope_fingerprint
    ):
        return False
    result = receipt["result"]
    try:
        if result.get("schema_version") != CHANGE_QUALITY_RESULT_SCHEMA_VERSION:
            return False
        normalized = normalize_change_quality_result(
            result,
            expected_fingerprint=scope_fingerprint,
            safe_fix_allowed=safe_fix_allowed,
            expected_changed_files=changed_files,
            expected_instruction_refs=instruction_refs,
            expected_scope=scope,
        )
        guardrails = derive_change_quality_guardrails(normalized, expected_scope=scope)
        unresolved = list(guardrails["blocking_codes"])
        if receipt.get("guardrails") != guardrails:
            return False
    except (TypeError, ValueError):
        return False
    return not unresolved and receipt.get("unresolved_blockers") == unresolved


def _read_first_receipt(paths: list[Path]) -> dict[str, Any] | None:
    for path in paths:
        try:
            receipt = read_json(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(receipt, dict):
            return receipt
    return None


def _build_pr_evidence(
    *,
    status: str,
    policy: dict[str, Any],
    scope: dict[str, Any],
    receipt: dict[str, Any] | None = None,
    previous_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state, summary, required_action = _PR_EVIDENCE_BY_STATUS.get(
        status,
        _PR_EVIDENCE_BY_STATUS["invalid_receipt"],
    )
    previous_scope: dict[str, Any] = {}
    if isinstance(previous_receipt, dict):
        candidate_scope = previous_receipt.get("scope")
        if isinstance(candidate_scope, dict):
            previous_scope = candidate_scope
    return {
        "schema_version": CHANGE_QUALITY_PR_EVIDENCE_SCHEMA_VERSION,
        "state": state,
        "summary": summary,
        "blocking": bool(
            policy.get("strict_receipt") and state in {"stale", "missing", "invalid"}
        ),
        "requalification_required": state == "stale",
        "scope_fingerprint": scope.get("scope_fingerprint"),
        "base_ref": scope.get("base_ref"),
        "base_commit": scope.get("base_commit"),
        "head_commit": scope.get("head_commit"),
        "receipt_id": receipt.get("receipt_id") if isinstance(receipt, dict) else None,
        "previous_receipt_id": (
            previous_receipt.get("receipt_id")
            if isinstance(previous_receipt, dict)
            else None
        ),
        "previous_scope_fingerprint": previous_scope.get("scope_fingerprint"),
        "previous_base_commit": previous_scope.get("base_commit"),
        "required_action": required_action,
    }


def verify_change_quality_receipt(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    repo_path: Path,
    base_ref: str = "origin/main",
) -> dict[str, Any]:
    goal = _goal_from_registry(
        registry_path, goal_id, runtime_root=runtime_root
    )
    policy = change_quality_goal_policy(goal)
    scope = build_change_quality_scope(repo_path=repo_path, base_ref=base_ref)
    if not policy["enabled"]:
        status = "disabled"
        return {
            "ok": True,
            "schema_version": CHANGE_QUALITY_VERIFY_SCHEMA_VERSION,
            "goal_id": goal_id,
            "status": status,
            "enforcement_applied": False,
            "policy": policy,
            "scope": scope,
            "pr_evidence": _build_pr_evidence(
                status=status,
                policy=policy,
                scope=scope,
            ),
        }
    if not scope["changed_files"]:
        status = "no_changes"
        return {
            "ok": True,
            "schema_version": CHANGE_QUALITY_VERIFY_SCHEMA_VERSION,
            "goal_id": goal_id,
            "status": status,
            "enforcement_applied": bool(policy["strict_receipt"]),
            "policy": policy,
            "scope": scope,
            "pr_evidence": _build_pr_evidence(
                status=status,
                policy=policy,
                scope=scope,
            ),
        }
    path = _receipt_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
        repo_path=repo_path,
        scope_fingerprint=str(scope["scope_fingerprint"]),
    )
    receipt = None
    repository_context = build_change_quality_repository_context(
        repo_path=repo_path,
        changed_files=list(scope["changed_files"]),
    )
    if path.exists():
        receipt = _read_first_receipt([path])
    receipt_valid = _stored_receipt_is_valid(
        receipt,
        scope_fingerprint=str(scope["scope_fingerprint"]),
        safe_fix_allowed=bool(policy["safe_fix"]),
        changed_files=list(scope["changed_files"]),
        instruction_refs=list(repository_context["instruction_refs"]),
        scope=scope,
    )
    previous_receipt = None
    if receipt_valid:
        status = "valid"
    elif path.exists():
        status = "invalid_receipt"
    else:
        repo_receipts = sorted(
            path.parent.glob(f"{_repo_key(repo_path)}-*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        status = "stale_receipt" if repo_receipts else "receipt_missing"
        previous_receipt = _read_first_receipt(repo_receipts)
    strict = bool(policy["strict_receipt"])
    pr_evidence = _build_pr_evidence(
        status=status,
        policy=policy,
        scope=scope,
        receipt=receipt,
        previous_receipt=previous_receipt,
    )
    return {
        "ok": receipt_valid or not strict,
        "schema_version": CHANGE_QUALITY_VERIFY_SCHEMA_VERSION,
        "goal_id": goal_id,
        "status": status,
        "enforcement_applied": strict,
        "policy": policy,
        "scope": scope,
        "receipt_id": receipt.get("receipt_id") if receipt else None,
        "receipt_path": str(path),
        "receipt_valid": receipt_valid,
        "required_action": pr_evidence["required_action"],
        "pr_evidence": pr_evidence,
    }
