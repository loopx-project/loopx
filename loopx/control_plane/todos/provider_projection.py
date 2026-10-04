"""Recoverable Markdown delivery for provider-owned Todo state.

The canonical authority journal is the transaction-bound projection outbox:
each committed mutation already retains the complete canonical head and its
provider revision.  This adapter drains that durable intent into the legacy
Markdown compatibility view.  Delivery failure never rolls back or obscures
the canonical commit; a later mutation or explicit projection can replay the
current head idempotently.
"""

from __future__ import annotations

from enum import StrEnum
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_cross_runtime_file_lock
from ...history import load_registry
from ..goals.state_resolution import resolve_goal_state
from ..coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
    local_authority_is_promoted,
)
from .machine_section_projection import (
    TodoSectionProjectionError,
    render_canonical_todo_sections,
)
from .completion_validation_store import load_completion_validation_declarations
from .active_state_editing import atomic_write_state_text, verify_state_text_durable
from .projection_document import recovered_todo_projection_skeleton


TODO_PROJECTION_DELIVERY_SCHEMA = "loopx_todo_projection_delivery_v0"


class ProjectionDeliveryStatus(StrEnum):
    """Stable cross-language states for canonical Todo display delivery."""
    PENDING = "pending"
    DELIVERED = "delivered"
    CURRENT = "current"
    NOT_REQUIRED = "not_required"


