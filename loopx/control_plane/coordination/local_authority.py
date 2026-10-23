"""Python adapter for provider-first local coordination reads.

TypeScript remains the semantic owner of canonical projection validation.  The
Python CLI invokes that owner for admission and snapshot validation, and fails
closed instead of consulting the legacy Markdown projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from ...agent_registry import registered_agent_ids_from_registry
from .authority_source_capture import authority_registry_source
from ..runtime.time import now_local_iso as now_local
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from .coordination_state_contract import (
    TODO_CANONICAL_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_ITEM_SCHEMA_VERSION,
    TODO_ITEM_SCHEMA_VERSION,
)
from .canonical_snapshot import read_canonical_snapshot


LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS = 15.0
LOCAL_COORDINATION_TODO_CLAIM_WITNESSED_REQUEST_SCHEMA = (
    "loopx_local_coordination_todo_claim_request_v1"
)
LOCAL_COORDINATION_TODO_CLAIM_METHOD = "coordination.local_authority.todo_claim"


LOCAL_AUTHORITY_SOURCES = ("file_v0", "sqlite_v0", "postgresql_v0")


class LocalCoordinationAuthorityUnavailable(RuntimeError):
    """Canonical coordination state cannot safely answer an ordinary read."""

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


class CanonicalAuthorityRequired(LocalCoordinationAuthorityRejection):
    """An ordinary operation targeted a Goal before authority migration."""

    def __init__(
        self,
        *,
        goal_id: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        evidence = {
            "goal_id": goal_id,
            "operation": operation,
            "write_admitted": False,
            "decision_read_from_provider": False,
            "legacy_fallback_used": False,
            "recovery": {
                "action": "migrate_goal_authority",
                "backup_required": True,
            },
            **dict(payload or {}),
        }
        super().__init__(
            "Canonical authority is required; back up and migrate this Goal before retrying",
            code="canonical_authority_required",
            payload=evidence,
        )


def raise_canonical_write_failure(
    payload: Mapping[str, Any],
    *,
    goal_id: str,
    operation: str,
) -> None:
    """Map native route admission once without re-reading promotion state."""

    if (
        payload.get("reason_code") == "canonical_authority_required"
        or payload.get("error_code") == "canonical_authority_required"
    ):
        raise CanonicalAuthorityRequired(
            goal_id=goal_id,
            operation=operation,
            payload=payload,
        )


def require_canonical_authority(*, goal_id: str, operation: str) -> NoReturn:
    """Fail a compatibility-only path that has no native request to dispatch."""

    raise CanonicalAuthorityRequired(goal_id=goal_id, operation=operation)


def local_authority_is_promoted(*, runtime_root: Path, goal_id: str) -> bool:
    from .legacy_writer_fence import legacy_coordination_writer_fence_path

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
) -> dict[str, Any]:
    """Route a claim to the canonical TypeScript transaction owner."""
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
        timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    )
    if not isinstance(result, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "local coordination authority returned an invalid Todo claim result",
            code="local_authority_todo_claim_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    payload = dict(result)
    raise_canonical_write_failure(
        payload,
        goal_id=goal_id,
        operation="todo_claim",
    )
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
    include_summary: bool = False, rollout_events=None, available_capabilities=None,
) -> dict[str, Any]:
    """Read canonical Todos or fail with the native migration/recovery cause.

    The historical import name no longer permits pre-cutover Markdown reads.
    Explicit migration captures use the dedicated source inspection owner.
    """

    projected = None
    if include_summary:
        from ..todos.goal_todo_projection import project_todo_read
        from ..runtime.time import now_utc

        now = now_utc()
        projected = project_todo_read({"kind": "canonical_fields",
            "source": {"runtime_root": str(runtime_root.expanduser().resolve(strict=False)), "goal_id": goal_id,
                "include_leases": include_leases, "projection_readback": dict(projection_readback) if projection_readback is not None else None},
            "rollout_events": list(rollout_events or []),
            "rollout_event_source": getattr(rollout_events, "source", None),
            "available_capabilities": sorted({str(value).strip().lower() for value in available_capabilities if str(value).strip()}) if available_capabilities is not None else None,
            "observed_at": now.timestamp(), "evaluated_at": now.isoformat()})
        result = projected["canonical"]
    else:
        result = read_canonical_snapshot(
            rpc=effect_runtime_result,
            runtime_root=str(runtime_root.expanduser().resolve(strict=False)),
            goal_id=goal_id, include_leases=include_leases,
            projection_readback=projection_readback,
            timeout=LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS,
        )
    if not isinstance(result, Mapping):
        raise LocalCoordinationAuthorityUnavailable(
            "local coordination authority returned an invalid Todo list",
            code="local_authority_todo_list_invalid_result",
            payload={"source_authority": "file_v0"},
        )
    payload = dict(result)
    if payload.get("reason_code") == "canonical_authority_required":
        raise CanonicalAuthorityRequired(
            goal_id=goal_id, operation="todo_read", payload=payload,
        )
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
            or confirmation.get("status") not in {"pending", "delivered", "current"}
            or confirmation.get("next_action") not in {"retry", "finish"}
            or (confirmation.get("next_action") == "retry" and confirmation.get("status") != "pending")):
            raise LocalCoordinationAuthorityUnavailable(
                "canonical projection confirmation is missing or invalid",
                code="local_authority_projection_confirmation_invalid", payload=payload,
            )
    if projected is not None and "fields" in projected:
        from ..todos.goal_todo_projection import restore_todo_field_evaluations
        payload["_todo_fields"] = restore_todo_field_evaluations(projected)
    return payload


class CanonicalTodoSnapshot:
    """One read-only request's complete Todo inputs, isolated from its consumers.

    Only identical no-lease, no-writeback reads participate. This is not a TTL
    cache or a transaction spanning Goals, registry, Markdown or rollout events.
    The first validated read (or authority failure) is retained per runtime/Goal;
    the next request must construct a new instance. Mutation/admission callers
    keep using fresh reads and the provider's transaction checks.
    """

    def __init__(self) -> None:
        self._reads: dict[
            tuple[Path, str], dict[str, Any] | LocalCoordinationAuthorityUnavailable
        ] = {}

    def read(self, *, runtime_root: Path, goal_id: str) -> dict[str, Any]:
        key = (runtime_root.expanduser().resolve(strict=False), goal_id)
        if key not in self._reads:
            try:
                from ..todos.resume_condition import TodoResumeRolloutEvents
                self._reads[key] = read_canonical_todos_if_promoted(
                    runtime_root=key[0], goal_id=goal_id, include_summary=True,
                    rollout_events=TodoResumeRolloutEvents([], runtime_root=key[0], goal_id=goal_id),
                )
            except LocalCoordinationAuthorityUnavailable as error:
                # A failed first read cannot become a successful attention view
                # later in the same request, contradicting contract diagnostics.
                self._reads[key] = LocalCoordinationAuthorityUnavailable(
                    str(error), code=error.code, payload=deepcopy(error.payload),
                )
        result = self._reads[key]
        if isinstance(result, LocalCoordinationAuthorityUnavailable):
            raise LocalCoordinationAuthorityUnavailable(
                str(result), code=result.code, payload=deepcopy(result.payload),
            )
        # Summary enrichment mutates nested records. Never share those mutations
        # with contract checks, another role, or the retained snapshot itself.
        return deepcopy(result)


def read_canonical_todo_fields_if_promoted(
    *, runtime_root: Path, goal_id: str,
    rollout_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read one complete canonical planning snapshot, including empty state.

    Empty canonical state is authoritative. Provider failures propagate; this
    read neither repairs Markdown nor grants mutation/promotion authority.
    Callers pass the same fields to all decisions in one planning operation.
    """
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id,
        include_summary=True, rollout_events=rollout_events)
    return canonical["_todo_fields"]



def canonical_todo_summary_fields(
    todos: list[dict[str, Any]],
    *,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    goal_acceptance_contract: dict[str, Any] | None = None,
    goal_acceptance_work_guards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..todos.goal_todo_projection import canonical_todo_fields

    return canonical_todo_fields(todos, rollout_events=rollout_events,
        available_capabilities=available_capabilities,
        goal_acceptance_contract=goal_acceptance_contract,
        goal_acceptance_work_guards=goal_acceptance_work_guards)


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
