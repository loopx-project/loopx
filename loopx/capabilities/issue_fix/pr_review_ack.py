from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.todos.contract import (
    TODO_TASK_CLASS_USER_ACTION,
    normalize_todo_bound_agent,
)
from ...control_plane.todos.todo_semantics import todo_item_task_class
from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import (
    append_rollout_event_once,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)
from ...control_plane.todos.list_readback import list_goal_todos
from .metadata_preview import normalise_github_issue_reference


PR_REVIEW_BINDING_SCHEMA_VERSION = "issue_fix_pr_review_binding_v0"
PR_REVIEW_ACK_RECEIPT_SCHEMA_VERSION = "issue_fix_pr_review_ack_receipt_v0"
PR_REVIEW_ACK_EVENT_KIND = "pr_review_ack"
PR_REVIEW_ACK_LIMIT = 16


def build_issue_fix_pr_review_binding(
    *,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    todo_revision: str,
    url: str,
) -> dict[str, Any]:
    reference = normalise_github_issue_reference(url=url)
    if reference.get("kind") != "pull_request":
        raise ValueError("PR review binding requires a GitHub /pull/<number> URL")
    repo = str(reference.get("repo") or "").strip().lower()
    number = reference.get("number")
    permalink = str(reference.get("permalink") or "").strip()
    if not repo or not isinstance(number, int) or number <= 0 or not permalink:
        raise ValueError("PR review binding requires an exact GitHub repository and PR")
    return {
        "schema_version": PR_REVIEW_BINDING_SCHEMA_VERSION,
        "provider": "github",
        "goal_id": str(goal_id).strip(),
        "todo_id": str(todo_id).strip(),
        "agent_id": str(agent_id).strip(),
        "todo_revision": str(todo_revision).strip(),
        "repository": repo,
        "pr_number": number,
        "pr_ref": f"{repo}#{number}",
        "permalink": permalink,
    }


def _require_exact_review_todo(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    project: Path | None,
) -> dict[str, Any]:
    todo_payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        role="user",
        todo_id=todo_id,
        project=project,
    )
    todo = todo_payload.get("todo")
    if not isinstance(todo, dict):
        raise ValueError(f"user todo {todo_id!r} was not found")
    if todo_item_task_class(todo) != TODO_TASK_CLASS_USER_ACTION:
        raise ValueError("PR review acknowledgement requires task_class=user_action")
    bound_agent = normalize_todo_bound_agent(todo.get("bound_agent"))
    if bound_agent and bound_agent != agent_id:
        raise ValueError(
            "PR review acknowledgement agent_id must match the user_action bound_agent"
        )
    return todo


