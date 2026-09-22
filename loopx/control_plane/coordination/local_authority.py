"""Python adapter for provider-first local coordination reads.

TypeScript remains the semantic owner of canonical projection validation.  The
Python CLI only detects whether cutover is engaged, invokes that owner, and
fails closed instead of consulting the legacy Markdown projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...agent_registry import registered_agent_ids_from_registry
from .authority_source_capture import authority_registry_source
from ..runtime.time import now_local_iso as now_local
from ..effect_runtime import effect_runtime_result
from .coordination_state_contract import (
    TODO_CANONICAL_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_ITEM_SCHEMA_VERSION,
)
from .coordination_state_contract_generated import (
    LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
)
from .legacy_writer_fence import legacy_coordination_writer_fence_path


LOCAL_COORDINATION_TODO_LIST_METHOD = "coordination.local_authority.todo_list"
LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA = (
    "loopx_local_coordination_todo_claim_request_v1"
)
LOCAL_COORDINATION_TODO_CLAIM_METHOD = "coordination.local_authority.todo_claim"


LOCAL_AUTHORITY_SOURCES = ("file_v0", "sqlite_v0")


class LocalCoordinationAuthorityUnavailable(RuntimeError):
    """Canonical coordination state cannot safely answer a post-cutover read."""

    def __init__(self, message: str, *, code: str, payload: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.payload = dict(payload)


class LocalCoordinationAuthorityRejection(
    LocalCoordinationAuthorityUnavailable, ValueError
):
    """The TypeScript coordination owner definitively rejected a claim.

    The legacy Python kernel raised ``ValueError`` for every claim rejection
    (todo_not_open, claim_owner_mismatch, unregistered actor, ...).  After
    promotion those rejections surface as ``status="failed"`` results from the
    TypeScript transaction owner; re-raising them through this class keeps the
    legacy ``except ValueError`` contract intact for Python API callers while
    remaining catchable as an authority outage.  Infrastructure and protocol
    failures keep raising :class:`LocalCoordinationAuthorityUnavailable`, which
    is not a ``ValueError``.
    """

    def __init__(self, message: str, *, code: str, payload: Mapping[str, Any]) -> None:
        super().__init__(message, code=code, payload=payload)


def local_authority_is_promoted(*, runtime_root: Path, goal_id: str) -> bool:
    fence_path = legacy_coordination_writer_fence_path(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    try:
        fence_path.stat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LocalCoordinationAuthorityUnavailable(
            "local coordination authority mode cannot be inspected",
            code="local_authority_mode_read_failed",
            payload={"source_authority": "unknown_fail_closed"},
        ) from exc
    return True


def claim_canonical_todo_if_promoted(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    role: str | None,
    claimed_by: str,
    actor_agent_id: str | None,
    dry_run: bool,
    operation_id: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
) -> dict[str, Any] | None:
    """Route a post-cutover claim to the TypeScript transaction owner."""

    if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
        return None
    with authority_registry_source(registry_path) as registry_source:
        registered = registered_agent_ids_from_registry(registry_path, goal_id)
    result = effect_runtime_result(
        LOCAL_COORDINATION_TODO_CLAIM_METHOD,
        {
            "schema_version": LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
            "todo_id": todo_id,
            "role": role,
            "claimed_by": claimed_by,
            "actor_agent_id": actor_agent_id,
            "registered_agents": registered,
            "registry_source": registry_source,
            "operation_id": (
                operation_id
                if operation_id is not None
                else f"todo-claim:{goal_id}:{todo_id}:{uuid4().hex}"
            ),
            "lease_request": (
                {
                    "idempotency_key": task_lease_idempotency_key,
                    "expected_version": task_lease_expected_version,
                    "ttl_seconds": None,
                }
                if task_lease_idempotency_key is not None
                else None
            ),
            "observed_at": now_local(),
            "dry_run": dry_run,
        },
    )
    if not isinstance(result, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "local coordination authority returned an invalid Todo claim result",
            code="local_authority_todo_claim_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    payload = dict(result)
    accepted = {"applied", "recovered", "replayed", "no_change", "planned"}
    if (
        payload.get("status") == "failed"
        and payload.get("failure_kind") == "decision_rejection"
    ):
        # The TypeScript owner classifies this failure as a definitive claim
        # decision. The legacy kernel raised ValueError for the same
        # rejections, so keep that caller-observable contract; protocol and
        # storage-integrity failures stay infrastructure outages.
        raise LocalCoordinationAuthorityRejection(
            str(payload.get("reason") or "canonical Todo claim was rejected"),
            code=str(payload.get("reason_code") or "claim_rejected"),
            payload=payload,
        )
    if (
        payload.get("status") not in accepted
        or payload.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
        or payload.get("decision_read_from_provider") is not True
        or payload.get("legacy_fallback_used") is not False
    ):
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo claim failed"),
            code=str(payload.get("reason_code") or "local_authority_todo_claim_failed"),
            payload=payload,
        )
    from ..todos.provider_projection import settle_canonical_todo_projection

    return settle_canonical_todo_projection({
        "ok": True,
        "dry_run": dry_run,
        "goal_id": goal_id,
        "role": "agent",
        "section": "Agent Todo",
        "todo_id": todo_id,
        **payload,
    }, registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        project=project, state_file=state_file)


def read_canonical_todos_if_promoted(
    *, runtime_root: Path, goal_id: str, include_leases: bool = False,
    projection_readback: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return canonical Todos after cutover, or ``None`` before cutover.

    Presence of the durable writer fence is the mode switch. Once present,
    every malformed, missing, or unavailable provider response is terminal for
    this read; callers must never recover by reading Markdown.
    """

    if not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
        return None

    result = effect_runtime_result(
        LOCAL_COORDINATION_TODO_LIST_METHOD,
        {
            "schema_version": LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
            "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
            "goal_id": goal_id,
            **({"include_leases": True} if include_leases else {}),
            **({"projection_readback": dict(projection_readback)} if projection_readback is not None else {}),
        },
    )
    if not isinstance(result, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "local coordination authority returned an invalid Todo list",
            code="local_authority_todo_list_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    payload = dict(result)
    todos = payload.get("todos")
    todo_read_model = payload.get("todo_read_model")
    if (
        payload.get("status") != "loaded"
        or payload.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
        or payload.get("decision_read_from_provider") is not True
        or payload.get("legacy_fallback_used") is not False
        or not isinstance(todos, list)
        or any(not isinstance(item, Mapping) for item in todos)
        or not isinstance(todo_read_model, Mapping)
        or todo_read_model.get("schema_version")
        not in {
            TODO_CANONICAL_READ_RECORD_SCHEMA_VERSION,
            TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        }
        or todo_read_model.get("todo_count") != len(todos)
    ):
        raise LocalCoordinationAuthorityUnavailable(
            str(payload.get("reason") or "canonical Todo authority is unavailable"),
            code=str(
                payload.get("reason_code") or "local_authority_todo_list_unavailable"
            ),
            payload=payload,
        )
    payload["todos"] = [dict(item) for item in todos]
    if include_leases and (
        not isinstance(payload.get("leases"), list)
        or any(not isinstance(item, Mapping) for item in payload["leases"])
        or not isinstance(payload.get("provider_revision"), str)
    ):
        raise LocalCoordinationAuthorityUnavailable(
            "canonical Todo/lease snapshot is incomplete", code="local_authority_snapshot_incomplete",
            payload=payload,
        )
    if projection_readback is not None:
        confirmation = payload.get("projection_readback")
        if (not isinstance(confirmation, Mapping)
            or confirmation.get("provider_revision") != projection_readback["provider_revision"]
            or confirmation.get("observed_provider_revision") != payload.get("provider_revision")
            or confirmation.get("status") not in {"pending", "delivered", "current"}):
            raise LocalCoordinationAuthorityUnavailable(
                "canonical projection confirmation is missing or invalid",
                code="local_authority_projection_confirmation_invalid", payload=payload,
            )
    return payload


def read_canonical_todo_fields_if_promoted(
    *, runtime_root: Path, goal_id: str,
    rollout_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Read one unbounded planning snapshot; None alone permits legacy parsing.

    Empty canonical state is authoritative. Provider failures propagate; this
    read neither repairs Markdown nor grants mutation/promotion authority.
    Callers pass the same fields to all decisions in one planning operation.
    """
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    return (
        canonical_todo_summary_fields(canonical["todos"], rollout_events=rollout_events,
            goal_acceptance_contract=canonical.get("goal_acceptance_contract"),
            goal_acceptance_work_guards=canonical.get("goal_acceptance_work_guards"))
        if canonical is not None else None
    )


def canonical_todo_summary_fields(
    todos: list[dict[str, Any]],
    *,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    goal_acceptance_contract: dict[str, Any] | None = None,
    goal_acceptance_work_guards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt canonical records into the existing Todo summary read model."""

    from ..todos.active_state_editing import TODO_SECTION_HEADINGS
    from ..todos.standing_decision import build_standing_decision_authority
    from ..todos.todo_summary import compact_todo_group, count_advancement_todos

    # Read standing decisions before assigning presentation-only indexes, and
    # include retained history so archiving a revocation cannot revive approval.
    standing_authority = build_standing_decision_authority(
        [item for item in todos if item.get("role") == "user"], canonical_records=True,
    )
    archived_ids = {
        item["todo_id"]
        for item in todos
        if item.get("archive_state") == "archive"
    }
    todos = canonical_todo_items(todos)
    # These are native authority decisions, not persisted Todo fields. Keep the
    # records visible while every summary/selection uses the same work guard.
    if goal_acceptance_contract and goal_acceptance_contract.get("enabled") is True:
        guards = goal_acceptance_work_guards or {}
        todos = [{**item, "goal_acceptance_guard": guards[item["todo_id"]]}
            if item.get("todo_id") in guards else item for item in todos]
    fields: dict[str, Any] = {}
    for role in ("user", "agent"):
        items = [
            item
            for item in todos
            if ("user" if item.get("role") == "user" else "agent") == role
            and item.get("todo_id") not in archived_ids
        ]
        summary = compact_todo_group(
            items,
            source_section=TODO_SECTION_HEADINGS[role],
            role=role,
            include_empty_source=True,
            resume_source_items=todos,
            rollout_events=rollout_events,
            available_capabilities=available_capabilities,
            item_limit=None,
        )
        if summary:
            if role == "agent":
                if goal_acceptance_contract and goal_acceptance_contract.get("enabled") is True:
                    summary["goal_acceptance_contract"] = goal_acceptance_contract
                archived_done = count_advancement_todos(
                    [
                        item
                        for item in todos
                        if item.get("todo_id") in archived_ids
                        and item.get("done") is True
                    ]
                )
                if archived_done:
                    summary["archived_advancement_done_count"] = archived_done
                    summary["advancement_done_count"] = (
                        int(summary.get("advancement_done_count") or 0) + archived_done
                    )
            fields[f"{role}_todos"] = summary
        if role == "user":
            if standing_authority:
                fields["standing_decision_authority"] = standing_authority
    return fields


def canonical_todo_items(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt every canonical Todo, including retained archive history."""

    from ..todos.active_state_editing import TODO_SECTION_HEADINGS

    # Native provider records have no Markdown address. Allocate display
    # positions from stable provider order; never read legacy Markdown here.
    return [
        {
            **item,
            **(
                {"schema_version": TODO_ITEM_SCHEMA_VERSION}
                if item.get("schema_version") == TODO_DOMAIN_ITEM_SCHEMA_VERSION
                else {}
            ),
            "source_section": "Completed Work Archive",
            "index": index,
        }
        if item.get("archive_state") == "archive"
        else (
            {
                **item,
                "schema_version": TODO_ITEM_SCHEMA_VERSION,
                "source_section": TODO_SECTION_HEADINGS[item["role"]],
                "index": index,
            }
            if item.get("schema_version") == TODO_DOMAIN_ITEM_SCHEMA_VERSION
            else item
        )
        for index, item in enumerate(todos, 1)
    ]
