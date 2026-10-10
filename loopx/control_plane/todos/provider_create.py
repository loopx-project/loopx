"""Provider-first Todo create bridge for a promoted local authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import uuid4
import hashlib
import re

from ...agent_registry import registered_agent_ids_from_registry
from ..coordination.authority_source_capture import authority_registry_source
from ...state_refresh import now_local
from ..coordination.local_authority import (
    LOCAL_AUTHORITY_SOURCES,
    LocalCoordinationAuthorityUnavailable,
    local_authority_is_promoted,
    read_canonical_todos_if_promoted,
)
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    EffectRuntimeResponseAmbiguous,
    effect_runtime_result,
)
from .contract import (
    normalize_todo_metadata_for_write,
    normalize_todo_task_class,
    todo_done_for_status,
)
from .completion_validation_projection import (
    completion_validation_declaration,
    completion_validation_declaration_sha256,
    project_completion_validation_authority,
)
from .completion_validation_store import (
    persist_completion_validation_declaration,
    prepare_completion_validation_declaration,
    read_completion_validation_declaration,
)
from .provider_projection import settle_canonical_todo_projection


def create_canonical_todo_if_promoted(
    *, registry_path: Path, runtime_root: Path, goal_id: str, role: str,
    text: str, status: str, actor_agent_id: str | None,
    claimed_by: str | None, metadata: dict[str, Any], dry_run: bool,
    project: Path | None = None, state_file: Path | None = None,
    operation_id: str | None = None,
    expected_provider_revision: str | None = None,
) -> dict[str, Any] | None:
    # The durable fence selects the route; the native transaction owns the
    # complete-head validation. A separate source read supplies no create facts.
    if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
        return None
    if operation_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]+", operation_id):
        raise ValueError("operation_id must be a non-empty public-safe token")
    operation_id = operation_id if operation_id is not None else f"todo-create:{uuid4().hex}"
    # Identity is independent of current Todo count: exact retries still address
    # the original operation after other creates, edits, completion or archive.
    todo_id = "todo_" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    normalized_metadata = normalize_todo_metadata_for_write(metadata)
    validation_source = {
        **normalized_metadata,
        **{
            field: metadata[field]
            for field in (
                "validation_command",
                "validation_command_argv",
                "validation_label",
                "validation_timeout_seconds",
            )
            if field in metadata
        },
    }
    validation_declaration = completion_validation_declaration(validation_source)
    provider_metadata = project_completion_validation_authority(validation_source)
    # TS owns the commit timestamp; transport retries must not change intent.
    provider_metadata.pop("updated_at", None)
    todo = {
        "schema_version": "todo_domain_record_v0",
        "todo_id": todo_id,
        "role": role,
        "status": status,
        "done": todo_done_for_status(status),
        "text": text,
        "archive_state": "active",
        "task_class": normalize_todo_task_class(
            provider_metadata.get("task_class"), text=text,
            action_kind=provider_metadata.get("action_kind"),
        ),
        **provider_metadata,
        **{key: metadata[key] for key in ("priority", "title") if key in metadata and metadata.get("priority") is not None},
        **({"claimed_by": claimed_by} if claimed_by else {}),
    }
    with authority_registry_source(registry_path) as registry_source:
        registered = registered_agent_ids_from_registry(registry_path, goal_id)
    if validation_declaration is not None and not dry_run:
        prepare_completion_validation_declaration(runtime_root=runtime_root,
            goal_id=goal_id, declaration=validation_declaration)
    try:
        result = effect_runtime_result(
            "coordination.local_authority.todo_create",
            {
                "schema_version": ("loopx_local_coordination_todo_create_request_v2"
                    if expected_provider_revision is not None else "loopx_local_coordination_todo_create_request_v1"),
                **({"expected_provider_revision": expected_provider_revision}
                    if expected_provider_revision is not None else {}),
                "runtime_root": str(runtime_root.resolve()),
                "goal_id": goal_id,
                "todo": todo,
                "actor_agent_id": actor_agent_id,
                "registered_agents": registered,
                "registry_source": registry_source,
                "operation_id": operation_id,
                "dry_run": dry_run,
                "observed_at": now_local(),
            },
            timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
        )
    except EffectRuntimeResponseAmbiguous as error:
        raise LocalCoordinationAuthorityUnavailable(
            "Todo create may have committed; inspect todo receipt with this operation id, "
            "then retry the same todo add intent with --operation-id",
            code="todo_create_response_ambiguous",
            payload={"goal_id": goal_id, "operation_id": operation_id,
                     "recovery": {"operation_id": operation_id, "retry_with_same_operation_id": True}},
        ) from error
    if not isinstance(result, dict) or result.get("status") not in {
        "applied", "recovered", "replayed", "no_change", "planned",
    } or result.get("source_authority") not in LOCAL_AUTHORITY_SOURCES or (
        result.get("decision_read_from_provider") is not True
        or result.get("legacy_fallback_used") is not False
    ):
        payload = result if isinstance(result, dict) else {}
        if (
            payload.get("status") == "missing"
            and payload.get("source_authority") in LOCAL_AUTHORITY_SOURCES
            and payload.get("decision_read_from_provider") is True
            and payload.get("legacy_fallback_used") is False
        ):
            unavailable_payload = dict(payload)
            unavailable_payload["recovery"] = {
                "action": "restore_canonical_authority",
                "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                "goal_id": goal_id,
                "legacy_markdown_fallback_allowed": False,
                "retry_after": "canonical_provider_readback_loaded",
            }
            raise LocalCoordinationAuthorityUnavailable(
                "canonical Todo authority is unavailable",
                code="local_authority_todo_list_unavailable",
                payload=unavailable_payload,
            )
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo create failed; reread before retry"),
            code=str(payload.get("reason_code") or payload.get("conflict_kind")
                     or "todo_create_failed"), payload={**payload, "operation_id": operation_id},
        )
    canonical_todo_id = str(result.get("todo_id") or "")
    canonical_todo = result.get("todo")
    if validation_declaration is not None and not dry_run:
        expected_digest = completion_validation_declaration_sha256(
            validation_declaration
        )
        if (
            not canonical_todo_id
            or not isinstance(canonical_todo, dict)
            or canonical_todo.get("todo_id") != canonical_todo_id
            or canonical_todo.get("completion_validation_required") is not True
            or canonical_todo.get("completion_validation_sha256") != expected_digest
        ):
            raise LocalCoordinationAuthorityUnavailable(
                "accepted canonical Todo does not match its private validation declaration",
                code="todo_create_validation_publication_mismatch",
                payload={
                    "source_authority": result.get("source_authority"),
                    "goal_id": goal_id,
                    "todo_id": canonical_todo_id or None,
                    "provider_status": result.get("status"),
                },
            )
        # A replay describes the original create, not the current validator.
        # Never deliberately regress the compatibility alias after a revision.
        current_todo = canonical_todo
        if result.get("status") in {"recovered", "replayed"}:
            current = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
            current_todo = next((item for item in (current or {}).get("todos", [])
                                 if item.get("todo_id") == canonical_todo_id), {})
        if current_todo.get("completion_validation_sha256") == expected_digest:
            persist_completion_validation_declaration(
                runtime_root=runtime_root, goal_id=goal_id, todo_id=canonical_todo_id,
                declaration=validation_declaration,
            )
        if read_completion_validation_declaration(
            runtime_root=runtime_root,
            goal_id=goal_id,
            todo_id=canonical_todo_id,
            expected_digest=expected_digest,
        ) != validation_declaration:
            raise LocalCoordinationAuthorityUnavailable(
                "accepted Todo validation declaration failed private-store readback",
                code="todo_create_validation_publication_readback_mismatch",
                payload={
                    "source_authority": result.get("source_authority"),
                    "goal_id": goal_id,
                    "todo_id": canonical_todo_id,
                    "provider_status": result.get("status"),
                },
            )
    settled = settle_canonical_todo_projection({
        "ok": True,
        "operation_id": operation_id,
        "goal_id": goal_id,
        "role": role,
        "todo_id": canonical_todo_id or todo_id,
        "todo": text,
        "dry_run": dry_run,
        "added": result.get("changed") is True,
        "already_exists": result.get("changed") is not True,
        **result,
    }, registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        project=project, state_file=state_file)
    return cast(dict[str, Any], settled)
