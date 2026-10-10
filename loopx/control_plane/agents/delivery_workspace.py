"""Python facade for the TypeScript-owned delivery workspace contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..coordination.coordination_state_contract_generated import (
    DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA as DELIVERY_WORKSPACE_REQUEST_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA as DELIVERY_WORKSPACE_RESULT_SCHEMA,
    DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA as DELIVERY_WORKSPACE_SCHEMA_VERSION,
)
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

LEGACY_DELIVERY_WORKSPACE_SCHEMA_VERSION = (
    DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA
)
DELIVERY_WORKSPACE_IDENTITY_KINDS = frozenset({"git_repository", "local_goal"})
DELIVERY_WORKSPACE_KINDS = frozenset(
    {"canonical_checkout", "independent_git_worktree", "local_goal_workspace"}
)


def _runtime_result(operation: str, **params: Any) -> Mapping[str, Any]:
    try:
        result = effect_runtime_result(
            "agent.delivery_workspace.evaluate",
            {
                "schema_version": DELIVERY_WORKSPACE_REQUEST_SCHEMA,
                "operation": operation,
                **params,
            },
        )
    except EffectRuntimeRejected as exc:
        raise RuntimeError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript delivery workspace result must be an object")
    if result.get("schema_version") != DELIVERY_WORKSPACE_RESULT_SCHEMA:
        raise RuntimeError("TypeScript delivery workspace result shape mismatch")
    return result


def _workspace_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    value = result.get("workspace")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeError("TypeScript delivery workspace result shape mismatch")
    expected_keys = {
        "schema_version",
        "workspace_identity",
        "identity_kind",
        "task_repository",
        "repository_source",
        "workspace_kind",
        "peer_independent_worktree_required",
    }
    actual_keys = set(value)
    if actual_keys not in (
        expected_keys,
        expected_keys | {"workspace_revision_digest"},
    ):
        raise RuntimeError("TypeScript delivery workspace result shape mismatch")
    if (
        value.get("schema_version") != DELIVERY_WORKSPACE_SCHEMA_VERSION
        or not isinstance(value.get("workspace_identity"), str)
        or value.get("identity_kind") not in DELIVERY_WORKSPACE_IDENTITY_KINDS
        or not isinstance(value.get("repository_source"), str)
        or value.get("workspace_kind") not in DELIVERY_WORKSPACE_KINDS
        or not isinstance(value.get("peer_independent_worktree_required"), bool)
        or (
            value.get("workspace_revision_digest") is not None
            and not isinstance(value.get("workspace_revision_digest"), str)
        )
        or (
            value.get("task_repository") is not None
            and not isinstance(value.get("task_repository"), str)
        )
    ):
        raise RuntimeError("TypeScript delivery workspace result shape mismatch")
    return dict(value)


def build_delivery_workspace_snapshot(
    *,
    workspace_identity: str,
    identity_kind: str,
    repository_source: str,
    workspace_kind: str,
    peer_independent_worktree_required: bool,
    workspace_revision_digest: str | None = None,
) -> dict[str, Any] | None:
    return _workspace_result(
        _runtime_result(
            "build",
            observation={
                "workspace_identity": workspace_identity,
                "identity_kind": identity_kind,
                "workspace_revision_digest": workspace_revision_digest,
                "repository_source": repository_source,
                "workspace_kind": workspace_kind,
                "peer_independent_worktree_required": bool(
                    peer_independent_worktree_required
                ),
            },
        )
    )


def normalize_delivery_workspace_snapshot(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    prepared = {
        "schema_version": value.get("schema_version"),
        "workspace_identity": value.get("workspace_identity"),
        "identity_kind": value.get("identity_kind"),
        "task_repository": value.get("task_repository"),
        "workspace_revision_digest": value.get("workspace_revision_digest"),
        "repository_source": value.get("repository_source"),
        "workspace_kind": value.get("workspace_kind"),
        "peer_independent_worktree_required": value.get(
            "peer_independent_worktree_required"
        ),
    }
    return _workspace_result(_runtime_result("normalize", workspace=prepared))


def qualify_delivery_workspace_isolation(
    workspace: dict[str, Any] | None,
    *,
    multi_agent_goal: bool,
    explicit_peer_worktree_requirement: bool | None,
    task_repository: str | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Delegate Git/local isolation policy to the existing typed owner."""
    result = _runtime_result(
        "isolation", workspace=workspace, multi_agent_goal=multi_agent_goal,
        explicit_peer_worktree_requirement=explicit_peer_worktree_requirement,
        task_repository=task_repository,
    )
    required = result.get("peer_independent_worktree_required")
    if not isinstance(required, bool):
        raise RuntimeError("TypeScript delivery workspace isolation result shape mismatch")
    return _workspace_result(result), required
