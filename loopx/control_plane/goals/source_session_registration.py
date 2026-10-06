from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...file_lock import exclusive_cross_runtime_file_lock
from ..projects.registration_state import (
    registration_state_matches,
    render_registration_state,
)
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    source_session_registry_transaction,
)
from ..runtime.time import now_local_iso
from ..runtime.document_io import atomic_write_state_text
from .source_session_registry_state import (
    GOAL_INSTANCE_ID,
    alias_digest,
    canonical_digest,
    guard_path,
    lifetime_root,
    require_goal_id,
    write_journal,
)


_JOURNAL_SCHEMA = "loopx_source_session_lifetime_journal_v1"
_CREATION_RECEIPT_SCHEMA = "loopx_goal_creation_receipt_v1"


@dataclass(frozen=True, slots=True)
class FreshSourceSessionRegistration:
    registry_path: Path
    runtime_root: Path
    operation_id: str
    project_id: str
    goal_id: str
    objective: str
    non_goals: list[str]
    acceptance: list[str]
    unknowns: list[str]
    next_effect: str
    stop_condition: str
    project_record: dict[str, Any]
    goal_record: dict[str, Any]
    state_file: Path


def _registration_digest(request: FreshSourceSessionRegistration) -> str:
    return canonical_digest(
        {
            "schema_version": "loopx_source_session_registration_request_v1",
            "operation_id": request.operation_id,
            "project_id": request.project_id,
            "goal_id": request.goal_id,
            "objective": request.objective,
            "non_goals": request.non_goals,
            "acceptance": request.acceptance,
            "unknowns": request.unknowns,
            "next_effect": request.next_effect,
            "stop_condition": request.stop_condition,
            "project": request.project_record,
            "goal": request.goal_record,
            "state_file": str(request.state_file),
            "runtime_root": str(request.runtime_root),
        }
    )


def _journal_path(
    registry_path: Path,
    *,
    goal_id: str,
    operation_id: str,
) -> Path:
    operation_digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    return (
        lifetime_root(registry_path)
        / "journals"
        / alias_digest(goal_id)
        / f"{operation_digest}.json"
    )


def _registration_journals(
    registry_path: Path,
    *,
    goal_id: str,
) -> list[Path]:
    directory = lifetime_root(registry_path) / "journals" / alias_digest(goal_id)
    return sorted(directory.glob("*.json"))


