"""Todo writeback adapters that preserve the Turn's effective runtime root."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from hashlib import sha256

from ..control_plane.todos.mutation_api import complete_goal_todo
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
)


def captured_todo_text_sha256(
    envelope: Mapping[str, Any], *, todo_id: str
) -> str | None:
    """Resolve admitted full-source references; hash bytes without rereading live state.

    Older journal envelopes have no verification marker and retain their
    original completion request. New producers mark source validation before
    body deduplication, so a display preview alone can never become the source.
    """
    action = envelope.get("action") or {}
    selected = action.get("selected_todo") or {}
    if "text_source_verified" not in selected:
        return None
    if (
        selected["text_source_verified"] is not True
        or selected.get("todo_id") != todo_id
    ):
        raise ValueError("Turn task source does not match its selected Todo")
    context = envelope.get("work_context") or {}
    if not isinstance(context, Mapping) or not isinstance(
        context.get("sources", []), list
    ):
        raise ValueError("Turn completion requires its admitted full task source")
    records = []
    for source in context.get("sources", []):
        if not isinstance(source, Mapping) or source.get("source") != "selected_todo":
            continue
        content = source.get("content")
        record = content.get("todo") if isinstance(content, Mapping) else None
        if (
            isinstance(record, Mapping)
            and record.get("todo_id") == todo_id
            and isinstance(record.get("text"), str)
        ):
            records.append(record["text"])
    if len(records) > 1:
        raise ValueError("Turn task source is ambiguous")
    text = records[0] if records else None
    if text is None and (
        not context or context.get("selected_todo_ref") == "selected_todo"
    ):
        text = selected.get("text")
        if text is None and selected.get("text_ref") == "action.recommended_action":
            text = action.get("recommended_action")
    if not isinstance(text, str) or not text:
        raise ValueError("Turn completion requires its admitted full task source")
    return sha256(text.encode("utf-8")).hexdigest()


def write_turn_validated_completion(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    completion_turn_key: str,
    evidence: str,
    note: str,
    agent_id: str | None,
    expected_todo_text_sha256: str | None = None,
    completion_delivery_workspace: Mapping[str, Any] | None = None,
    completion_validation_workspace_path: Path | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> dict[str, Any]:
    """Complete one validated Todo under the effective runtime root."""

    try:
        return complete_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            role="agent",
            completion_turn_key=completion_turn_key,
            completion_identity_source="turn_settlement",
            expected_todo_text_sha256=expected_todo_text_sha256,
            require_captured_todo_text=True,
            completion_delivery_workspace=completion_delivery_workspace,
            completion_validation_workspace_path=completion_validation_workspace_path,
            task_lease_idempotency_key=task_lease_idempotency_key,
            task_lease_expected_version=task_lease_expected_version,
            evidence=evidence,
            note=note,
            agent_id=agent_id,
            project=None,
            dry_run=False,
            runtime_root_arg=runtime_root_arg,
        )
    except LocalCoordinationAuthorityRejection as exc:
        # A typed owner rejection proves that this attempt did not write. Lost
        # responses and unavailable authority still raise for receipt recovery.
        diagnostics = dict(exc.payload)
        diagnostics.setdefault("reason_code", exc.code)
        diagnostics.setdefault("reason", str(exc))
        return {
            **diagnostics,
            "ok": False,
            "appended": False,
            "completed": False,
        }
