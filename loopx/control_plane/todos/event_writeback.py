from __future__ import annotations

from contextlib import nullcontext
from ..coordination.legacy_writer_fence import legacy_todo_write_transaction, require_legacy_coordination_write_allowed
from ..coordination.shadow_management import require_shadow_primary_write_allowed
from ..coordination.local_authority_shadow_adapter import effective_runtime_root

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ...event_sourced_state import (
    AppendOnlyStateEventStore,
    StateEventError,
    StateEventSourceChangedError,
    TODO_ADDED,
    TODO_CLAIMED,
    TODO_COMPLETED,
    build_state_projection,
    make_state_event,
)
from ...history import load_registry
from ..goals.active_state_event_projection import (
    active_state_event_projection_fields,
    state_event_log_candidates,
)
from ..goals.path_resolution import resolve_goal_local_path
from ..runtime.validation_command import CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION
from .active_state_todo_parser import parse_active_state_todos
from .contract import (
    TODO_STATUS_DONE,
    build_todo_id,
    merge_todo_id_lists,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_id_list,
)
from .completion_transaction import reduce_todo_completion_transaction
from .successor_derivation import (
    build_successor_intents,
    derive_successor_proposals,
)
from .todo_semantics import todo_priority_parts


TODO_SECTION_HEADINGS = {
    "user": "User Todo / Owner Review Reading Queue",
    "agent": "Agent Todo",
}


def _registry_goal(registry_path: Path, goal_id: str) -> dict[str, Any] | None:
    registry = load_registry(registry_path)
    for goal in registry.get("goals") or []:
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            return goal
    return None


def _raw_event_projection_todo_item(
    *,
    projection: Mapping[str, Any],
    todo_id: str,
    roles: list[str],
) -> dict[str, Any] | None:
    for item_role in roles:
        if item_role not in TODO_SECTION_HEADINGS:
            continue
        summary = projection.get(f"{item_role}_todos")
        items = summary.get("items") if isinstance(summary, dict) else []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if normalize_todo_id(item.get("todo_id")) == todo_id:
                return dict(item)
    return None