def parse_projection_delivery(value: object) -> ProjectionDeliveryStatus:
    try:
        return ProjectionDeliveryStatus(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported projection_delivery: {value!r}") from error


def projection_delivery_requires_ack(value: object) -> bool:
    return parse_projection_delivery(value) in {
        ProjectionDeliveryStatus.DELIVERED,
        ProjectionDeliveryStatus.CURRENT,
    }


def projection_delivery_for_mutation(changed: bool) -> ProjectionDeliveryStatus:
    """Map a committed mutation to its display outbox state."""
    return (
        ProjectionDeliveryStatus.PENDING
        if changed
        else ProjectionDeliveryStatus.NOT_REQUIRED
    )


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def project_current_canonical_todos(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    expected_provider_revision: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    execute: bool = True,
    registry_data: Mapping[str, Any] | None = None,
    canonical_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render one exact canonical head into machine-owned Markdown regions."""

    registry = dict(registry_data) if registry_data is not None else load_registry(registry_path)
    goal, resolved_project, state_path = resolve_goal_state(
        registry=registry,
        goal_id=goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    if goal is None:
        raise ValueError(f"goal {goal_id!r} is not present in the registry")
    with exclusive_cross_runtime_file_lock(
        state_path, operation="project_canonical_todo_sections"
    ):
        # The refresh planner can supply its complete authoritative snapshot.
        # It may age while refresh commits: durable confirmation below must still
        # observe the provider and can never be replaced by this earlier read.
        authority_read = canonical_snapshot if canonical_snapshot is not None else read_canonical_todos_if_promoted(
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
        if not isinstance(authority_read, dict):
            raise ValueError(
                "Todo Markdown projection requires promoted canonical authority"
            )
        recovered_missing = False
        changed = False
        confirmation: dict[str, Any] | None = None
        attempt = 0
        while True:
            attempt += 1
            provider_revision = authority_read.get("provider_revision")
            if not isinstance(provider_revision, str) or not provider_revision:
                raise ValueError("canonical Todo authority omitted provider revision")
            if (
                expected_provider_revision is not None
                and provider_revision != expected_provider_revision
            ):
                raise ValueError(
                    "Todo Markdown projection provider revision does not match the "
                    "canonical read head"
                )
            missing_this_attempt = False
            try:
                source = _read_text_exact(state_path)
            except FileNotFoundError:
                recovered_missing = True
                missing_this_attempt = True
                source = recovered_todo_projection_skeleton(goal_id)
            projection = render_canonical_todo_sections(
                source,
                authority_read["todos"],
                provider_revision=provider_revision,
                private_validation_declarations=load_completion_validation_declarations(
                    runtime_root=runtime_root,
                    goal_id=goal_id,
                    todos=authority_read["todos"],
                ),
            )
            if execute and projection.changed:
                # The projection is a primary-state write: it must respect the
                # same source-ownership and shadow-management fences as every
                # other Markdown writer, checked under the state lock it holds.
                from ..coordination.legacy_writer_fence import (
                    require_registry_source_write_allowed,
                )

                require_registry_source_write_allowed(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=goal_id,
                    state_file=state_path,
                )
                if missing_this_attempt:
                    atomic_write_state_text(state_path, projection.markdown, create_only=True)
                else:
                    atomic_write_state_text(state_path, projection.markdown)
                if _read_text_exact(state_path) != projection.markdown:
                    raise RuntimeError("Todo Markdown projection readback mismatch")
            elif execute:
                verify_state_text_durable(state_path, projection.markdown)
            changed = changed or projection.changed
            if not execute:
                break
            confirmed = read_canonical_todos_if_promoted(
                runtime_root=runtime_root, goal_id=goal_id,
                projection_readback={"provider_revision": provider_revision, "changed": changed,
                                     "attempt": attempt,
                                     "target": "pinned" if expected_provider_revision is not None else "latest"},
            )
            if not isinstance(confirmed, dict) or not isinstance(confirmed.get("projection_readback"), dict):
                raise ValueError("canonical projection confirmation is missing")
            confirmation = confirmed["projection_readback"]
            if confirmation["next_action"] == "finish":
                break
            # TS owns the retry bound and exact-revision intent. Reuse the
            # complete confirmation snapshot instead of issuing another read.
            authority_read = confirmed

    return {
        "schema_version": TODO_PROJECTION_DELIVERY_SCHEMA,
        **(confirmation if confirmation is not None else {"status": "planned"}),
        "delivery_attempts": attempt,
        "source": "committed_authority_journal",
        "goal_id": goal_id,
        "state_file": str(state_path),
        "project": str(resolved_project) if resolved_project else None,
        "source_authority": authority_read.get("source_authority"),
        "provider_revision": projection.provider_revision,
        "todo_count": projection.todo_count,
        "changed": changed,
        "executed": execute,
        "source_sha256": projection.source_sha256,
        "rendered_sha256": projection.rendered_sha256,
        "narrative_sha256": projection.narrative_sha256,
        "section_record_sha256": projection.section_record_sha256,
        "parse_render_parity": True,
        "narrative_preserved": not recovered_missing,
        **({"recovery_scope": "todo_sections_only"} if recovered_missing else {}),
        "legacy_fallback_used": False,
    }


def settle_canonical_todo_projection(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
    only_if_promoted: bool = False,
    canonical_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drain the committed provider head, preserving a successful mutation."""

    if payload.get("dry_run") is True or payload.get("status") == "planned":
        payload["projection_delivery"] = projection_delivery_for_mutation(False).value
        payload["projection_outbox"] = {
            "schema_version": TODO_PROJECTION_DELIVERY_SCHEMA,
            "status": ProjectionDeliveryStatus.NOT_REQUIRED.value,
            "source": "committed_authority_journal",
        }
        return payload
    trigger_revision = payload.get("provider_revision")
    trigger_cursor = payload.get("cursor")
    try:
        if only_if_promoted and not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id):
            return payload
        delivery = project_current_canonical_todos(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            project=project,
            state_file=state_file,
            execute=True,
            canonical_snapshot=canonical_snapshot,
        )
    except Exception as error:  # noqa: BLE001 - canonical commit already landed
        if isinstance(error, LocalCoordinationAuthorityUnavailable):
            reason_code = "todo_projection_authority_unavailable"
        elif isinstance(error, TodoSectionProjectionError):
            reason_code = "todo_projection_render_rejected"
        elif isinstance(error, OSError):
            reason_code = "todo_projection_write_unavailable"
        else:
            reason_code = "todo_projection_target_unavailable"
        delivery = {
            "schema_version": TODO_PROJECTION_DELIVERY_SCHEMA,
            "status": "pending",
            "source": "committed_authority_journal",
            "reason_code": reason_code,
            "error_class": error.__class__.__name__,
            "retryable": True,
            "recommended_action": (
                "Repair the display or provider error, read the current provider revision "
                "with todo list, then retry todo project-markdown for that revision."
            ),
            "retry_business_mutation": False,
        }
    if isinstance(trigger_revision, str) and trigger_revision:
        delivery["trigger_provider_revision"] = trigger_revision
    if isinstance(trigger_cursor, str) and trigger_cursor:
        delivery["trigger_cursor"] = trigger_cursor
    payload["projection_delivery"] = parse_projection_delivery(delivery["status"]).value
    payload["projection_outbox"] = delivery
    return payload


def recover_refresh_todo_projection(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, project: Path | None = None, state_file: Path | None = None,
    canonical_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A committed refresh/replay is a recovery opportunity, never a new Todo write.

    Keep legacy, rejected and preview calls unchanged. The journal already owns
    durable projection intent; there is no additional queue, receipt or quota
    event here. A display failure stays pending alongside the committed refresh.
    """
    if payload.get("dry_run") is True or not (
        payload.get("ok") is True or payload.get("appended") is True
    ):
        return payload
    return settle_canonical_todo_projection(
        payload, registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, project=project, state_file=state_file, only_if_promoted=True,
        canonical_snapshot=canonical_snapshot,
    )


__all__ = [
    "TODO_PROJECTION_DELIVERY_SCHEMA",
    "ProjectionDeliveryStatus",
    "parse_projection_delivery",
    "projection_delivery_requires_ack",
    "projection_delivery_for_mutation",
    "project_current_canonical_todos",
    "settle_canonical_todo_projection",
    "recover_refresh_todo_projection",
]
