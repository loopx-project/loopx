"""Maintainer-correction settlement and origin-request binding helpers."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import NotRequired, TypedDict

from ...agent_registry import load_goal_from_registry
from ...control_plane.goals.activation import goal_is_stopped
from ...control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ...control_plane.runtime.time import now_utc_iso
from ...control_plane.todos.mutation_api import add_goal_todo
from ...domain_packs.issue_fix import (
    issue_fix_maintainer_correction_entries,
    read_issue_fix_pr_lifecycle_ledger_row,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from .pr_lifecycle import recheck_issue_fix_maintainer_correction_effect_gates


# Set to "1" to abort between the accepted ledger entry write and its Todo
# write. Exists only to prove the next run reconciles the pending entry.
MAINTAINER_CORRECTION_FAULT_ENV = "LOOPX_ISSUE_FIX_FAULT_BEFORE_CORRECTION_TODO"
_SOURCE_EVENT_REFUSALS = {
    "source_event_not_observed",
    "source_event_revision_mismatch",
    "self_or_bot_author",
    "author_not_maintainer",
}


def _require_origin_registry(registry_path: Path) -> None:
    """Check the origin profile before looking up the request in its store."""
    try:
        profile = load_project_registry(registry_path).get("profile_id")
    except (ValueError, OSError) as exc:
        raise ValueError("origin registry is unreadable") from exc
    if profile != SOURCE_SESSION_PROFILE_ID:
        raise ValueError(f"origin registry requires {SOURCE_SESSION_PROFILE_ID}")


def _goal_stopped_for_correction(registry_path: Path | None, goal_id: str | None) -> bool:
    if registry_path is None or not goal_id:
        return False
    try:
        goal = load_goal_from_registry(registry_path, goal_id)
        if goal is None:
            return True
        return goal_is_stopped(goal)
    except ValueError:
        # An unreadable activation record cannot prove the Goal is active.
        return True


class _CorrectionTodoIntent(TypedDict, total=False):
    role: str
    text: str
    task_class: str
    action_kind: str
    required_write_scopes: list[str]


class _OriginRequestIdentity(TypedDict):
    goal_id: str
    agent_id: str
    goal_ref: NotRequired[dict[str, object]]


def _write_maintainer_correction_todo(
    *,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    operation_id: str,
    intent: _CorrectionTodoIntent,
) -> dict[str, object]:
    role = str(intent.get("role") or "agent")
    return add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role=role,
        text=str(intent.get("text") or ""),
        task_class=str(intent.get("task_class") or "advancement_task"),
        action_kind=str(intent.get("action_kind") or ""),
        required_write_scopes=(
            list(intent.get("required_write_scopes") or []) if role == "agent" else None
        ),
        claimed_by=claimed_by if role == "agent" else None,
        agent_id=claimed_by if role == "user" else None,
        project=project,
        operation_id=operation_id,
    )


def _maintainer_correction_todo_write_receipt(
    todo_write: Mapping[str, object], *, operation_id: str
) -> dict[str, object]:
    todo_changed = bool(
        todo_write.get("added")
        or todo_write.get("metadata_updated")
        or todo_write.get("status_changed")
    )
    created_todo = todo_write.get("todo")
    created_todo = created_todo if isinstance(created_todo, dict) else {}
    return {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": todo_changed,
        "already_exists": bool(todo_write.get("already_exists")),
        "todo_id": todo_write.get("todo_id"),
        "operation_id": operation_id,
        "role": todo_write.get("role"),
        "claimed_by": created_todo.get("claimed_by"),
        "blocks_agent": created_todo.get("blocks_agent"),
        "path_recorded": False,
    }


def _correction_todo_readback_receipt(
    *, skip_reason: str, operation_id: str | None = None, todo_id: str | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": False,
        "already_exists": bool(todo_id),
        "skip_reason": skip_reason,
        "path_recorded": False,
    }
    if operation_id is not None:
        receipt["operation_id"] = operation_id
    if todo_id is not None:
        receipt["todo_id"] = todo_id
    return receipt


def _resolve_origin_request_identity(runtime_root: Path, request_id: str) -> _OriginRequestIdentity:
    paths = list((runtime_root / ".local/manager-context/entries").glob(f"*/{request_id}.json"))
    if not paths:
        raise ValueError("origin_request_not_found")
    if len(paths) != 1:
        raise ValueError("origin_request_ambiguous")
    try:
        entry = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("origin_request_not_found") from exc
    if not isinstance(entry, dict):
        raise ValueError("origin_request_not_found")
    if isinstance(entry.get("recipient"), dict):
        raise ValueError("origin_request_unsupported_target")
    goal_id = entry.get("goal_id")
    agent_id = entry.get("agent_id")
    if not isinstance(goal_id, str) or not goal_id or not isinstance(agent_id, str) or not agent_id:
        raise ValueError("origin_request_unsupported_target")
    identity: _OriginRequestIdentity = {"goal_id": goal_id, "agent_id": agent_id}
    if isinstance(entry.get("goal_ref"), dict):
        identity["goal_ref"] = entry["goal_ref"]
    return identity


def _settle_maintainer_correction_entries(
    *,
    payload: dict[str, object],
    ledger_path: Path,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    source_events: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write Todos for accepted ledger entries that still lack a todo_id.

    Each accepted entry stores its own `todo_intent`, so an entry left pending
    by an interrupted run is retried under its original operation id on any
    later run. Every candidate is re-gated against the ledger as it stands
    now, the same observation, and the Goal activation state.
    """

    observation = payload.get("observation")
    observation = observation if isinstance(observation, dict) else {}
    admission = payload.get("maintainer_correction_admission")
    admission = admission if isinstance(admission, dict) else {}
    current_operation_id = str(admission.get("operation_id") or "")
    goal_stopped = _goal_stopped_for_correction(registry_path, goal_id)
    repo = str(observation.get("repo") or "")
    pr_ref = str(observation.get("pr_ref") or "")

    row = read_issue_fix_pr_lifecycle_ledger_row(ledger_path, repo=repo, pr_ref=pr_ref)
    ledger_entries = issue_fix_maintainer_correction_entries(row)
    if source_events is None:
        pending_source_events = payload.pop("_maintainer_correction_source_events", None)
        source_events = pending_source_events if isinstance(pending_source_events, dict) else None
    origin = (row or {}).get("origin_request")
    view = None
    origin_refusal = None
    if origin:
        from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
        from ...control_plane.collaboration.request_control_source import execute_request_control

        try:
            identity = _resolve_origin_request_identity(Path(origin["runtime_root"]), origin["request_id"])
            if any(identity[key] != origin[key] for key in ("goal_id", "agent_id")):
                raise ValueError("origin request identity changed")
            with collaboration_goal_scope(Path(origin["registry_path"]), goal_id=identity["goal_id"],
                    agents=(), require_active=False) as scope:
                if scope.profile_id != SOURCE_SESSION_PROFILE_ID:
                    raise ValueError("origin registry profile changed")
                goal_ref = identity.get("goal_ref") or scope.current_goal_ref or {"goal_id": identity["goal_id"]}
            if origin.get("goal_ref") and goal_ref != origin["goal_ref"]:
                raise ValueError("origin request identity changed")
            result = execute_request_control(registry_path=Path(origin["registry_path"]),
                runtime_root=Path(origin["runtime_root"]), goal_ref=goal_ref,
                receiver_agent_id=identity["agent_id"], request_id=origin["request_id"],
                actor_agent_id=identity["agent_id"], operation="inspect", execute=False)
            if not result.get("ok"):
                raise ValueError("request inspection failed")
            view = result["view"]
            if not view.get("control_state"):
                raise ValueError("request control state unavailable")
            if view["control_state"] in {"cancelled", "cancellation_pending"}:
                origin_refusal = "origin_request_cancelled"
        except (OSError, ValueError, KeyError):
            origin_refusal = "origin_request_unreadable"
    settlements: list[dict[str, object]] = []
    current_todo_write: dict[str, object] | None = None
    changed_entries: list[dict[str, object]] = []
    for entry in ledger_entries:
        if origin and entry.get("disposition") == "accepted" and "accepted_head_sha" not in entry:
            entry["accepted_head_sha"] = observation.get("head_commit_ref")
            changed_entries.append(dict(entry))
        operation_id = str(entry.get("operation_id") or "")
        is_current = operation_id == current_operation_id
        intent = entry.get("todo_intent")
        if (
            entry.get("disposition") != "accepted"
            or not isinstance(intent, dict)
            or (entry.get("todo_id") and not is_current)
        ):
            continue
        refusal = recheck_issue_fix_maintainer_correction_effect_gates(
            entry,
            observation=observation,
            ledger_entries=ledger_entries,
            goal_stopped=goal_stopped,
            source_events=source_events,
        )
        refusal = origin_refusal or refusal
        if refusal is not None and not entry.get("todo_id"):
            if refusal in _SOURCE_EVENT_REFUSALS:
                settlements.append(
                    {"operation_id": operation_id, "status": "refused", "reason": refusal}
                )
                if is_current:
                    current_todo_write = _correction_todo_readback_receipt(
                        skip_reason=refusal, operation_id=operation_id,
                    )
                continue
            if is_current and origin_refusal:
                admission.update(disposition="context_only", reason=refusal)
            changed_entries.append(
                {**entry, "disposition": "context_only", "reason": refusal,
                 "effect_recheck": "refused"}
            )
            settlements.append(
                {"operation_id": operation_id, "status": "refused", "reason": refusal}
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal, operation_id=operation_id,
                )
            continue
        if refusal is not None:
            # The Todo already exists for this revision; a replay only reads it
            # back and leaves its lifecycle to the Todo owner.
            settlements.append(
                {"operation_id": operation_id, "status": "settled_earlier", "reason": refusal}
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal, operation_id=operation_id,
                    todo_id=entry.get("todo_id"),
                )
            continue
        if not entry.get("todo_id") and os.environ.get(MAINTAINER_CORRECTION_FAULT_ENV) == "1":
            raise RuntimeError(
                "maintainer correction Todo write aborted by "
                f"{MAINTAINER_CORRECTION_FAULT_ENV}"
            )
        todo_write = _write_maintainer_correction_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            claimed_by=claimed_by,
            project=project,
            operation_id=operation_id,
            intent=intent,
        )
        receipt = _maintainer_correction_todo_write_receipt(
            todo_write, operation_id=operation_id
        )
        if entry.get("todo_id") != receipt["todo_id"]:
            changed_entries.append({**entry, "todo_id": receipt["todo_id"]})
        settlements.append(
            {
                "operation_id": operation_id,
                "status": "written" if receipt["write_performed"] else "already_exists",
                "todo_id": receipt["todo_id"],
            }
        )
        if is_current:
            current_todo_write = receipt
    if current_todo_write is None and current_operation_id:
        current_todo_write = _correction_todo_readback_receipt(
            skip_reason=(
                str(admission.get("reason") or admission.get("disposition") or "")
                if admission.get("disposition") != "accepted"
                else "unchanged_monitor_quiet"
            ),
            operation_id=current_operation_id,
        )
    if changed_entries:
        payload["maintainer_corrections"] = changed_entries
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        row = read_issue_fix_pr_lifecycle_ledger_row(
            ledger_path, repo=repo, pr_ref=pr_ref
        )
    entries = issue_fix_maintainer_correction_entries(row)
    payload["maintainer_corrections"] = entries
    if origin:
        from ...control_plane.todos.list_readback import list_goal_todos
        from ...control_plane.collaboration.inbox import reply_status
        from ..manager_context.roundtrip import report

        todos = {} if origin_refusal else {t["todo_id"]: t for t in list_goal_todos(
            registry_path=registry_path, goal_id=goal_id)["todos"]}
        head = str(observation.get("head_commit_ref") or "")
        for entry in entries:
            if (origin_refusal or not view or entry.get("disposition") != "accepted"
                    or not entry.get("todo_id")
                    or todos.get(entry["todo_id"], {}).get("status") != "done"):
                continue
            returns = entry.setdefault("returns", [])
            operation_id = entry["operation_id"]
            origin_root = Path(origin["runtime_root"])
            pending = entry.get("pending_return")
            pending_head = pending.get("head_sha") if (
                isinstance(pending, dict)
                and isinstance(pending.get("head_sha"), str)
                and isinstance(pending.get("text"), str)
                and isinstance(pending.get("request_revision"), int)
            ) else None
            # Finish the immutable publication before a newer observation can
            # replace it, and record the return against its original full head.
            for return_head in dict.fromkeys((pending_head, head)):
                if (not return_head or return_head == entry.get("accepted_head_sha")
                        or any(r["head_sha"] == return_head for r in returns)):
                    continue
                update_id = f"issue-fix-correction:{operation_id}:{return_head}"
                if return_head != pending_head:
                    event = entry.get("source_event") or {}
                    checks = observation.get("checks") or {}
                    text = (f"Maintainer correction {entry.get('correction_kind')} "
                        f"({event.get('kind')} {event.get('id')}) applied in "
                        f"{observation.get('permalink')} at head {return_head[:12]}; "
                        f"PR state {observation.get('state')}, checks {checks.get('aggregate')}; "
                        f"todo {entry['todo_id']} done.")
                    # Persist the entire publication before report(), including
                    # the first conclusion's absence of an update_id.
                    pending = {
                        "schema_version": "issue_fix_maintainer_correction_publication_v1",
                        "head_sha": return_head,
                        "update_id": update_id if any(
                            r["phase"] == "conclusion"
                            for r in reply_status(origin_root, origin)
                        ) else None,
                        "text": text,
                        "request_revision": view["revision"],
                        "phase": "conclusion",
                    }
                    entry["pending_return"] = pending
                    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
                result = report(origin_root, identity["goal_id"], identity["agent_id"],
                    origin["request_id"], str(pending.get("phase") or "conclusion"),
                    str(pending["text"]),
                    registry=Path(origin["registry_path"]),
                    update_id=pending.get("update_id"), workspace=project,
                    request_revision=pending["request_revision"],
                    caller_goal_ref=goal_ref)
                entry["pending_return"] = None
                returns.append({"update_id": update_id, "result_key": result.get("result_key", "conclusion"),
                    "enqueued_at": now_utc_iso(), "head_sha": return_head})
                upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        statuses = {r.get("result_key", r["phase"]): r["status"]
            for r in reply_status(Path(origin["runtime_root"]), origin)}
        payload["return_delivery"] = [{"operation_id": e["operation_id"],
            "update_id": r["update_id"], "status": statuses.get(r["result_key"], "explicit_unverified")}
            for e in entries for r in e.get("returns", [])]
    return {
        "schema_version": "issue_fix_maintainer_correction_settlement_v0",
        "settlements": settlements,
        "goal_stopped": goal_stopped,
        "todo_write": current_todo_write,
        "path_recorded": False,
    }