def issue_fix_pr_review_todo_revision(todo: Mapping[str, Any]) -> str:
    updated_at = str(todo.get("updated_at") or "").strip()
    if updated_at:
        return f"updated_at:{updated_at}"
    legacy_fields = {
        field: todo.get(field)
        for field in ("todo_id", "text", "status", "task_class", "bound_agent")
    }
    digest = hashlib.sha256(
        json.dumps(
            legacy_fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"legacy:{digest}"


def resolve_issue_fix_pr_review_context(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    project: Path | None,
    url: str,
    ack_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        todo = _require_exact_review_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            agent_id=agent_id,
            project=project,
        )
    except ValueError as exc:
        return {
            "status": "todo_invalid",
            "error": str(exc),
            "todo": None,
            "binding": None,
            "todo_revision": None,
            "ack_valid": False,
        }
    if bool(todo.get("done")) or str(todo.get("status") or "") == "done":
        return {
            "status": "todo_not_open",
            "todo": todo,
            "binding": None,
            "todo_revision": issue_fix_pr_review_todo_revision(todo),
            "ack_valid": False,
        }
    todo_revision = issue_fix_pr_review_todo_revision(todo)
    try:
        binding = build_issue_fix_pr_review_binding(
            goal_id=goal_id,
            todo_id=todo_id,
            agent_id=agent_id,
            todo_revision=todo_revision,
            url=url,
        )
    except ValueError as exc:
        return {
            "status": "provider_unsupported",
            "error": str(exc),
            "todo": todo,
            "binding": None,
            "todo_revision": todo_revision,
            "ack_valid": False,
        }
    if ack_receipt is None:
        return {
            "status": "current",
            "todo": todo,
            "binding": binding,
            "todo_revision": todo_revision,
            "ack_valid": False,
        }
    ack_valid, status = validate_issue_fix_pr_review_ack_receipt(
        ack_receipt,
        binding=binding,
    )
    return {
        "status": status,
        "todo": todo,
        "binding": binding,
        "todo_revision": todo_revision,
        "ack_valid": ack_valid,
    }


def _runtime_root(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> Path:
    if runtime_root_arg:
        return Path(runtime_root_arg).expanduser()
    return resolve_runtime_root(load_registry(registry_path.expanduser()), None)


def issue_fix_pr_review_ack_receipt_from_event(
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    if event.get("event_kind") != PR_REVIEW_ACK_EVENT_KIND:
        return None
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    binding = {
        "schema_version": details.get("binding_schema_version"),
        "provider": details.get("provider"),
        "goal_id": event.get("goal_id"),
        "todo_id": event.get("todo_id"),
        "agent_id": event.get("agent_id"),
        "todo_revision": details.get("todo_revision"),
        "repository": details.get("repository"),
        "pr_number": details.get("pr_number"),
        "pr_ref": (
            event.get("code_refs", {}).get("pr_ref")
            if isinstance(event.get("code_refs"), Mapping)
            else None
        ),
        "permalink": details.get("permalink"),
    }
    return {
        "schema_version": details.get("receipt_schema_version"),
        "receipt_id": event.get("event_id"),
        "acknowledged_at": event.get("recorded_at"),
        "owner_acknowledged": details.get("owner_acknowledged") is True,
        "binding": binding,
        "readback_verified": True,
    }


def validate_issue_fix_pr_review_ack_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    binding: Mapping[str, Any],
) -> tuple[bool, str]:
    if not isinstance(receipt, Mapping):
        return False, "ack_receipt_missing"
    if receipt.get("schema_version") != PR_REVIEW_ACK_RECEIPT_SCHEMA_VERSION:
        return False, "ack_receipt_schema_unsupported"
    if receipt.get("owner_acknowledged") is not True:
        return False, "owner_acknowledgement_required"
    if receipt.get("readback_verified") is not True:
        return False, "ack_receipt_readback_required"
    candidate = receipt.get("binding")
    if not isinstance(candidate, Mapping):
        return False, "ack_receipt_binding_missing"
    if candidate.get("todo_revision") != binding.get("todo_revision"):
        return False, "stale_ack_receipt"
    identity_fields = (
        "schema_version",
        "provider",
        "goal_id",
        "todo_id",
        "agent_id",
        "repository",
        "pr_number",
        "pr_ref",
        "permalink",
    )
    if any(candidate.get(field) != binding.get(field) for field in identity_fields):
        return False, "ack_receipt_binding_mismatch"
    return True, "matched"


def record_issue_fix_pr_review_ack(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    project: Path | None,
    url: str,
    owner_acknowledged: bool,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not owner_acknowledged:
        raise ValueError("pr-review-ack requires explicit owner acknowledgement")
    context = resolve_issue_fix_pr_review_context(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        agent_id=agent_id,
        project=project,
        url=url,
    )
    if context.get("status") != "current":
        raise ValueError(
            f"pr-review-ack requires a current open review todo: {context.get('status')}"
        )
    binding = context["binding"]
    runtime_root = _runtime_root(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    event = build_rollout_event(
        goal_id=goal_id,
        event_kind=PR_REVIEW_ACK_EVENT_KIND,
        agent_id=agent_id,
        todo_id=todo_id,
        run_id=f"{binding['pr_ref']}@{binding['todo_revision']}",
        pr_ref=str(binding["pr_ref"]),
        status="acknowledged",
        summary=(
            f"Owner acknowledged exact PR review reminder {todo_id} for "
            f"{binding['pr_ref']}."
        ),
        details={
            "receipt_schema_version": PR_REVIEW_ACK_RECEIPT_SCHEMA_VERSION,
            "binding_schema_version": PR_REVIEW_BINDING_SCHEMA_VERSION,
            "provider": binding["provider"],
            "repository": binding["repository"],
            "pr_number": binding["pr_number"],
            "permalink": binding["permalink"],
            "todo_revision": binding["todo_revision"],
            "owner_acknowledged": True,
        },
        recorded_at=generated_at,
    )
    recorded, appended = append_rollout_event_once(
        rollout_event_log_path(runtime_root, goal_id),
        event,
        identity_fields=("goal_id", "event_kind", "todo_id", "agent_id", "run_id"),
    )
    event_id = str(recorded.get("event_id") or "")
    readback_event = next(
        (
            candidate
            for candidate in reversed(
                load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
            )
            if candidate.get("event_id") == event_id
        ),
        None,
    )
    if readback_event is None:
        raise RuntimeError("PR review acknowledgement event readback failed")
    receipt = issue_fix_pr_review_ack_receipt_from_event(readback_event)
    if receipt is None:
        raise RuntimeError("PR review acknowledgement receipt readback failed")
    valid, reason = validate_issue_fix_pr_review_ack_receipt(
        receipt,
        binding=binding,
    )
    if not valid:
        raise RuntimeError(f"PR review acknowledgement receipt invalid: {reason}")
    return {
        "ok": True,
        "schema_version": PR_REVIEW_ACK_RECEIPT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "agent_id": agent_id,
        "binding": binding,
        "ack_receipt": receipt,
        "write_performed": appended,
        "replayed": not appended,
        "external_read_performed": False,
        "external_write_performed": False,
        "private_material_read": False,
    }


def load_issue_fix_pr_review_ack_receipts(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    seen_todos: set[str] = set()
    events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    for event in reversed(events):
        if (
            event.get("event_kind") != PR_REVIEW_ACK_EVENT_KIND
            or str(event.get("agent_id") or "") != agent_id
        ):
            continue
        todo_id = str(event.get("todo_id") or "")
        if not todo_id or todo_id in seen_todos:
            continue
        receipt = issue_fix_pr_review_ack_receipt_from_event(event)
        if receipt is None:
            continue
        seen_todos.add(todo_id)
        receipts.append(receipt)
        if len(receipts) >= PR_REVIEW_ACK_LIMIT:
            break
    return list(reversed(receipts))


def find_issue_fix_pr_review_ack_receipt(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    url: str,
) -> dict[str, Any] | None:
    context = resolve_issue_fix_pr_review_context(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        agent_id=agent_id,
        project=None,
        url=url,
    )
    binding = context.get("binding")
    if context.get("status") != "current" or not isinstance(binding, Mapping):
        return None
    runtime_root = _runtime_root(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    for receipt in reversed(
        load_issue_fix_pr_review_ack_receipts(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
        )
    ):
        valid, _reason = validate_issue_fix_pr_review_ack_receipt(
            receipt,
            binding=binding,
        )
        if valid:
            return receipt
    return None


__all__ = [
    "PR_REVIEW_ACK_EVENT_KIND",
    "PR_REVIEW_ACK_RECEIPT_SCHEMA_VERSION",
    "PR_REVIEW_BINDING_SCHEMA_VERSION",
    "build_issue_fix_pr_review_binding",
    "find_issue_fix_pr_review_ack_receipt",
    "issue_fix_pr_review_todo_revision",
    "issue_fix_pr_review_ack_receipt_from_event",
    "load_issue_fix_pr_review_ack_receipts",
    "record_issue_fix_pr_review_ack",
    "resolve_issue_fix_pr_review_context",
    "validate_issue_fix_pr_review_ack_receipt",
]
