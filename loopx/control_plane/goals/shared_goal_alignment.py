"""Read-only shared Goal alignment over the selected Todo/lease authority.

Promoted Goals carry a provider revision; legacy Markdown has no monotonic
revision. Neither source fabricates an event sequence or an Agent frontier.
The historical sequence fields remain zero/unbound for persisted proposal
compatibility; typed alignment and amendment admission decide what is provable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...history import load_registry
from ...registry import registry_goals
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..todos.contract import normalize_todo_claimed_by
from .shared_goal_work_source import SharedGoalWorkSource, read_shared_goal_work_source
from .active_state_metadata import parse_state_frontmatter
from .goal_frontier import (
    autonomous_replan_is_required,
    select_autonomous_replan_obligation,
)

SHARED_GOAL_ALIGNMENT_EFFECT_METHOD = "goal.shared_goal_alignment.project"
SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION = "shared_goal_alignment_request_v0"
SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION = "shared_goal_alignment_v0"
REVISION_BASIS_MARKDOWN_ACTIVE_STATE = "markdown_active_state"
BASIS_SOURCE_UNBOUND = "unbound"
DEFAULT_REGISTRY_RELATIVE_PATH = Path(".loopx") / "registry.json"


def _canonical_digest(value: object) -> str:
    # Local copy of the repository digest recipe: control_plane must not
    # import loopx.capabilities (m6 control_plane_outward_dependency).
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _registered_goal(
    registry_payload: Mapping[str, Any],
    *,
    goal_id: str,
) -> dict[str, Any]:
    for goal in registry_goals(dict(registry_payload)):
        if str(goal.get("id") or "") == goal_id:
            return goal
    raise ValueError(f"goal is not registered: {goal_id}")






def _source_basis_facts_envelope(
    *,
    goal_id: str,
    goal_status: str | None,
    registered_agents: list[str],
    revision_basis: str,
    last_append_sequence: int | None,
    source_checksum: str | None,
    state_updated_at: str | None,
    todo_basis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "goal_status": goal_status,
        "registered_agents": sorted(registered_agents),
        "revision_basis": revision_basis,
        "last_append_sequence": last_append_sequence,
        "source_checksum": source_checksum,
        "state_updated_at": state_updated_at,
        **({"todo_basis": todo_basis} if todo_basis is not None else {}),
    }


def project_shared_goal_alignment(
    *, goal_id: str, agent_id: str | None, project: Path,
    registry_path: Path | None = None, runtime_root: Path | None = None,
    status_item: Mapping[str, Any] | None = None,
    project_asset: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Public read-only entrypoint; callers cannot inject the Todo snapshot."""
    return _project_shared_goal_alignment(goal_id=goal_id, agent_id=agent_id,
        project=project, registry_path=registry_path, runtime_root=runtime_root,
        status_item=status_item, project_asset=project_asset)


def _project_shared_goal_alignment(
    *,
    goal_id: str,
    agent_id: str | None,
    project: Path,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
    status_item: Mapping[str, Any] | None = None,
    project_asset: Mapping[str, Any] | None = None,
    work_source: SharedGoalWorkSource | None = None,
) -> dict[str, Any]:
    """Project the read-only ``shared_goal_alignment_v0`` view for one Agent."""

    normalized_goal_id = str(goal_id or "").strip()
    if not normalized_goal_id:
        raise ValueError("goal_id must be a non-empty registered goal id")
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not normalized_agent_id:
        raise ValueError("agent_id must be a public-safe agent id")

    effective_registry_path = (
        registry_path if registry_path is not None
        else project / DEFAULT_REGISTRY_RELATIVE_PATH
    )
    try:
        registry_payload = load_registry(effective_registry_path)
    except (OSError, ValueError):
        raise ValueError(
            f"goal registry is unreadable: {effective_registry_path}"
        ) from None
    goal = _registered_goal(registry_payload, goal_id=normalized_goal_id)

    registered_agents = registered_agent_ids_for_goal(goal)
    if normalized_agent_id not in registered_agents:
        raise ValueError(
            f"agent is not registered for goal {normalized_goal_id}: "
            f"{normalized_agent_id}"
        )

    effective_runtime_root = runtime_root if runtime_root is not None else _runtime_root_from_registry(registry_payload)
    source = work_source or read_shared_goal_work_source(
        goal=goal, project=project, runtime_root=effective_runtime_root,
    )
    if source.goal_id != normalized_goal_id:
        raise ValueError("shared work snapshot belongs to another Goal")
    state_text = source.state_text

    frontmatter = parse_state_frontmatter(state_text)
    state_updated_at = str(frontmatter.get("updated_at") or "").strip() or None
    goal_status = str(goal.get("status") or "").strip() or (
        str(frontmatter.get("status") or "").strip() or None
    )

    revision_basis = ("canonical_todo_snapshot" if source.canonical_basis is not None
        else REVISION_BASIS_MARKDOWN_ACTIVE_STATE)
    basis_sequence = 0
    source_checksum = None

    source_basis_digest = _canonical_digest(
        _source_basis_facts_envelope(
            goal_id=normalized_goal_id,
            goal_status=goal_status,
            registered_agents=registered_agents,
            revision_basis=revision_basis,
            last_append_sequence=None,
            source_checksum=source_checksum,
            state_updated_at=state_updated_at,
            todo_basis=source.canonical_basis,
        )
    )
    source_basis = {
        "state_event_basis_sequence": basis_sequence,
        "source_basis_digest": source_basis_digest,
        "revision_basis": revision_basis,
        "state_updated_at": state_updated_at,
        **({"todo_basis": source.canonical_basis} if source.canonical_basis is not None else {}),
    }

    frontier_basis = {"based_on_state_event_sequence": None,
        "basis_source": BASIS_SOURCE_UNBOUND, "last_agent_event_id": None}

    replan_obligation = select_autonomous_replan_obligation(
        dict(status_item) if isinstance(status_item, Mapping) else {},
        dict(project_asset) if isinstance(project_asset, Mapping) else None,
        agent_id=normalized_agent_id,
    )

    request = {
        "schema_version": SHARED_GOAL_ALIGNMENT_REQUEST_SCHEMA_VERSION,
        "goal_id": normalized_goal_id,
        "agent_id": normalized_agent_id,
        "source_basis": source_basis,
        "frontier_basis": frontier_basis,
        "work_items": source.items,
        "observed_at": source.observed_at,
        "open_lane_replan_obligation_required": (
            autonomous_replan_is_required(replan_obligation)
        ),
    }
    try:
        result = effect_runtime_result(
            SHARED_GOAL_ALIGNMENT_EFFECT_METHOD,
            request,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or (
        result.get("schema_version") != SHARED_GOAL_ALIGNMENT_SCHEMA_VERSION
        or result.get("read_only") is not True
        or result.get("goal_id") != normalized_goal_id
        or result.get("agent_id") != normalized_agent_id
        or not isinstance(result.get("drift_facts"), list)
        or not isinstance(result.get("conflict_facts"), list)
    ):
        raise RuntimeError("TypeScript shared goal alignment shape mismatch")
    return dict(result)


def _runtime_root_from_registry(
    registry_payload: Mapping[str, Any],
) -> Path | None:
    raw = registry_payload.get("common_runtime_root")
    text = str(raw or "").strip()
    return Path(text).expanduser() if text else None