def _read_journal(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("source-session lifetime journal must be a JSON object")
    required = {
        "schema_version",
        "operation_id",
        "request_digest",
        "goal_ref",
        "created_at",
        "phase",
    }
    if set(value) != required:
        raise ValueError("source-session lifetime journal shape is invalid")
    goal_ref = value.get("goal_ref")
    if (
        value.get("schema_version") != _JOURNAL_SCHEMA
        or not isinstance(value.get("operation_id"), str)
        or not isinstance(value.get("request_digest"), str)
        or not isinstance(value.get("created_at"), str)
        or value.get("phase") not in {"reserved", "published"}
        or not isinstance(goal_ref, dict)
        or not isinstance(goal_ref.get("goal_id"), str)
        or not isinstance(goal_ref.get("goal_instance_id"), str)
        or not GOAL_INSTANCE_ID.fullmatch(goal_ref["goal_instance_id"])
    ):
        raise ValueError("source-session lifetime journal content is invalid")
    return value


def _new_registration_journal(
    request: FreshSourceSessionRegistration,
    *,
    request_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": _JOURNAL_SCHEMA,
        "operation_id": request.operation_id,
        "request_digest": request_digest,
        "goal_ref": {
            "goal_id": request.goal_id,
            "goal_instance_id": f"ginst_{uuid4().hex}",
        },
        "created_at": now_local_iso(),
        "phase": "reserved",
    }


def _require_matching_journal(
    journal: dict[str, Any],
    request: FreshSourceSessionRegistration,
    *,
    request_digest: str,
) -> None:
    if journal["operation_id"] != request.operation_id:
        raise ValueError("source-session journal operation_id mismatch")
    if journal["request_digest"] != request_digest:
        raise ValueError("source-session operation_id was reused with different input")
    if journal["goal_ref"]["goal_id"] != request.goal_id:
        raise ValueError("source-session journal goal_id mismatch")


def _state_text(
    request: FreshSourceSessionRegistration,
    *,
    updated_at: str,
) -> str:
    return render_registration_state(
        project_id=request.project_id,
        goal_id=request.goal_id,
        objective=request.objective,
        non_goals=request.non_goals,
        acceptance=request.acceptance,
        unknowns=request.unknowns,
        next_effect=request.next_effect,
        stop_condition=request.stop_condition,
        updated_at=updated_at,
    )


def _ensure_registration_state(
    request: FreshSourceSessionRegistration,
    *,
    updated_at: str,
) -> bool:
    expected = _state_text(request, updated_at=updated_at)
    with exclusive_cross_runtime_file_lock(
        request.state_file,
        operation="source_session_registration_state",
    ):
        if request.state_file.exists():
            existing = request.state_file.read_text(encoding="utf-8")
            if not registration_state_matches(
                existing,
                expected,
                objective=request.objective,
            ):
                raise ValueError(
                    f"goal state file conflicts with registration: {request.state_file}"
                )
            return False
        atomic_write_state_text(request.state_file, expected, create_only=True)
        return True


def _creation_receipt(
    request: FreshSourceSessionRegistration,
    journal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": _CREATION_RECEIPT_SCHEMA,
        "operation_id": request.operation_id,
        "request_digest": journal["request_digest"],
        "goal_ref": copy.deepcopy(journal["goal_ref"]),
        "created_at": journal["created_at"],
    }


def _registration_result(
    request: FreshSourceSessionRegistration,
    *,
    receipt: dict[str, Any],
    changed: bool,
    replayed: bool,
) -> dict[str, Any]:
    goal_ref = copy.deepcopy(receipt["goal_ref"])
    goal = {
        **copy.deepcopy(request.goal_record),
        "goal_instance_id": goal_ref["goal_instance_id"],
        "execution_authority": False,
    }
    return {
        "ok": True,
        "schema_version": "loopx_project_registration_v1",
        "changed": changed,
        "replayed": replayed,
        "registry": str(request.registry_path),
        "project": copy.deepcopy(request.project_record),
        "goal": goal,
        "goal_ref": goal_ref,
        "request_digest": receipt["request_digest"],
        "receipt": copy.deepcopy(receipt),
        "state_file": str(request.state_file),
        "execution_authority": False,
    }


def _matching_creation_receipt(
    registry: dict[str, Any],
    request: FreshSourceSessionRegistration,
    *,
    request_digest: str,
) -> dict[str, Any] | None:
    receipts = registry.get("lifetime_receipts")
    if not isinstance(receipts, list):
        raise ValueError("source-session lifetime_receipts must be a list")
    matches = [
        receipt
        for receipt in receipts
        if isinstance(receipt, dict)
        and receipt.get("operation_id") == request.operation_id
    ]
    if len(matches) > 1:
        raise ValueError("source-session operation has duplicate lifetime receipts")
    if not matches:
        return None
    receipt = matches[0]
    goal_ref = receipt.get("goal_ref")
    if (
        receipt.get("schema_version") != _CREATION_RECEIPT_SCHEMA
        or receipt.get("request_digest") != request_digest
        or not isinstance(receipt.get("created_at"), str)
        or not isinstance(goal_ref, dict)
        or goal_ref.get("goal_id") != request.goal_id
        or not isinstance(goal_ref.get("goal_instance_id"), str)
        or not GOAL_INSTANCE_ID.fullmatch(goal_ref["goal_instance_id"])
    ):
        raise ValueError("source-session operation_id conflicts with its receipt")
    return receipt


def register_fresh_source_session_project(
    request: FreshSourceSessionRegistration,
) -> dict[str, Any]:
    """Create or replay the one fresh-project source-session publication."""

    require_goal_id(request.goal_id)
    request_digest = _registration_digest(request)
    guard = guard_path(request.registry_path, request.goal_id)
    journal_path = _journal_path(
        request.registry_path,
        goal_id=request.goal_id,
        operation_id=request.operation_id,
    )
    with exclusive_cross_runtime_file_lock(
        guard,
        operation="source_session_goal_lifetime",
    ):
        journal = _read_journal(journal_path)
        if journal is not None:
            _require_matching_journal(
                journal,
                request,
                request_digest=request_digest,
            )
        elif not request.registry_path.exists():
            if _registration_journals(request.registry_path, goal_id=request.goal_id):
                raise ValueError(
                    "source-session registration has another reservation journal"
                )
            if request.state_file.exists():
                raise ValueError(
                    "source-session Goal state has no matching reservation journal"
                )
            journal = _new_registration_journal(
                request,
                request_digest=request_digest,
            )
            write_journal(journal_path, journal)

        with source_session_registry_transaction(
            request.registry_path,
            operation="source_session_project_register",
            create=lambda: {
                "schema_version": "0.2",
                "registry_role": "project-local",
                "common_runtime_root": str(request.runtime_root),
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "projects": [],
                "goals": [],
                "session_bindings": [],
                "session_receipts": [],
                "lifetime_receipts": [],
                "retired_goal_instances": [],
            },
        ) as transaction:
            registry = transaction.payload_copy()
            existing_receipt = _matching_creation_receipt(
                registry,
                request,
                request_digest=request_digest,
            )
            if existing_receipt is not None:
                goal_ref = existing_receipt["goal_ref"]
                if journal is None:
                    journal = {
                        "schema_version": _JOURNAL_SCHEMA,
                        "operation_id": request.operation_id,
                        "request_digest": request_digest,
                        "goal_ref": copy.deepcopy(goal_ref),
                        "created_at": existing_receipt["created_at"],
                        "phase": "published",
                    }
                    write_journal(journal_path, journal)
                elif journal["phase"] != "published":
                    journal = {**journal, "phase": "published"}
                    write_journal(journal_path, journal)
                return _registration_result(
                    request,
                    receipt=existing_receipt,
                    changed=False,
                    replayed=True,
                )

            if request.registry_path.exists():
                raise ValueError(
                    "source_session_v1 registration requires an absent registry"
                )
            if journal is None:
                raise RuntimeError("source-session registration reservation is missing")
            if journal["phase"] == "published":
                raise ValueError(
                    "published source-session registry is missing; refusing recreation"
                )

            goal_record = {
                **copy.deepcopy(request.goal_record),
                "goal_instance_id": journal["goal_ref"]["goal_instance_id"],
                "execution_authority": False,
            }
            receipt = _creation_receipt(request, journal)
            _ensure_registration_state(
                request,
                updated_at=journal["created_at"],
            )
            registry["projects"] = [copy.deepcopy(request.project_record)]
            registry["goals"] = [goal_record]
            registry["lifetime_receipts"] = [receipt]
            registry["updated_at"] = journal["created_at"]
            transaction.commit(registry)
            journal = {**journal, "phase": "published"}
            write_journal(journal_path, journal)

    return _registration_result(
        request,
        receipt=receipt,
        changed=True,
        replayed=False,
    )