def _canonical_event_projection_source(
    *,
    goal: dict[str, Any],
    state_path: Path,
    projection_authority: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    """Resolve the exact log whose fingerprint produced the public projection."""
    authority_keys = (
        "source_event_count",
        "source_checksum",
        "last_event_id",
        "last_append_sequence",
        "projection_version",
    )
    goal_id = str(goal.get("id") or "").strip()
    for event_log_path in state_event_log_candidates(
        goal,
        state_path=state_path,
        resolve_goal_local_path=resolve_goal_local_path,
    ):
        if not event_log_path.exists():
            continue
        try:
            events = AppendOnlyStateEventStore(event_log_path).load()
            if not events:
                continue
            projection = build_state_projection(events, goal_id=goal_id or None)
        except (OSError, StateEventError):
            continue
        if all(
            projection.get(key) == projection_authority.get(key)
            for key in authority_keys
        ):
            return event_log_path, projection
    return None


def event_projection_source_authority(context: Mapping[str, Any]) -> dict[str, Any]:
    fields = context.get("fields") if isinstance(context.get("fields"), dict) else {}
    projection = (
        fields.get("state_event_projection")
        if isinstance(fields, dict)
        else None
    )
    projection = projection if isinstance(projection, dict) else {}
    return {
        "projection_source": "event_log",
        "event_log_path": str(context.get("event_log_path") or ""),
        "source_checksum": projection.get("source_checksum"),
        "last_event_id": projection.get("last_event_id"),
        "last_append_sequence": projection.get("last_append_sequence"),
    }


def event_projection_source_matches(
    context: Mapping[str, Any],
    source_authority: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(source_authority, Mapping):
        return True
    if source_authority.get("projection_source") != "event_log":
        return True
    current = event_projection_source_authority(context)
    return all(
        current.get(key) == source_authority.get(key)
        for key in (
            "event_log_path",
            "source_checksum",
            "last_event_id",
            "last_append_sequence",
        )
    )


def _completion_validation_source_drift_failure(
    *,
    goal_id: str,
    todo_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "ok": False,
        "dry_run": dry_run,
        "completed": False,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "changed": False,
        "validation": {
            "schema_version": CALLER_VALIDATION_RECEIPT_SCHEMA_VERSION,
            "command_label": "todo completion validation",
            "exit_code": None,
            "passed": False,
            "status": "source_drift",
            "summary": (
                "event-log validation source changed before completion append; "
                "retry the completion against the current canonical source"
            ),
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        },
        "validation_blocked_completion": True,
    }


def event_projection_todo_context(
    *,
    registry_path: Path,
    goal_id: str,
    state_path: Path,
    todo_id: str,
    role: str | None,
) -> dict[str, Any] | None:
    goal = _registry_goal(registry_path, goal_id)
    if not goal:
        return None
    fields = active_state_event_projection_fields(
        goal,
        state_path=state_path,
        resolve_goal_local_path=resolve_goal_local_path,
        parse_active_state_todos=parse_active_state_todos,
        item_limit=None,
    )
    if not fields.get("state_event_projection"):
        return None
    normalized_todo_id = normalize_todo_id(todo_id)
    if not normalized_todo_id:
        return None
    roles = [role] if role else ["user", "agent"]
    matched_role: str | None = None
    matched_item: dict[str, Any] | None = None
    for item_role in roles:
        if item_role not in TODO_SECTION_HEADINGS:
            continue
        summary = fields.get(f"{item_role}_todos")
        items = summary.get("items") if isinstance(summary, dict) else []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if normalize_todo_id(item.get("todo_id")) == normalized_todo_id:
                matched_role = item_role
                matched_item = dict(item)
                break
        if matched_item:
            break
    if not matched_item or matched_role is None:
        return None
    projection_authority = fields.get("state_event_projection")
    source = _canonical_event_projection_source(
        goal=goal,
        state_path=state_path,
        projection_authority=(
            projection_authority
            if isinstance(projection_authority, Mapping)
            else {}
        ),
    )
    if source is None:
        return None
    event_log_path, raw_projection = source
    raw_item = _raw_event_projection_todo_item(
        projection=raw_projection,
        todo_id=normalized_todo_id,
        roles=roles,
    )
    return {
        "goal": goal,
        "fields": fields,
        "event_log_path": event_log_path,
        "registry_path": registry_path,
        "state_path": state_path,
        "role": matched_role,
        "item": matched_item,
        "raw_item": raw_item or matched_item,
    }


def _todo_write_event_id(
    *,
    goal_id: str,
    todo_id: str,
    action: str,
    updated_at: str,
    text: str | None = None,
) -> str:
    digest = hashlib.sha1(
        "|".join([goal_id, todo_id, action, updated_at, str(text or "")]).encode("utf-8")
    ).hexdigest()[:16]
    return f"todo-write-{action}-{digest}"


def _encode_event_projected_successor(
    *,
    goal_id: str,
    proposal: Mapping[str, Any],
    updated_at: str,
    fields: dict[str, Any],
    actor_agent_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Encode a TS-derived successor without deciding defaults or writing it."""
    role = str(proposal["role"])
    section = TODO_SECTION_HEADINGS[role]
    summary = fields.get(f"{role}_todos")
    items = summary.get("items") if isinstance(summary, dict) else []
    index = len(items if isinstance(items, list) else []) + 1
    text = str(proposal["text"])
    todo_id = build_todo_id(role=role, source_section=section, index=index, text=text)
    priority, title = todo_priority_parts(text)
    metadata_fields = (
        "task_class",
        "action_kind",
        "capability_binding_ref",
        "task_repository",
        "required_capabilities",
        "continuation_policy",
        "bound_agent",
        "goal_bound",
        "blocks_agent",
        "excluded_agents",
        "unblocks_todo_id",
    )
    metadata = {
        key: proposal[key] for key in metadata_fields
        if key in proposal and proposal[key] not in (None, "", [])
    }
    payload = {
        "role": role,
        "priority": priority or "P2",
        "title": title,
        "planner_order": index,
        "updated_at": updated_at,
        **metadata,
    }
    events = [
        make_state_event(
            event_id=_todo_write_event_id(
                goal_id=goal_id,
                todo_id=todo_id,
                action="add",
                updated_at=updated_at,
                text=text,
            ),
            goal_id=goal_id,
            event_type=TODO_ADDED,
            refs={"todo_id": todo_id},
            payload=payload,
            recorded_at=updated_at,
            producer="loopx.todo.complete",
            actor_agent_id=actor_agent_id,
        )
    ]
    claimed_by = proposal.get("claimed_by")
    if claimed_by:
        events.append(
            make_state_event(
                event_id=_todo_write_event_id(
                    goal_id=goal_id,
                    todo_id=todo_id,
                    action="claim",
                    updated_at=updated_at,
                    text=claimed_by,
                ),
                goal_id=goal_id,
                event_type=TODO_CLAIMED,
                refs={"todo_id": todo_id},
                payload={"claimed_by": claimed_by},
                recorded_at=updated_at,
                producer="loopx.todo.complete",
                actor_agent_id=actor_agent_id,
            )
        )
    return {
        "added": True,
        "already_exists": False,
        "metadata_updated": False,
        "role": role,
        "section": section,
        "todo": text,
        "todo_id": todo_id,
        **{key: proposal.get(key) for key in metadata_fields},
        "required_capabilities": proposal.get("required_capabilities", []),
        "excluded_agents": proposal.get("excluded_agents", []),
        "claimed_by": claimed_by,
        "updated_at": updated_at,
        "source": "event_log",
    }, events


def complete_event_projected_goal_todo(
    *,
    goal_id: str,
    context: dict[str, Any],
    evidence: str | None,
    note: str | None,
    no_followup: bool,
    successor_todo_ids: list[str] | None,
    claimed_by: str | None,
    clear_claim: bool,
    next_agent_todo: str | None,
    next_user_todo: str | None,
    next_user_task_class: str,
    next_claimed_by: str | None,
    next_task_class: str | None,
    next_action_kind: str | None,
    next_task_repository: str | None,
    next_required_capabilities: list[str] | None,
    next_continuation_policy: str | None,
    self_merged: bool,
    next_excluded_agents: list[str],
    registered_agents: list[str],
    updated_at: str,
    dry_run: bool,
    completion_turn_key: str | None = None,
    completion_identity_source: str | None = None,
    actor_agent_id: str | None = None,
    completion_fence: dict[str, Any] | None = None,
    completion_state: Mapping[str, Any] | None = None,
    completion_validation_source_authority: dict[str, Any] | None = None,
    runtime_root: Path | None = None,
    primary_lock_held: bool = False,
) -> dict[str, Any]:
    registry_path = Path(context["registry_path"])
    state_path = Path(context["state_path"])
    root = runtime_root or effective_runtime_root(registry_path, None)
    transaction = (
        nullcontext()
        if primary_lock_held
        else legacy_todo_write_transaction(
            registry_path,
            goal_id,
            state_path,
            actor_agent_id,
            "todo_event_complete",
            dry_run,
            runtime_root=root,
        )
    )
    with transaction:
        if not dry_run:
            require_shadow_primary_write_allowed(root, goal_id)
            require_legacy_coordination_write_allowed(
                runtime_root=root, goal_id=goal_id
            )
        item = dict(context["item"])
        role = str(context["role"])
        todo_id = normalize_todo_id(item.get("todo_id"))
        if not todo_id:
            raise ValueError("event-projected todo has no stable todo_id")
        if not event_projection_source_matches(
            context,
            completion_validation_source_authority,
        ):
            return _completion_validation_source_drift_failure(
                goal_id=goal_id,
                todo_id=todo_id,
                dry_run=dry_run,
            )
        if clear_claim and item.get("claimed_by"):
            item.pop("claimed_by", None)
        effective_claimed_by = claimed_by or normalize_todo_claimed_by(
            item.get("claimed_by")
        )
        store = AppendOnlyStateEventStore(Path(context["event_log_path"]))
        source_checksum = context["fields"]["state_event_projection"]["source_checksum"]
        if completion_fence is None or completion_state is None:
            transaction = reduce_todo_completion_transaction(
                todo=item,
                projection_source="event_log",
                completion_turn_key=completion_turn_key,
                no_followup=no_followup,
                goal_id=goal_id,
                todo_id=todo_id,
                completion_identity_source=completion_identity_source,
                requested_has_successor=bool(
                    normalize_todo_id_list(successor_todo_ids)
                    or normalize_todo_id_list(item.get("successor_todo_ids"))
                    or next_agent_todo
                    or next_user_todo
                ),
                dry_run=dry_run,
            )
            if transaction["decision"] in {"execute_validation", "reject"}:
                raise RuntimeError(
                    "event-projected Todo completion validation must run through "
                    "the completion gate"
                )
            completion_fence = dict(transaction["fence"])
            candidate_state = transaction.get("completion_state")
            completion_state = (
                dict(candidate_state) if isinstance(candidate_state, Mapping) else None
            )
        already_done = bool(completion_fence["terminal_before_request"])
        terminal_upgrade = completion_fence["reason"] in {
            "same_turn_terminal_upgrade",
            "lifecycle_reentry_terminal_upgrade",
        }
        untyped_completion_repair = (
            completion_fence["reason"] == "untyped_completion_repair"
        )
        unscoped_identity_repair = (
            completion_fence["reason"] == "unscoped_completion_identity_repair"
        )
        if completion_fence["outcome"] == "replay":
            if not dry_run:
                try:
                    store.append_many([], expected_checksum=source_checksum)
                except StateEventSourceChangedError:
                    return _completion_validation_source_drift_failure(
                        goal_id=goal_id, todo_id=todo_id, dry_run=dry_run
                    )
            return {
                "ok": True,
                "dry_run": dry_run,
                "completed": True,
                "idempotent_replay": True,
                "changed": False,
                "goal_id": goal_id,
                "role": role,
                "section": TODO_SECTION_HEADINGS[role],
                "todo": item.get("text") or item.get("title"),
                "todo_id": todo_id,
                "status": TODO_STATUS_DONE,
                "completion_continuation": completion_fence.get(
                    "completion_continuation"
                ),
                "completion_recovery": item.get("completion_recovery"),
                "status_changed": False,
                "next_todos": [],
                "state_file": str(context.get("state_file") or ""),
                "project": str(context.get("project") or "") or None,
                "updated_at": item.get("updated_at"),
                "source": "event_log",
            }
        successor_intents = build_successor_intents(
            next_agent_todo=next_agent_todo,
            next_user_todo=next_user_todo,
            next_user_task_class=next_user_task_class,
            next_claimed_by=next_claimed_by,
            next_task_class=next_task_class,
            next_action_kind=next_action_kind,
            next_task_repository=next_task_repository,
            next_required_capabilities=next_required_capabilities,
            next_continuation_policy=next_continuation_policy,
            next_excluded_agents=next_excluded_agents,
        )
        successor_proposals = derive_successor_proposals(
            command="complete",
            predecessor=item,
            registered_agents=registered_agents,
            actor_agent_id=actor_agent_id,
            completion_policy={
                "effective_claimed_by": effective_claimed_by,
                "effective_next_claimed_by": next_claimed_by,
                "effective_next_excluded_agents": next_excluded_agents,
            },
            successor_intents=successor_intents,
        )
        encoded_successors = [
            _encode_event_projected_successor(
                goal_id=goal_id,
                proposal=proposal,
                updated_at=updated_at,
                fields=context["fields"],
                actor_agent_id=actor_agent_id,
            )
            for proposal in successor_proposals
        ]
        next_results = [result for result, _ in encoded_successors]
        pending_events = [event for _, events in encoded_successors for event in events]

        normalized_successor_todo_ids = merge_todo_id_lists(
            successor_todo_ids,
            [item.get("todo_id") for item in next_results],
            normalize_todo_id_list(item.get("successor_todo_ids")),
        )
        if not isinstance(completion_state, Mapping):
            raise RuntimeError(
                "event-projected Todo completion requires the TypeScript "
                "transaction state"
            )
        completion_continuation = completion_state.get("continuation")
        completion_recovery = completion_state.get("recovery")
        if completion_continuation not in {
            "active_goal",
            "successor",
            "no_followup",
        } or completion_recovery not in {
            None,
            "same_turn_terminal_closeout",
            "lifecycle_reentry_terminal_closeout",
        }:
            raise RuntimeError(
                "TypeScript Todo completion transaction state shape mismatch"
            )
        completion_payload: dict[str, Any] = {"updated_at": updated_at}
        completion_payload["completion_continuation"] = completion_continuation
        if completion_recovery:
            completion_payload["completion_recovery"] = completion_recovery
        if not already_done:
            completion_payload["completed_at"] = updated_at
        if evidence:
            completion_payload["evidence"] = evidence
        if completion_turn_key:
            completion_payload["completion_turn_key"] = completion_turn_key
        if note:
            completion_payload["note"] = note
        if no_followup:
            completion_payload["no_followup"] = "true"
        if normalized_successor_todo_ids:
            completion_payload["successor_todo_ids"] = normalized_successor_todo_ids
        completion_event = make_state_event(
            event_id=_todo_write_event_id(
                goal_id=goal_id,
                todo_id=todo_id,
                action="complete",
                updated_at=updated_at,
                text=evidence or note,
            ),
            goal_id=goal_id,
            event_type=TODO_COMPLETED,
            refs={"todo_id": todo_id},
            payload=completion_payload,
            recorded_at=updated_at,
            producer="loopx.todo.complete",
            actor_agent_id=actor_agent_id,
        )
        if (
            not already_done
            or terminal_upgrade
            or untyped_completion_repair
            or unscoped_identity_repair
        ):
            pending_events.append(completion_event)
        if not dry_run:
            try:
                store.append_many(pending_events, expected_checksum=source_checksum)
            except StateEventSourceChangedError:
                return _completion_validation_source_drift_failure(
                    goal_id=goal_id, todo_id=todo_id, dry_run=dry_run
                )

        result = {
            "ok": True,
            "dry_run": dry_run,
            "completed": True,
            "goal_id": goal_id,
            "role": role,
            "section": TODO_SECTION_HEADINGS[role],
            "todo": item.get("text") or item.get("title"),
            "todo_id": todo_id,
            "status": TODO_STATUS_DONE,
            "status_changed": not already_done,
            "text_changed": False,
            "metadata_updated": (
                (not already_done)
                or terminal_upgrade
                or untyped_completion_repair
                or unscoped_identity_repair
            ),
            "changed": (
                (not already_done)
                or terminal_upgrade
                or untyped_completion_repair
                or unscoped_identity_repair
                or bool(next_results)
            ),
            "claimed_by": normalize_todo_claimed_by(effective_claimed_by),
            "task_class": item.get("task_class"),
            "action_kind": item.get("action_kind"),
            "capability_binding_ref": item.get("capability_binding_ref"),
            "continuation_policy": item.get("continuation_policy"),
            "successor_todo_ids": normalized_successor_todo_ids,
            "completion_continuation": completion_continuation,
            "completion_recovery": completion_recovery,
            "next_todos": next_results,
            "state_file": str(context.get("state_file") or ""),
            "project": str(context.get("project") or "") or None,
            "updated_at": updated_at,
            "source": "event_log",
        }
        result["self_merged"] = self_merged
        return result
